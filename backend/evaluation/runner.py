from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.shared.languages import SOURCE_LANGUAGES, language_for_path


DATASET_DIRECTORY = Path(__file__).resolve().parent / "datasets"
DEFAULT_TIMEOUT_SECONDS = 1800
DEFAULT_POLL_INTERVAL_SECONDS = 2
TARGETS = {"validation", "analysis", "repair", "workflow", "security", "bug_resource"}
DEFAULT_WORKFLOW_CHECKS = {
    "code_quality": True,
    "security": True,
    "bug_resource": True,
    "complexity": True,
    "automated_repair": True,
    "final_validation": True,
    "ai_report_recommendations": True,
}
SUCCESS_STATUSES = {"completed", "passed", "success"}
SKIPPED_STATUSES = {"skipped", "not_run", "disabled", "not_applicable"}


class EvaluationDataError(ValueError):
    """A dataset file or test case does not follow the evaluation contract."""


class EvaluationRequestError(RuntimeError):
    """An evaluation target could not return a usable HTTP response."""


RequestJson = Callable[[str, dict[str, Any] | None, float], dict[str, Any]]


def load_cases(paths: Iterable[Path]) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for path in paths:
        try:
            with path.open(encoding="utf-8") as dataset:
                for line_number, line in enumerate(dataset, start=1):
                    if not line.strip():
                        continue
                    try:
                        case = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise EvaluationDataError(
                            f"{path}:{line_number}: invalid JSON: {exc.msg}"
                        ) from exc
                    _validate_case(case, path, line_number)
                    if case["id"] in seen_ids:
                        raise EvaluationDataError(
                            f"{path}:{line_number}: duplicate case id {case['id']!r}"
                        )
                    seen_ids.add(case["id"])
                    cases.append(case)
        except OSError as exc:
            raise EvaluationDataError(f"Cannot read dataset {path}: {exc}") from exc
    if not cases:
        raise EvaluationDataError("No test cases were loaded from the selected datasets.")
    return cases


def _validate_case(case: Any, path: Path, line_number: int) -> None:
    location = f"{path}:{line_number}"
    if not isinstance(case, dict):
        raise EvaluationDataError(f"{location}: each JSONL record must be an object")
    for field in ("id", "language", "target", "code", "expected"):
        if field not in case:
            raise EvaluationDataError(f"{location}: missing required field {field!r}")
    if not isinstance(case["id"], str) or not case["id"].strip():
        raise EvaluationDataError(f"{location}: 'id' must be a non-empty string")
    if not isinstance(case["language"], str) or case["language"] not in SOURCE_LANGUAGES:
        raise EvaluationDataError(
            f"{location}: unsupported language {case['language']!r}"
        )
    if not isinstance(case["target"], str) or case["target"] not in TARGETS:
        raise EvaluationDataError(
            f"{location}: target must be one of {', '.join(sorted(TARGETS))}"
        )
    if not isinstance(case["code"], str):
        raise EvaluationDataError(f"{location}: 'code' must be a string")
    if not isinstance(case["expected"], dict):
        raise EvaluationDataError(f"{location}: 'expected' must be an object")
    expected = case["expected"]
    for field in ("finding_codes", "resolved_finding_ids", "required_agents"):
        if field in expected and (
            not isinstance(expected[field], list)
            or any(not isinstance(value, str) for value in expected[field])
        ):
            raise EvaluationDataError(
                f"{location}: expected.{field} must be a list of strings"
            )
    if "expected_findings" in expected:
        expected_findings = expected["expected_findings"]
        if not isinstance(expected_findings, list) or any(
            not isinstance(finding, dict)
            or not isinstance(finding.get("rule_id"), str)
            or not finding["rule_id"].strip()
            or (
                "severity" in finding
                and (
                    not isinstance(finding["severity"], str)
                    or not finding["severity"].strip()
                )
            )
            for finding in expected_findings
        ):
            raise EvaluationDataError(
                f"{location}: expected.expected_findings must be a list of objects with rule_id and optional severity"
            )
    for field in ("minimum_findings", "maximum_findings"):
        if field in expected and (
            type(expected[field]) is not int or expected[field] < 0
        ):
            raise EvaluationDataError(
                f"{location}: expected.{field} must be a non-negative integer"
            )
    if (
        "minimum_findings" in expected
        and "maximum_findings" in expected
        and expected["minimum_findings"] > expected["maximum_findings"]
    ):
        raise EvaluationDataError(
            f"{location}: expected.minimum_findings cannot exceed maximum_findings"
        )
    filename = case.get("filename", "main.py")
    normalized = filename.replace("\\", "/") if isinstance(filename, str) else ""
    expected_language = language_for_path(normalized)
    if (
        not normalized
        or normalized.startswith("/")
        or ":" in normalized.split("/")[0]
        or ".." in normalized.split("/")
        or expected_language is None
    ):
        raise EvaluationDataError(
            f"{location}: 'filename' must be a safe relative path with a supported source extension"
        )
    if expected_language != case["language"] and not {
        expected_language, case["language"]
    } <= {"javascript", "typescript"} and not {
        expected_language, case["language"]
    } <= {"c", "cpp"}:
        raise EvaluationDataError(
            f"{location}: filename language does not match case language"
        )
    if "findings" in case and (
        not isinstance(case["findings"], list)
        or any(not isinstance(finding, dict) for finding in case["findings"])
    ):
        raise EvaluationDataError(f"{location}: 'findings' must be a list of objects")
    if case["target"] == "repair" and not case.get("findings"):
        raise EvaluationDataError(
            f"{location}: repair cases must include at least one finding"
        )
    if case["target"] == "workflow" and not isinstance(case.get("checks", {}), dict):
        raise EvaluationDataError(f"{location}: 'checks' must be an object")


def _request_json(
    url: str,
    payload: dict[str, Any] | None,
    timeout: float,
) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"} if data is not None else {},
        method="POST" if data is not None else "GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise EvaluationRequestError(
            f"{url} returned HTTP {exc.code}: {detail}"
        ) from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise EvaluationRequestError(
            f"Could not get a valid response from {url}: {exc}"
        ) from exc
    if not isinstance(body, dict):
        raise EvaluationRequestError(f"{url} returned a non-object JSON response")
    return body


def _base_url(environment_name: str, default: str) -> str:
    return os.environ.get(environment_name, default).rstrip("/")


class Evaluator:
    def __init__(
        self,
        *,
        request_json: RequestJson = _request_json,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
    ) -> None:
        self.request_json = request_json
        self.timeout_seconds = timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self.validation_url = _base_url("EVAL_VALIDATION_URL", "http://localhost:8006")
        self.quality_url = _base_url("EVAL_QUALITY_URL", "http://localhost:8002")
        self.security_url = _base_url("EVAL_SECURITY_URL", "http://localhost:8009")
        self.bug_resource_url = _base_url("EVAL_BUG_RESOURCE_URL", "http://localhost:8005")
        self.repair_url = _base_url("EVAL_REPAIR_URL", "http://localhost:8007")
        self.orchestrator_url = _base_url("EVAL_ORCHESTRATOR_URL", "http://localhost:8001")
        self.sandbox_url = _base_url("EVAL_SANDBOX_URL", "http://localhost:8004")

    def evaluate_case(self, case: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            actual = getattr(self, f"_evaluate_{case['target']}")(case)
            checks = _compare(case["expected"], actual)
            return {
                "id": case["id"],
                "language": case["language"],
                "target": case["target"],
                "passed": not checks,
                "expected": case["expected"],
                "actual": actual,
                "assertion_failures": checks,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            }
        except Exception as exc:
            return {
                "id": case["id"],
                "language": case["language"],
                "target": case["target"],
                "passed": False,
                "expected": case["expected"],
                "actual": None,
                "error": f"{type(exc).__name__}: {exc}",
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            }

    def _evaluate_validation(self, case: dict[str, Any]) -> dict[str, Any]:
        filename = case.get("filename", "main.py")
        if case["language"] != "python":
            response = self.request_json(
                f"{self.sandbox_url}/execute",
                {
                    "code": case["code"],
                    "filename": filename,
                    "files": {filename: case["code"]},
                    "timeout": 20,
                    "validation_only": True,
                },
                self.timeout_seconds,
            )
            passed = (
                response.get("status") == "completed"
                and response.get("passed", response.get("exit_code") == 0)
            )
            return {
                "status": "passed" if passed else "failed",
                "valid": passed,
                "finding_codes": [],
                "finding_details": [],
                "findings_count": 0,
                "validator": response.get("validator"),
                "error": response.get("error") or response.get("stderr"),
            }
        response = self.request_json(
            f"{self.validation_url}/validate",
            {"code": case["code"], "filename": filename},
            self.timeout_seconds,
        )
        findings = response.get("findings", [])
        return {
            "status": response.get("status"),
            "valid": response.get("valid"),
            "finding_codes": _finding_codes(findings),
            "finding_details": _finding_details(findings),
            "findings_count": response.get("total_findings", len(findings)),
        }

    def _evaluate_analysis(self, case: dict[str, Any]) -> dict[str, Any]:
        response = self.request_json(
            f"{self.quality_url}/analyze",
            {
                "project_id": f"evaluation-{uuid4().hex}",
                "files": [{
                    "path": case.get("filename", "main.py"),
                    "content": case["code"],
                }],
                "tools": case.get("tools"),
            },
            self.timeout_seconds,
        )
        findings = response.get("findings", [])
        summary = response.get("quality_summary") or {}
        return {
            "status": "failed" if response.get("error") else "completed",
            "error": response.get("error"),
            "finding_codes": _finding_codes(findings),
            "finding_details": _finding_details(findings),
            "findings_count": response.get("findings_count", len(findings)),
            "quality_gate_status": summary.get("quality_gate_status"),
        }

    def _evaluate_security(self, case: dict[str, Any]) -> dict[str, Any]:
        response = self.request_json(
            f"{self.security_url}/scan",
            {
                "filename": case.get("filename", "main.py"),
                "code": case["code"],
            },
            self.timeout_seconds,
        )
        findings = response.get("findings", [])
        return {
            "status": response.get("status"),
            "finding_codes": _finding_codes(findings),
            "finding_details": _finding_details(findings),
            "findings_count": response.get("total_findings", len(findings)),
        }

    def _evaluate_bug_resource(self, case: dict[str, Any]) -> dict[str, Any]:
        filename = case.get("filename", "main.py")
        response = self.request_json(
            f"{self.bug_resource_url}/analyze",
            {
                "project_id": f"evaluation-{uuid4().hex}",
                "files": [{"path": filename, "content": case["code"]}],
            },
            self.timeout_seconds,
        )
        findings = response.get("findings", [])
        return {
            "status": "failed" if response.get("error") else "completed",
            "error": response.get("error"),
            "finding_codes": _finding_codes(findings),
            "finding_details": _finding_details(findings),
            "findings_count": response.get("findings_count", len(findings)),
        }

    def _evaluate_repair(self, case: dict[str, Any]) -> dict[str, Any]:
        findings = []
        for index, finding in enumerate(case["findings"], start=1):
            findings.append({
                "finding_id": finding.get("finding_id", f"{case['id']}-{index}"),
                "file": finding.get("file", case.get("filename", "main.py")),
                "line": finding.get("line"),
                "rule_id": finding.get("rule_id"),
                "type": finding.get("type", "code_quality"),
                "severity": finding.get("severity", "medium"),
                "message": finding.get("message", "Dataset repair finding"),
                "recommendation": finding.get("recommendation"),
            })
        response = self.request_json(
            f"{self.repair_url}/repair",
            {
                "project_id": f"evaluation-{uuid4().hex}",
                "language": case["language"],
                "files": [{
                    "path": case.get("filename", "main.py"),
                    "content": case["code"],
                }],
                "findings": findings,
            },
            self.timeout_seconds,
        )
        resolved_ids = [
            finding_id
            for repair in response.get("repairs", [])
            for finding_id in repair.get("resolved_finding_ids", [])
        ]
        repair_details = [
            {
                "path": repair.get("path"),
                "changed": repair.get("changed"),
                "explanation": repair.get("explanation"),
            }
            for repair in response.get("repairs", [])
        ]
        return {
            "status": response.get("status"),
            "outcome": response.get("outcome"),
            "error": response.get("error"),
            "resolved_finding_ids": sorted(set(resolved_ids)),
            "unresolved_findings_count": len(response.get("unresolved_findings", [])),
            "changed_files_count": len(response.get("changed_files", [])),
            "repair_details": repair_details,
        }

    def _evaluate_workflow(self, case: dict[str, Any]) -> dict[str, Any]:
        workflow_id = f"evaluation-{uuid4().hex}"
        checks = {**DEFAULT_WORKFLOW_CHECKS, **case.get("checks", {})}
        request = {
            "project_id": workflow_id,
            "workflow_id": workflow_id,
            "files": [{
                "path": case.get("filename", "main.py"),
                "content": case["code"],
            }],
            "project_context": {"analysis_checks": checks},
            "tools": case.get("tools"),
        }
        self.request_json(
            f"{self.orchestrator_url}/workflow/start",
            request,
            self.timeout_seconds,
        )
        deadline = time.monotonic() + self.timeout_seconds
        while time.monotonic() < deadline:
            response = self.request_json(
                f"{self.orchestrator_url}/workflow/status/{urllib.parse.quote(workflow_id, safe='')}",
                None,
                min(self.timeout_seconds, max(1, deadline - time.monotonic())),
            )
            status = response.get("status")
            if status in {"completed", "failed", "cancelled"}:
                result = response.get("result") or {}
                final_report = result.get("final_report") or {}
                agent_statuses = _workflow_agent_statuses(result)
                validation_results = result.get("validation_results") or {}
                quality_summary = result.get("quality_summary") or {}
                quality_gate = quality_summary.get("quality_gate") or {}
                return {
                    "status": status,
                    "error": response.get("error"),
                    "workflow_status": final_report.get("workflow_status"),
                    "workflow_status_reason": final_report.get("workflow_status_reason"),
                    "validation_status": response.get(
                        "validation_status", result.get("validation_status")
                    ),
                    "validation_findings": _finding_details(
                        validation_results.get("findings", [])
                    ),
                    "quality_gate_status": (
                        quality_summary.get("quality_gate_status")
                        or quality_gate.get("status")
                    ),
                    "ai_recommendations_status": (
                        (result.get("ai_recommendations_status") or {}).get("status")
                    ),
                    "ai_recommendations_provider": (
                        (result.get("ai_recommendations_status") or {}).get("provider")
                    ),
                    "ai_report_scanned_files": (
                        (result.get("code_report") or {}).get("scanned_files")
                    ),
                    "agent_statuses": agent_statuses,
                    "workflow_stage_statuses": _workflow_stage_statuses(result),
                    "agent_errors": result.get("agent_errors", []),
                }
            time.sleep(self.poll_interval_seconds)
        raise EvaluationRequestError(
            f"Workflow {workflow_id} did not finish within {self.timeout_seconds:g} seconds"
        )


def _finding_codes(findings: Any) -> list[str]:
    if not isinstance(findings, list):
        return []
    return sorted({
        str(code)
        for finding in findings
        if isinstance(finding, dict)
        for code in [
            finding.get("code")
            or finding.get("issue")
            or finding.get("rule_id")
            or finding.get("type")
        ]
        if code
    })


def _finding_details(findings: Any) -> list[dict[str, Any]]:
    if not isinstance(findings, list):
        return []
    return [
        {
            "code": finding.get("code") or finding.get("issue"),
            "file": finding.get("file"),
            "line": finding.get("line", finding.get("line_number")),
            "message": finding.get("message") or finding.get("description"),
            "rule_id": (
                finding.get("rule_id")
                or finding.get("code")
                or finding.get("issue")
                or finding.get("type")
            ),
            "severity": (
                finding.get("severity").lower()
                if isinstance(finding.get("severity"), str)
                else finding.get("severity")
            ),
        }
        for finding in findings
        if isinstance(finding, dict)
    ]


def _workflow_agent_statuses(result: dict[str, Any]) -> dict[str, str]:
    statuses: dict[str, str] = {}
    for stage in result.get("stage_history", []):
        if not isinstance(stage, dict):
            continue
        for name, status in (stage.get("agents") or {}).items():
            statuses[name] = status
        stage_name = stage.get("stage")
        if stage_name == "repair":
            statuses["repair"] = stage.get("status", "unknown")
        elif stage_name == "validate":
            statuses["validation"] = stage.get("status", "unknown")
    report_status = (result.get("ai_recommendations_status") or {}).get("status")
    if report_status:
        statuses["report_recommendation"] = report_status
    return statuses


def _workflow_stage_statuses(result: dict[str, Any]) -> dict[str, str]:
    return {
        stage["stage"]: stage.get("status", "unknown")
        for stage in result.get("stage_history", [])
        if isinstance(stage, dict) and isinstance(stage.get("stage"), str)
    }


def _compare(expected: dict[str, Any], actual: dict[str, Any]) -> list[str]:
    failures = []
    for key in (
        "status",
        "workflow_status",
        "validation_status",
        "quality_gate_status",
        "outcome",
        "ai_recommendations_status",
        "ai_recommendations_provider",
        "ai_report_scanned_files",
    ):
        if key in expected and actual.get(key) != expected[key]:
            failures.append(
                f"{key}: expected {expected[key]!r}, got {actual.get(key)!r}"
            )
    expected_codes = set(expected.get("finding_codes", []))
    actual_codes = set(actual.get("finding_codes", []))
    missing_codes = sorted(expected_codes - actual_codes)
    if missing_codes:
        failures.append(f"missing finding codes: {', '.join(missing_codes)}")
    actual_findings = actual.get("finding_details", [])
    for expected_finding in expected.get("expected_findings", []):
        rule_id = expected_finding.get("rule_id")
        severity = expected_finding.get("severity")
        if not any(
            finding.get("rule_id") == rule_id
            and (
                severity is None
                or str(finding.get("severity", "")).lower() == severity.lower()
            )
            for finding in actual_findings
        ):
            expected_label = f"{rule_id} ({severity})" if severity else str(rule_id)
            failures.append(f"missing expected finding: {expected_label}")
    if "minimum_findings" in expected and actual.get("findings_count", 0) < expected["minimum_findings"]:
        failures.append(
            f"expected at least {expected['minimum_findings']} findings, "
            f"got {actual.get('findings_count', 0)}"
        )
    if "maximum_findings" in expected and actual.get("findings_count", 0) > expected["maximum_findings"]:
        failures.append(
            f"expected at most {expected['maximum_findings']} findings, "
            f"got {actual.get('findings_count', 0)}"
        )
    expected_resolved = set(expected.get("resolved_finding_ids", []))
    actual_resolved = set(actual.get("resolved_finding_ids", []))
    missing_resolved = sorted(expected_resolved - actual_resolved)
    if missing_resolved:
        failures.append(f"unresolved expected finding IDs: {', '.join(missing_resolved)}")
    required_agents = expected.get("required_agents", [])
    agent_statuses = actual.get("agent_statuses", {})
    incomplete = [
        f"{agent} ({agent_statuses.get(agent, 'not run')})"
        for agent in required_agents
        if agent_statuses.get(agent) not in {"completed", "passed"}
    ]
    if incomplete:
        failures.append(f"required agents did not complete: {', '.join(incomplete)}")
    return failures


def evaluate(cases: list[dict[str, Any]], evaluator: Evaluator) -> dict[str, Any]:
    started_at = datetime.now(timezone.utc).isoformat()
    results = [evaluator.evaluate_case(case) for case in cases]
    passed = sum(result["passed"] for result in results)
    failed = len(results) - passed
    language_metrics: dict[str, dict[str, int | float]] = {}
    agent_totals: dict[str, list[int]] = {}
    stage_totals: dict[str, list[int]] = {}
    for result in results:
        language = result.get("language", "unknown")
        metrics = language_metrics.setdefault(language, {"total": 0, "passed": 0, "failed": 0})
        metrics["total"] += 1
        metrics["passed"] += int(result["passed"])
        metrics["failed"] += int(not result["passed"])
        target = result.get("target")
        actual = result.get("actual") or {}
        direct_agent = {
            "validation": "validation",
            "analysis": "code_quality",
            "security": "security",
            "bug_resource": "bug_resource",
            "repair": "repair",
        }.get(target)
        statuses = actual.get("agent_statuses", {})
        if direct_agent:
            statuses = {
                **statuses,
                direct_agent: "passed" if result["passed"] else "failed",
            }
        for agent, status in statuses.items():
            counts = agent_totals.setdefault(agent, [0, 0, 0, 0])
            counts[0] += 1
            normalized_status = status.lower() if isinstance(status, str) else ""
            if normalized_status in SKIPPED_STATUSES:
                counts[3] += 1
            elif normalized_status in SUCCESS_STATUSES:
                counts[1] += 1
            else:
                counts[2] += 1
        for stage, status in (actual.get("workflow_stage_statuses") or {}).items():
            counts = stage_totals.setdefault(stage, [0, 0, 0, 0])
            counts[0] += 1
            normalized_status = status.lower() if isinstance(status, str) else ""
            if normalized_status in SKIPPED_STATUSES:
                counts[3] += 1
            elif normalized_status in SUCCESS_STATUSES:
                counts[1] += 1
            else:
                counts[2] += 1
    for metrics in language_metrics.values():
        metrics["pass_rate"] = round(metrics["passed"] / metrics["total"], 4) if metrics["total"] else 0
    return {
        "schema_version": 1,
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "total": len(results),
            "passed": passed,
            "failed": failed,
            "pass_rate": round(passed / len(results), 4) if results else 0,
        },
        "per_language": language_metrics,
        "per_agent": {
            agent: {
                "total": counts[0],
                "passed": counts[1],
                "failed": counts[2],
                "skipped": counts[3],
                "pass_rate": round(
                    counts[1] / (counts[0] - counts[3]),
                    4,
                ) if counts[0] > counts[3] else 0,
            }
            for agent, counts in sorted(agent_totals.items())
        },
        "per_workflow_stage": {
            stage: {
                "total": counts[0],
                "passed": counts[1],
                "failed": counts[2],
                "skipped": counts[3],
                "pass_rate": round(
                    counts[1] / (counts[0] - counts[3]),
                    4,
                ) if counts[0] > counts[3] else 0,
            }
            for stage, counts in sorted(stage_totals.items())
        },
        "results": results,
    }


def _dataset_paths(arguments: list[str] | None) -> list[Path]:
    if arguments:
        paths = [Path(argument).resolve() for argument in arguments]
    else:
        paths = sorted(DATASET_DIRECTORY.glob("*.jsonl"))
    if not paths:
        raise EvaluationDataError(f"No JSONL datasets found in {DATASET_DIRECTORY}")
    return paths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate coding agents against JSONL datasets.")
    parser.add_argument(
        "--dataset",
        action="append",
        help="Dataset JSONL path (repeat to evaluate multiple files; defaults to all bundled datasets).",
    )
    parser.add_argument("--report", type=Path, help="Write the JSON report to this path.")
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"Per-request/workflow timeout in seconds (default: {DEFAULT_TIMEOUT_SECONDS:g}).",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=DEFAULT_POLL_INTERVAL_SECONDS,
        help=f"Workflow status polling interval in seconds (default: {DEFAULT_POLL_INTERVAL_SECONDS:g}).",
    )
    args = parser.parse_args(argv)
    if args.timeout <= 0 or args.poll_interval <= 0:
        parser.error("--timeout and --poll-interval must be positive")
    try:
        cases = load_cases(_dataset_paths(args.dataset))
    except EvaluationDataError as exc:
        print(f"Dataset error: {exc}", file=sys.stderr)
        return 2
    report = evaluate(
        cases,
        Evaluator(
            timeout_seconds=args.timeout,
            poll_interval_seconds=args.poll_interval,
        ),
    )
    output = json.dumps(report, indent=2, ensure_ascii=False)
    if args.report:
        try:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(output + "\n", encoding="utf-8")
        except OSError as exc:
            print(f"Cannot write evaluation report: {exc}", file=sys.stderr)
            return 2
    print(output)
    return 0 if report["summary"]["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
