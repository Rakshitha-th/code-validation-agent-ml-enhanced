"""
Individual LangGraph nodes. Each node takes the WorkflowState, does one
job, and returns a partial dict that LangGraph merges back into state.

Nodes only talk to other services through the `app/services/*_client.py`
clients — never by importing another team member's code directly.
"""
import asyncio
from collections import Counter
from collections.abc import Awaitable
import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from time import perf_counter

import httpx

from app.graph.state import WorkflowState
from app.services import (
    security_client,
    quality_client,
    bug_resource_client,
    repair_client,
    report_recommendation_client,
    sandbox_client,
    validation_client,
)
from app.aggregation.finding_aggregator import aggregate
from app.config import settings
from app.analysis.python_structure import inspect_python_files
from app.reporting.report_builder import build_report
from shared.languages import detect_language, source_files

logger = logging.getLogger(__name__)
REPAIR_REJECTED_PREFIX = "Repair rejected:"


def understand_project(state: WorkflowState) -> dict:
    """Light-weight project intake: just confirms we have files and sets
    initial counters. Kept separate from `analyze` so it's easy to later
    add real project-understanding (dependency graph, language detection)
    without touching the analysis fan-out logic."""
    files = state.get("project_files", {})
    checks = _analysis_checks(state.get("project_context"))
    language = detect_language(list(source_files(files)))
    structure = inspect_python_files(_python_files(files))
    stage = {"stage": "understand", "status": "completed", "file_count": len(files)}
    return {
        "retry_count": 0,
        "repair_attempt": 0,
        "tools": state.get("tools") if language == "python" else None,
        "validation_status": "not_run" if checks is not None and not checks["final_validation"] else "pending",
        "code_structure": structure,
        "analysis_id": state.get("analysis_id") or state.get("run_id") or state.get("project_id"),
        "agent_errors": [],
        "stage_history": [stage],
        "workflow_trace": ["Workflow started", "Project understanding completed"],
        "error": None if files else "No project files supplied",
    }


def _analysis_agent_flags(checks: dict | None) -> tuple[bool, bool, bool]:
    security_enabled = checks is None or checks["security"]
    quality_enabled = checks is None or checks["code_quality"] or checks["complexity"]
    resource_enabled = checks is None or checks["bug_resource"]
    return security_enabled, quality_enabled, resource_enabled


def _python_files(files: dict[str, str]) -> dict[str, str]:
    return {path: content for path, content in files.items() if path.lower().endswith(".py")}


async def _collect_analysis_responses(
    project_id: str,
    files: dict[str, str],
    context: dict,
    tools: list[str] | None,
    security_enabled: bool,
    quality_enabled: bool,
    resource_enabled: bool,
    workflow_id: str | None = None,
) -> dict[str, dict]:
    calls = []
    agents = []
    language = detect_language(list(source_files(files)))
    quality_tools = tools if language == "python" else None
    if security_enabled:
        agents.append("security")
        calls.append(_time_analysis_call(
            project_id,
            workflow_id or project_id,
            "security",
            security_client.analyze(project_id, files, context),
        ))
    if quality_enabled:
        agents.append("code_quality")
        calls.append(_time_analysis_call(
            project_id,
            workflow_id or project_id,
            "code_quality",
            quality_client.analyze(project_id, files, context, quality_tools),
        ))
    if resource_enabled:
        agents.append("bug_resource")
        calls.append(_time_analysis_call(
            project_id,
            workflow_id or project_id,
            "bug_resource",
            bug_resource_client.analyze(project_id, source_files(files), context),
        ))

    results = await asyncio.gather(*calls, return_exceptions=True)
    responses = {}
    for agent, result in zip(agents, results):
        if isinstance(result, Exception):
            responses[agent] = {"findings": [], "error": f"{agent} failed: {result}"}
        else:
            responses[agent] = result
    return responses


async def _time_analysis_call(
    project_id: str,
    workflow_id: str,
    agent: str,
    call: Awaitable[dict],
) -> dict:
    started = perf_counter()
    try:
        return await call
    finally:
        duration_ms = round((perf_counter() - started) * 1000, 2)
        logger.info(
            "workflow_agent_timing run_id=%s project_id=%s agent=%s duration_ms=%s",
            workflow_id,
            project_id,
            agent,
            duration_ms,
            extra={
                "workflow_run_id": workflow_id,
                "project_id": project_id,
                "workflow_agent": agent,
                "duration_ms": duration_ms,
            },
        )


def _collect_analysis_errors(responses: dict[str, dict]) -> list[dict[str, str]]:
    return [
        {"agent": agent, "error": result["error"]}
        for agent, result in responses.items()
        if result.get("error")
    ]


def _analysis_agent_statuses(
    security_enabled: bool,
    quality_enabled: bool,
    resource_enabled: bool,
    agent_errors: list[dict[str, str]],
) -> dict[str, str]:
    statuses = {
        "security": "skipped" if not security_enabled else "completed",
        "quality": "skipped" if not quality_enabled else "completed",
        "bug_resource": "skipped" if not resource_enabled else "completed",
    }
    for error in agent_errors:
        agent = "quality" if error["agent"] == "code_quality" else error["agent"]
        if agent in statuses:
            statuses[agent] = "failed"
    return statuses


def _log_analysis_endpoints(
    state: WorkflowState,
    stage: str,
    flags: tuple[bool, bool, bool],
    file_count: int,
) -> None:
    security_enabled, quality_enabled, resource_enabled = flags
    endpoints: list[str] = []
    if security_enabled:
        endpoints.append("security:/scan")
    if quality_enabled:
        endpoints.append("code-quality:/analyze")
    if resource_enabled:
        endpoints.append("bug-resource:/analyze")
    workflow_id = state.get("run_id") or state.get("analysis_id") or state.get("project_id")
    logger.info(
        "workflow_endpoint_calls run_id=%s node=%s endpoints=%s file_count=%s",
        workflow_id,
        stage,
        ",".join(endpoints) if endpoints else "none",
        file_count,
        extra={
            "workflow_run_id": workflow_id,
            "workflow_node": stage,
            "service_endpoints": endpoints,
        },
    )


async def analyze(state: WorkflowState) -> dict:
    """Fan out to enabled analysis agents and store their raw findings."""
    files = state.get("project_files", {})
    analysis_files = files
    context = state.get("project_context", {})
    project_id = state["project_id"]
    tools = state.get("tools")
    checks = _analysis_checks(context)
    flags = _analysis_agent_flags(checks)
    quality_enabled = flags[1]
    _log_analysis_endpoints(state, "analyze", flags, len(source_files(analysis_files)))
    responses = await _collect_analysis_responses(
        project_id,
        analysis_files,
        context,
        tools,
        *flags,
        state.get("run_id") or state.get("analysis_id") or project_id,
    )
    security_res = responses.get("security", {"findings": []})
    quality_res = responses.get("code_quality", {"findings": []})
    resource_res = responses.get("bug_resource", {"findings": []})
    agent_errors = _collect_analysis_errors(responses)
    agent_statuses = _analysis_agent_statuses(*flags, agent_errors)

    update = {
        "security_findings": security_res.get("findings", []),
        "quality_findings": quality_res.get("findings", []),
        "resource_findings": resource_res.get("findings", []),
        "agent_errors": agent_errors,
        "workflow_trace": [*state.get("workflow_trace", []), "Analysis agents completed"],
        "stage_history": [*state.get("stage_history", []), {
            "stage": "analyze",
            "status": "completed" if not agent_errors else "partial",
            "agent_errors": agent_errors,
            "agents": agent_statuses,
        }],
    }
    if quality_enabled:
        quality_summary = quality_res.get("quality_summary", {})
        update["quality_summary"] = quality_summary
        update["quality_gate_status"] = (
            quality_summary.get("quality_gate_status")
            or quality_summary.get("quality_gate", {}).get("status", "UNKNOWN")
        )
    return update


def _analysis_checks(project_context: dict | None) -> dict | None:
    """Return normalized user-selected checks, or None for legacy requests."""
    context = project_context or {}
    checks = context.get("analysis_checks")
    if not isinstance(checks, dict):
        return None
    return {
        "code_quality": bool(checks.get("code_quality", True)),
        "security": bool(checks.get("security", True)),
        "bug_resource": bool(checks.get("bug_resource", True)),
        "complexity": bool(checks.get("complexity", True)),
        "automated_repair": bool(checks.get("automated_repair", True)),
        "final_validation": bool(checks.get("final_validation", True)),
        "ai_report_recommendations": bool(checks.get("ai_report_recommendations", False)),
    }


def aggregate_findings(state: WorkflowState) -> dict:
    findings = aggregate(
        state.get("security_findings", []),
        state.get("quality_findings", []),
        state.get("resource_findings", []),
    )
    return {
        "findings": findings,
        "workflow_trace": [*state.get("workflow_trace", []), "Findings aggregated"],
        "stage_history": [*state.get("stage_history", []), {
            "stage": "aggregate", "status": "completed", "finding_count": len(findings)
        }],
    }


def explain_and_prioritize(state: WorkflowState) -> dict:
    """Create deterministic explanations and repair priority metadata."""
    findings = state.get("findings", [])
    severity_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
    prioritized = sorted(
        findings,
        key=lambda finding: (
            severity_rank.get(finding.get("severity", "medium"), 2),
            0 if state.get("quality_gate_status") == "FAIL" and finding.get("category") == "code_quality" else 1,
            finding.get("file", ""),
            finding.get("line_number") or 0,
        ),
    )
    explanations = [
        {
            "finding_id": finding.get("finding_id"),
            "priority": index + 1,
            "root_cause": finding.get("description", "Reported analyzer issue"),
            "recommendation": finding.get("recommendation") or "Review and address the finding.",
        }
        for index, finding in enumerate(prioritized)
    ]
    return {
        "prioritized_findings": prioritized,
        "explanations": explanations,
        "workflow_trace": [*state.get("workflow_trace", []), "Findings prioritized"],
        "stage_history": [*state.get("stage_history", []), {
            "stage": "explain_prioritize", "status": "completed", "finding_count": len(prioritized)
        }],
    }


def _report_failure_message(error: Exception) -> str:
    if isinstance(error, httpx.TimeoutException):
        return "AI report/recommendation generation timed out while waiting for Ollama."
    if not isinstance(error, httpx.HTTPStatusError):
        return f"AI report/recommendation generation failed ({type(error).__name__})."
    message = f"AI report service returned HTTP {error.response.status_code}."
    try:
        detail = str(error.response.json().get("detail") or "")
    except (ValueError, AttributeError):
        detail = ""
    return f"{message} {detail}" if detail else message


async def _run_report_recommendation(files: dict[str, str]) -> tuple[dict, str, dict, dict]:
    try:
        result = await report_recommendation_client.analyze(source_files(files))
    except Exception as exc:  # noqa: BLE001 - optional AI reporting must not fail analysis
        message = _report_failure_message(exc)
        return {"status": "failed", "message": message}, "failed", {}, {}
    code_report = result.get("code_report", {})
    recommendation_report = result.get("recommendation_report", {})
    if result.get("status") == "skipped":
        status = {"status": "skipped", "message": result.get("message", "Agent is not configured.")}
        return status, "skipped", code_report, recommendation_report
    return {"status": "completed", "provider": result.get("provider")}, "completed", code_report, recommendation_report


async def report_and_recommend(state: WorkflowState) -> dict:
    checks = _analysis_checks(state.get("project_context"))
    if checks is None or not checks["ai_report_recommendations"]:
        status, stage_status, code_report, recommendation_report = {"status": "disabled"}, "skipped", {}, {}
    else:
        status, stage_status, code_report, recommendation_report = await _run_report_recommendation(
            state.get("project_files", {})
        )
    return {
        "code_report": code_report,
        "recommendation_report": recommendation_report,
        "ai_recommendations_status": status,
        "workflow_trace": [*state.get("workflow_trace", []), "AI report and recommendation stage completed"],
        "stage_history": [*state.get("stage_history", []), {
            "stage": "report_recommend",
            "status": stage_status,
            **({"message": status["message"]} if status.get("message") else {}),
        }],
    }


def _skipped_stages(checks: dict | None, stage_history: list[dict]) -> list[dict[str, str]]:
    if checks is None:
        return []
    reported_stages = {item.get("stage") for item in stage_history}
    skipped = []
    if not checks["automated_repair"] and "repair" not in reported_stages:
        skipped.append({"stage": "repair", "status": "skipped"})
    if not checks["final_validation"] and "validate" not in reported_stages:
        skipped.append({"stage": "validate", "status": "skipped"})
    if not checks["automated_repair"]:
        if "reanalyze" not in reported_stages:
            skipped.append({"stage": "reanalyze", "status": "skipped"})
        if "compare" not in reported_stages:
            skipped.append({"stage": "compare", "status": "skipped"})
    return skipped


def _format_unresolved_finding(finding: dict) -> str:
    return (
        f"{finding.get('file', 'unknown file')}:{finding.get('line') or '?'} "
        f"{finding.get('rule_id') or finding.get('issue') or finding.get('type')}: "
        f"{finding.get('message') or finding.get('description') or 'Finding remains unresolved.'} "
        f"Recommendation: {finding.get('recommendation') or 'Apply a minimal targeted fix.'}"
    )


def _repair_feedback(previous_attempt: dict, attempt: int) -> str:
    previous_error = (
        previous_attempt.get("error")
        or previous_attempt.get("candidate_rejection_reason")
        or ""
    )
    regression_reasons = previous_attempt.get("regression_reasons", [])
    if regression_reasons:
        previous_error = "\n".join([previous_error, *regression_reasons]).strip()
    previous_unresolved = previous_attempt.get("unresolved_findings", [])
    if not previous_error and not previous_unresolved and not regression_reasons:
        return ""

    feedback_details = []
    for detail in previous_attempt.get("repair_details", []):
        explanation = detail.get("explanation")
        if (
            explanation
            and explanation != previous_error
            and not previous_error.startswith(REPAIR_REJECTED_PREFIX)
        ):
            feedback_details.append(f"{detail.get('path', 'unknown file')}: {explanation}")
        candidate = detail.get("candidate_code")
        if candidate:
            feedback_details.append(
                f"Rejected candidate for {detail.get('path', 'unknown file')} "
                f"(do not repeat it):\n{candidate}"
            )
    for path, candidate in previous_attempt.get("candidate_files", {}).items():
        if candidate:
            feedback_details.append(
                f"Candidate for {path} failed verification; correct the diagnostics below:\n{candidate}"
            )
    unresolved = [_format_unresolved_finding(item) for item in previous_unresolved]
    strategy_feedback = ""
    if attempt > 1:
        strategy_feedback = (
            f"Attempt {attempt}: choose a different repair strategy from the previous attempt. "
            "Address each remaining finding directly; do not return the unchanged source unless "
            "the finding is demonstrably a false positive."
        )
    return "\n".join([previous_error, *feedback_details, *unresolved, strategy_feedback])


async def _call_repair_service(
    state: WorkflowState,
    files: dict[str, str],
    findings: list[dict],
    feedback: str,
    language: str | None,
) -> dict:
    project_id = state["project_id"]
    workflow_id = state.get("run_id") or state.get("analysis_id") or project_id
    logger.info(
        "workflow_endpoint_call run_id=%s node=repair endpoint=repair:/repair language=%s file_count=%s finding_count=%s",
        workflow_id,
        language or "unknown",
        len(files),
        len(findings),
        extra={"workflow_run_id": workflow_id, "service_endpoint": "repair:/repair"},
    )
    try:
        arguments = (project_id, files, findings, feedback)
        if language == "python":
            return await repair_client.repair(*arguments)
        return await repair_client.repair(*arguments, language)
    except Exception as exc:  # noqa: BLE001 - convert downstream failure to state
        logger.exception(
            "workflow_endpoint_exception run_id=%s node=repair endpoint=repair:/repair",
            workflow_id,
            extra={"workflow_run_id": workflow_id, "service_endpoint": "repair:/repair"},
        )
        return {"repaired_files": [], "error": f"repair failed: {exc}"}


def _repair_status(result: dict) -> str:
    fallback = {"completed": "needs_review", "partial": "needs_review", "failed": "failed"}
    return result.get("repair_status") or fallback.get(result.get("status"), "needs_review")


def _repair_attempt_record(
    attempt: int,
    result: dict,
    input_findings: list[dict],
    original_files: dict[str, str],
    repaired_files: dict[str, str],
    changed_files: list[str],
    repair_status: str,
    repair_error: str | None,
) -> dict:
    rejected_candidates = []
    for detail in result.get("repair_details", []):
        if detail.get("candidate_code") is None:
            continue
        explanation = str(detail.get("explanation") or "")
        rejected_candidates.append({
            "path": detail.get("path"),
            "code": detail.get("candidate_code"),
            "reason": detail.get("explanation"),
            "decision": "rejected" if explanation.startswith(REPAIR_REJECTED_PREFIX) else "no_change",
        })
    fingerprint = hashlib.sha256(
        json.dumps(repaired_files, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "iteration": attempt,
        "mode": result.get("mode", "targeted"),
        "service_status": result.get("status", "unknown"),
        "repair_status": repair_status,
        "input_findings": input_findings,
        "input_files": {path: original_files[path] for path in changed_files},
        "candidate_files": {path: repaired_files[path] for path in changed_files},
        "candidate_fingerprint": fingerprint,
        "verification_status": result.get("repair_status", "needs_review"),
        "skipped_checks": result.get("skipped_checks", []),
        "rejected_candidates": rejected_candidates,
        "changed_files": changed_files,
        "candidate_status": "awaiting_reanalysis" if changed_files else "no_change",
        "candidate_reason": repair_error,
        "outcome": repair_status,
        "unresolved_findings": result.get("unresolved_findings", []),
        "repair_details": result.get("repair_details", []),
        "error": repair_error,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


async def repair(state: WorkflowState) -> dict:
    """Calls the Fix & Regeneration service with the current findings."""
    project_files = state.get("repaired_code") or state.get("project_files", {})
    files = source_files(project_files)
    language = detect_language(list(files))
    attempt = state.get("repair_attempt", 0) + 1
    input_findings = (
        state.get("reanalysis_findings", [])
        if state.get("repair_attempt", 0) > 0
        else state.get("findings", [])
    )
    previous_attempts = state.get("repair_attempts", [])
    previous_attempt = previous_attempts[-1] if previous_attempts else {}
    feedback = _repair_feedback(previous_attempt, attempt)
    result = await _call_repair_service(state, files, input_findings, feedback, language)

    repaired_files = {
        **project_files,
        **{item["path"]: item["content"] for item in result.get("repaired_files", [])},
    }
    repair_error = result.get("error")
    repair_status = _repair_status(result)
    agent_errors = [*state.get("agent_errors", [])]
    if repair_error and repair_status not in {"needs_review", "not_run"}:
        agent_errors.append({"agent": "repair", "error": repair_error})
    changed_files = [
        path for path in files
        if repaired_files.get(path) != project_files.get(path)
    ]
    stage_status = {
        "failed": "failed",
        "partial": "partial",
        "needs_review": "needs_review",
        "not_run": "not_run",
    }.get(repair_status, "completed")
    attempt_record = _repair_attempt_record(
        attempt,
        result,
        input_findings,
        files,
        repaired_files,
        changed_files,
        repair_status,
        repair_error,
    )
    return {
        "repaired_code": repaired_files or project_files,
        "repair_attempt": attempt,
        "repair_attempts": [*previous_attempts, attempt_record],
        "agent_errors": agent_errors,
        "workflow_trace": [*state.get("workflow_trace", []), f"Repair iteration {attempt} requested"],
        "stage_history": [*state.get("stage_history", []), {
            "stage": "repair",
            "status": stage_status,
            "attempt": attempt,
        }],
    }

SANDBOX_BLOCKED_STATUSES = {
    "dependency_missing",
    "project_incomplete",
    "unsupported_project",
    "unavailable",
}


def _validation_outcome(
    sandbox_result: dict,
    validation_result: dict,
) -> str:
    if validation_result.get("status") == "error" or validation_result.get("error"):
        return "error"

    if sandbox_result.get("status") in SANDBOX_BLOCKED_STATUSES - {"unavailable"}:
        return "blocked"

    if sandbox_result.get("comparison_status") == "failed":
        return "failed"

    if (
        sandbox_result.get("status") == "unavailable"
        or (
            sandbox_result.get("error")
            and not sandbox_result.get("executed")
        )
    ):
        return "error"

    if validation_result.get("status") in {"blocked", "inconclusive"}:
        return validation_result["status"]

    if (
        sandbox_result.get("status") in SANDBOX_BLOCKED_STATUSES
        or not sandbox_result.get("executed")
    ):
        return "blocked"

    sandbox_passed = sandbox_result.get(
        "passed",
        sandbox_result.get("exit_code") == 0,
    )

    validator_passed = validation_result.get(
        "passed",
        validation_result.get("valid", False),
    )

    if not sandbox_passed or not validator_passed:
        return "failed"

    if validation_result.get("status") == "needs_review":
        return "needs_review"

    return validation_result.get("status", "passed")


def _record_validation_error(
    errors: list[dict],
    agent: str,
    detail: str,
) -> None:
    if not any(
        item.get("agent") == agent and item.get("error") == detail
        for item in errors
    ):
        errors.append({
            "agent": agent,
            "error": detail,
        })


def _validation_findings_detail(validation_result: dict) -> str:
    findings = validation_result.get("findings") or []

    if not findings:
        return "Validation checks reported blocking findings."

    finding = findings[0]

    message = (
        finding.get("message")
        or finding.get("description")
        or finding.get("issue")
        or "Validation finding reported."
    )

    code = finding.get("code")

    return f"{code}: {message}" if code else message


def _mark_repair_attempts(
    attempts: list[dict],
    validation_status: str,
    files: dict[str, str],
    reanalysis_findings: list[dict],
    quality_summary: dict,
    sandbox_result: dict,
    validation_results: dict,
) -> list[dict]:
    if (
        not attempts
        or attempts[-1].get("outcome") in {"failed", "error"}
        or attempts[-1].get("candidate_rollback")
    ):
        return attempts

    outcome = "validated"

    if validation_status == "needs_review":
        outcome = "needs_review"
    elif validation_status != "passed":
        outcome = "validation_failed"

    if not attempts[-1].get("changed_files"):
        candidate_status = "no_change"
    elif validation_status == "passed":
        candidate_status = "accepted"
    elif validation_status != "needs_review":
        candidate_status = "rejected"
    else:
        candidate_status = attempts[-1].get(
            "candidate_status",
            "unverified",
        )

    attempts[-1] = {
        **attempts[-1],
        "outcome": outcome,
        "candidate_status": candidate_status,
        "validation_status": validation_status,
        **({
            "verified_files": files,
            "verified_findings": reanalysis_findings,
            "verified_quality_summary": quality_summary,
            "verified_sandbox_result": sandbox_result,
            "verified_validation_results": validation_results,
        } if validation_status == "passed" else {}),
        **({
            "reviewed_files": files,
            "reviewed_findings": reanalysis_findings,
            "reviewed_quality_summary": quality_summary,
            "reviewed_sandbox_result": sandbox_result,
            "reviewed_validation_results": validation_results,
        } if validation_status == "needs_review" else {}),
    }

    return attempts


async def _execute_sandbox_validation(
    project_id: str,
    files: dict[str, str],
    workflow_id: str,
    language: str | None,
    stdin: str,
    test_cases: list[dict],
) -> dict:
    logger.info(
        "workflow_endpoint_call run_id=%s node=validate endpoint=sandbox:/execute language=%s file_count=%s test_case_count=%s",
        workflow_id,
        language or "unknown",
        len(source_files(files)),
        len(test_cases),
        extra={
            "workflow_run_id": workflow_id,
            "service_endpoint": "sandbox:/execute",
        },
    )

    try:
        options = {"test_cases": test_cases} if test_cases else {}

        return await sandbox_client.execute(
            project_id,
            files,
            stdin,
            **options,
        )

    except Exception as exc:
        logger.exception(
            "workflow_endpoint_exception run_id=%s node=validate endpoint=sandbox:/execute",
            workflow_id,
            extra={
                "workflow_run_id": workflow_id,
                "service_endpoint": "sandbox:/execute",
            },
        )

        return {
            "status": "unavailable",
            "executed": False,
            "passed": False,
            "error": f"sandbox failed: {exc}",
        }


async def _execute_validation_agent(
    project_id: str,
    files: dict[str, str],
    findings: list[dict],
    workflow_id: str,
    language: str | None,
) -> dict:
    logger.info(
        "workflow_endpoint_call run_id=%s node=validate endpoint=validation:/validate language=%s file_count=%s",
        workflow_id,
        language or "unknown",
        len(source_files(files)),
        extra={
            "workflow_run_id": workflow_id,
            "service_endpoint": "validation:/validate",
        },
    )

    try:
        return await validation_client.validate(
            project_id,
            files,
            findings,
        )

    except Exception as exc:
        logger.exception(
            "workflow_endpoint_exception run_id=%s node=validate endpoint=validation:/validate",
            workflow_id,
            extra={
                "workflow_run_id": workflow_id,
                "service_endpoint": "validation:/validate",
            },
        )

        return {
            "passed": False,
            "valid": False,
            "status": "error",
            "error": f"validation failed: {exc}",
        }


async def _time_validation_check(
    workflow_id: str,
    check: str,
    call: Awaitable[dict],
) -> dict:
    started = perf_counter()

    try:
        return await call

    finally:
        duration_ms = round(
            (perf_counter() - started) * 1000,
            2,
        )

        logger.info(
            "workflow_validation_check_timing run_id=%s check=%s duration_ms=%s",
            workflow_id,
            check,
            duration_ms,
            extra={
                "workflow_run_id": workflow_id,
                "validation_check": check,
                "duration_ms": duration_ms,
            },
        )


def _validation_agent_errors(
    existing: list[dict],
    sandbox_result: dict,
    validation_result: dict,
    validation_status: str,
) -> list[dict]:
    errors = [*existing]

    sandbox_passed = sandbox_result.get(
        "passed",
        sandbox_result.get("exit_code") == 0,
    )

    if not sandbox_result.get("executed") or not sandbox_passed:
        _record_validation_error(
            errors,
            "sandbox",
            sandbox_result.get("error") or "Sandbox execution failed",
        )

    validator_status = validation_result.get(
        "validator_status",
        validation_result.get("status"),
    )

    validator_passed = validation_result.get(
        "validator_passed",
        validation_result.get(
            "passed",
            validation_result.get("valid"),
        ),
    )

    if validation_result.get("error"):
        _record_validation_error(
            errors,
            "validation",
            validation_result["error"],
        )

    elif (
        validator_status in {"failed", "error", "blocked"}
        or validator_passed is False
    ):
        _record_validation_error(
            errors,
            "validation",
            _validation_findings_detail(validation_result),
        )

    return errors


def _previous_validation_candidate(
    attempts: list[dict],
) -> tuple[dict | None, str | None]:
    for attempt in reversed(attempts[:-1]):
        if (
            attempt.get("validation_status") == "passed"
            and isinstance(attempt.get("verified_files"), dict)
        ):
            return attempt, "passed"

        if (
            attempt.get("validation_status") == "needs_review"
            and isinstance(attempt.get("reviewed_files"), dict)
        ):
            return attempt, "needs_review"

    return None, None


def _rollback_validation_candidate(
    state: WorkflowState,
    attempts: list[dict],
    validation_status: str,
    sandbox_result: dict,
    validation_results: dict,
) -> tuple[dict, list[dict], dict, dict, dict, str, dict | None, str | None, str | None]:
    files = state.get("repaired_code") or state.get("project_files", {})
    findings = state.get("reanalysis_findings", [])
    summary = state.get("reanalysis_summary", {})
    best_candidate = state.get("best_repair_candidate")

    if validation_status in {"passed", "needs_review"} or not attempts:
        return (
            files,
            findings,
            summary,
            sandbox_result,
            validation_results,
            validation_status,
            best_candidate,
            None,
            None,
        )

    rejected_status = validation_status

    diagnostic = (
        sandbox_result.get("error")
        or sandbox_result.get("stderr")
        or sandbox_result.get("stdout")
    )

    rollback_reason = (
        validation_results.get("error")
        or diagnostic
        or _validation_findings_detail(validation_results)
    )

    previous, restored_status = _previous_validation_candidate(attempts)

    if previous is None:
        files = state.get("project_files", {})
        findings = state.get("findings", [])
        summary = state.get("quality_summary", {})

    else:
        files_key = (
            "verified_files"
            if restored_status == "passed"
            else "reviewed_files"
        )

        findings_key = (
            "verified_findings"
            if restored_status == "passed"
            else "reviewed_findings"
        )

        summary_key = (
            "verified_quality_summary"
            if restored_status == "passed"
            else "reviewed_quality_summary"
        )

        sandbox_key = (
            "verified_sandbox_result"
            if restored_status == "passed"
            else "reviewed_sandbox_result"
        )

        validation_key = (
            "verified_validation_results"
            if restored_status == "passed"
            else "reviewed_validation_results"
        )

        files = previous[files_key]
        findings = previous.get(
            findings_key,
            state.get("findings", []),
        )
        summary = previous.get(
            summary_key,
            state.get("quality_summary", {}),
        )
        sandbox_result = previous.get(sandbox_key, sandbox_result)
        validation_results = previous.get(
            validation_key,
            validation_results,
        )
        validation_status = restored_status

    best_candidate = {
        "files": files,
        "findings": findings,
        "quality_summary": summary,
        "finding_count": len(findings),
        "quality_score": _quality_score(summary),
        "comparison_available": True,
    }

    return (
        files,
        findings,
        summary,
        sandbox_result,
        validation_results,
        validation_status,
        best_candidate,
        rollback_reason,
        rejected_status,
    )


async def validate(state: WorkflowState) -> dict:
    """Run isolated execution and static validation, keeping their outcomes distinct."""

    files = state.get("repaired_code") or state.get("project_files", {})
    project_id = state["project_id"]
    workflow_id = (
        state.get("run_id")
        or state.get("analysis_id")
        or project_id
    )

    source_paths = source_files(files)
    language = detect_language(list(source_paths))

    project_context = state.get("project_context") or {}
    sandbox_stdin = project_context.get("sandbox_stdin", "")
    test_cases = project_context.get("test_cases") or []

    sandbox_result, validation_result = await asyncio.gather(
        _time_validation_check(
            workflow_id,
            "sandbox",
            _execute_sandbox_validation(
                project_id,
                files,
                workflow_id,
                language,
                sandbox_stdin,
                test_cases,
            ),
        ),
        _time_validation_check(
            workflow_id,
            "validation",
            _execute_validation_agent(
                project_id,
                files,
                state.get("findings", []),
                workflow_id,
                language,
            ),
        ),
    )

    validator_passed = validation_result.get(
        "passed",
        validation_result.get("valid", False),
    )

    validation_status = _validation_outcome(
        sandbox_result,
        validation_result,
    )

    logger.info(
        "workflow_validation_result run_id=%s status=%s sandbox_status=%s sandbox_exit_code=%s validator_status=%s validator_passed=%s",
        workflow_id,
        validation_status,
        sandbox_result.get("status"),
        sandbox_result.get("exit_code"),
        validation_result.get("status"),
        validator_passed,
        extra={
            "workflow_run_id": workflow_id,
            "validation_status": validation_status,
            "sandbox_status": sandbox_result.get("status"),
            "sandbox_exit_code": sandbox_result.get("exit_code"),
            "validator_status": validation_result.get("status"),
        },
    )

    if validation_result.get("error"):
        logger.error(
            "workflow_validation_error run_id=%s detail=%s",
            workflow_id,
            validation_result["error"],
            extra={
                "workflow_run_id": workflow_id,
                "validation_status": validation_status,
            },
        )

    validation_results = {
        **validation_result,
        "validator_passed": validator_passed,
        "validator_status": validation_result.get("status"),
        "passed": validation_status == "passed",
        "status": validation_status,
    }

    agent_errors = _validation_agent_errors(
        state.get("agent_errors", []),
        sandbox_result,
        validation_results,
        validation_status,
    )

    repair_attempts = list(state.get("repair_attempts", []))

    (
        selected_files,
        selected_findings,
        selected_summary,
        selected_sandbox,
        selected_validation,
        validation_status,
        selected_best_candidate,
        rollback_reason,
        rejected_candidate_validation_status,
    ) = _rollback_validation_candidate(
        state,
        repair_attempts,
        validation_status,
        sandbox_result,
        validation_results,
    )

    if rollback_reason and repair_attempts:
        repair_attempts[-1] = {
            **repair_attempts[-1],
            "candidate_rejected": True,
            "candidate_rejection_reason": rollback_reason,
            "candidate_rollback": True,
            "candidate_validation_status": rejected_candidate_validation_status,
            "candidate_sandbox_result": sandbox_result,
            "candidate_validation_results": validation_results,
        }

    repair_attempts = _mark_repair_attempts(
        repair_attempts,
        validation_status,
        selected_files,
        selected_findings,
        selected_summary,
        selected_sandbox,
        selected_validation,
    )

    return {
        "repaired_code": selected_files,
        "reanalysis_findings": selected_findings,
        "reanalysis_summary": selected_summary,
        "best_repair_candidate": selected_best_candidate,
        "sandbox_result": selected_sandbox,
        "validation_results": selected_validation,
        "validation_status": validation_status,
        "repair_attempts": repair_attempts,
        "agent_errors": agent_errors,
        "workflow_trace": [
            *state.get("workflow_trace", []),
            "Validation completed",
        ],
        "stage_history": [
            *state.get("stage_history", []),
            {
                "stage": "validate",
                "status": validation_status,
            },
        ],
    }


def _initial_reanalysis_candidate(state: WorkflowState) -> dict:
    candidate = state.get("best_repair_candidate")

    if candidate is not None:
        return candidate

    baseline_available = (
        "findings" in state
        or "quality_summary" in state
    )

    summary = state.get("quality_summary", {})
    findings = state.get("findings", [])

    return {
        "files": state.get("project_files", {}),
        "findings": findings,
        "quality_summary": summary,
        "finding_count": len(findings),
        "quality_score": _quality_score(summary),
        "comparison_available": baseline_available,
    }

def _initial_reanalysis_candidate(state: WorkflowState) -> dict:
    candidate = state.get("best_repair_candidate")
    if candidate is not None:
        return candidate
    baseline_available = "findings" in state or "quality_summary" in state
    summary = state.get("quality_summary", {})
    findings = state.get("findings", [])
    return {
        "files": state.get("project_files", {}),
        "findings": findings,
        "quality_summary": summary,
        "finding_count": len(findings),
        "quality_score": _quality_score(summary),
        "comparison_available": baseline_available,
    }


def _serious_security_findings(findings: list[dict]) -> Counter:
    return Counter(
        _finding_base_identity(finding)
        for finding in findings
        if str(finding.get("category", "")).lower() == "security"
        and str(finding.get("severity", "")).lower() in {"critical", "high"}
    )


def _reanalysis_regression_reasons(
    findings: list[dict],
    candidate_score: int | float | None,
    best_candidate: dict,
    analysis_available: bool,
    errors: list[dict],
    skipped_tools: set[str],
) -> list[str]:
    if errors:
        return ["Candidate was not promoted because re-analysis did not complete successfully."]
    if skipped_tools:
        return [
            "Candidate was not promoted because selected analyzers were unavailable: "
            + ", ".join(sorted(skipped_tools))
            + "."
        ]
    reasons = []
    if not analysis_available or not best_candidate.get("comparison_available", True):
        return reasons
    if len(findings) > best_candidate["finding_count"]:
        reasons.append(
            f"Candidate increased findings from {best_candidate['finding_count']} to {len(findings)}."
        )
    prior_security = _serious_security_findings(best_candidate.get("findings", []))
    current_security = _serious_security_findings(findings)
    if any(count > prior_security[identity] for identity, count in current_security.items()):
        reasons.append("Candidate introduced a new critical or high-severity security finding.")
    best_score = best_candidate.get("quality_score")
    if best_score is not None and candidate_score is not None and candidate_score < best_score:
        reasons.append(f"Candidate lowered quality score from {best_score:g} to {candidate_score:g}.")
    return reasons


def _update_reanalysis_attempt(
    attempts: list[dict],
    rollback: bool,
    reasons: list[str],
    analysis_available: bool,
    findings: list[dict],
    candidate_score: int | float | None,
    quality_summary: dict,
) -> list[dict]:
    if not attempts:
        return attempts
    attempt = attempts[-1]
    changed = bool(attempt.get("changed_files"))
    if rollback:
        candidate_status = "rejected"
    elif analysis_available and changed:
        candidate_status = "accepted"
    else:
        candidate_status = attempt.get("candidate_status", "unverified")
    attempt_update = {
        **attempt,
        "regression_rollback": rollback,
        "regression_reasons": reasons,
        "candidate_rejected": rollback,
        "candidate_rollback": rollback,
        "candidate_status": candidate_status,
        "candidate_findings_after": len(findings),
        "candidate_quality_score": candidate_score,
        "candidate_findings": findings,
        "candidate_quality_summary": quality_summary,
    }
    if analysis_available and not rollback and changed:
        attempt_update["candidate_acceptance_reason"] = "Re-analysis completed without detected regressions."
    if rollback:
        attempt_update["candidate_rejection_reason"] = "; ".join(reasons)
    attempts[-1] = attempt_update
    return attempts


async def reanalyze(state: WorkflowState) -> dict:
    """Re-run enabled analysis agents against the repair candidate."""
    files = state.get("repaired_code") or state.get("project_files", {})
    context = state.get("project_context", {})
    flags = _analysis_agent_flags(_analysis_checks(context))
    _log_analysis_endpoints(state, "reanalyze", flags, len(source_files(files)))
    try:
        responses = await _collect_analysis_responses(
            state["project_id"],
            files,
            context,
            state.get("tools"),
            *flags,
            state.get("run_id") or state.get("analysis_id") or state["project_id"],
        )
    except Exception as exc:  # noqa: BLE001 - preserve re-analysis evidence
        message = f"re-analysis failed: {exc}"
        responses = {
            agent: {"findings": [], "error": message}
            for agent, enabled in zip(("security", "code_quality", "bug_resource"), flags)
            if enabled
        }

    reanalysis_errors = _collect_analysis_errors(responses)
    agent_errors = [
        *state.get("agent_errors", []),
        *(
            {"agent": f"{error['agent']}_reanalysis", "error": error["error"]}
            for error in reanalysis_errors
        ),
    ]
    security_result = responses.get("security", {"findings": []})
    quality_result = responses.get("code_quality", {"findings": []})
    resource_result = responses.get("bug_resource", {"findings": []})
    findings = aggregate(
        security_result.get("findings", []),
        quality_result.get("findings", []),
        resource_result.get("findings", []),
    )
    quality_summary = quality_result.get("quality_summary", {})
    best_candidate = _initial_reanalysis_candidate(state)
    candidate_score = _quality_score(quality_summary)
    skipped_tools = _skipped_quality_tools(
        quality_summary, state.get("tools") or quality_summary.get("selected_tools")
    )
    analysis_available = bool(any(flags)) and not reanalysis_errors and not skipped_tools
    regression_reasons = _reanalysis_regression_reasons(
        findings, candidate_score, best_candidate, analysis_available, reanalysis_errors, skipped_tools
    )
    regression_rollback = bool(regression_reasons)
    if regression_rollback:
        selected_files = best_candidate["files"]
        selected_findings = best_candidate["findings"]
        selected_summary = best_candidate["quality_summary"]
    else:
        selected_files = files
        selected_findings = findings
        selected_summary = quality_summary
        if analysis_available:
            best_candidate = {
                "files": files,
                "findings": findings,
                "quality_summary": quality_summary,
                "finding_count": len(findings),
                "quality_score": candidate_score,
                "comparison_available": True,
            }

    repair_attempts = _update_reanalysis_attempt(
        list(state.get("repair_attempts", [])),
        regression_rollback,
        regression_reasons,
        analysis_available,
        findings,
        candidate_score,
        quality_summary,
    )
    agent_statuses = _analysis_agent_statuses(*flags, reanalysis_errors)
    if not any(flags):
        reanalysis_status = "skipped"
    elif reanalysis_errors:
        reanalysis_status = "partial"
    else:
        reanalysis_status = "completed"
    return {
        "repaired_code": selected_files,
        "reanalysis_findings": selected_findings,
        "reanalysis_summary": selected_summary,
        "best_repair_candidate": best_candidate,
        "repair_attempts": repair_attempts,
        "agent_errors": agent_errors,
        "workflow_trace": [*state.get("workflow_trace", []), "Re-analysis completed"],
        "stage_history": [*state.get("stage_history", []), {
            "stage": "reanalyze",
            "status": reanalysis_status,
            "finding_count": len(findings),
            "regression_rollback": regression_rollback,
            "regression_reasons": regression_reasons,
            "agent_errors": reanalysis_errors,
            "agents": agent_statuses,
        }],
    }


def _quality_score(summary: dict) -> int | float | None:
    for key in ("overall_score", "quality_score"):
        score = summary.get(key)
        if isinstance(score, (int, float)) and not isinstance(score, bool):
            return score
    return None


def _skipped_quality_tools(summary: dict, selected_tools: list[str] | None = None) -> set[str]:
    selected = {str(tool).lower() for tool in selected_tools or []}
    return {
        str(run.get("tool") or "unknown analyzer")
        for run in summary.get("tool_runs", [])
        if isinstance(run, dict)
        and run.get("status") in {"skipped", "not_run", "unavailable", "unsupported"}
        and (not selected or str(run.get("tool") or "").lower() in selected)
    }


def _finding_agent(finding: dict) -> str:
    category = str(finding.get("category", "")).lower()
    tool = str(finding.get("tool", "")).lower()
    if category == "security" or tool in {"bandit", "semgrep", "gitleaks"}:
        return "security"
    if category in {"bug", "resource"} or "resource" in category:
        return "bug_resource"
    return "quality"


def _finding_identity(finding: dict) -> tuple:
    return _finding_base_identity(finding)


def _finding_locationless_identity(finding: dict) -> tuple:
    identity = _finding_base_identity(finding)
    return identity[:4] + identity[6:]


def _finding_symbol_identity(finding: dict) -> tuple | None:
    issue = str(finding.get("issue") or "").strip().lower()
    if issue not in {"language_analyzer", "language_analyzer_failed"}:
        return None
    message = str(finding.get("description") or finding.get("message") or "")
    match = re.search(r"""['"`‘’“”]([A-Za-z_$][\w$]*)['"`‘’“”]""", message)
    if not match:
        return None
    identity = _finding_base_identity(finding)
    return identity[:3] + (identity[3], "symbol", match.group(1).lower())


def _dedupe_findings(findings: list[dict]) -> list[dict]:
    unique = []
    seen = set()
    for finding in findings:
        key = (
            *_finding_identity(finding),
            finding.get("line_number", finding.get("line")),
            " ".join(str(finding.get("evidence") or "").strip().lower().split()),
        )
        if key not in seen:
            unique.append(finding)
            seen.add(key)
    return unique


def _finding_base_identity(finding: dict) -> tuple:
    issue = str(finding.get("issue") or "").strip().lower()
    rule_id = str(
        finding.get("rule_id")
        or finding.get("code")
        or issue
    ).strip().lower()
    evidence = str(finding.get("evidence") or "").strip()
    rule = re.search(r"\b[A-Z][A-Z0-9_-]{1,30}\b", rule_id, re.IGNORECASE)
    if not rule:
        rule = re.search(r"\b[A-Z][A-Z0-9_-]{1,30}\b", evidence, re.IGNORECASE)
    identity = rule.group(0).lower() if rule else rule_id or issue
    return (
        str(finding.get("category") or "").lower(),
        str(finding.get("tool") or "").lower(),
        str(finding.get("file") or "").replace("\\", "/").lower(),
        identity,
        finding.get("line_number", finding.get("line")),
        finding.get("column"),
        " ".join(
            str(finding.get("description") or finding.get("message") or "").strip().lower().split()
        ),
    )


def _stage_by_name(history: list[dict], name: str) -> dict:
    return next((stage for stage in reversed(history) if stage.get("stage") == name), {})


def _agent_status_sets(*stages: dict) -> tuple[set[str], set[str]]:
    failed = set()
    skipped = set()
    for stage in stages:
        for agent, status in stage.get("agents", {}).items():
            if status in {"failed", "blocked", "unavailable", "unsupported"}:
                failed.add(agent)
            elif status in {"skipped", "not_run"}:
                skipped.add(agent)
    return failed, skipped


def _comparison_skipped_tools(state: WorkflowState) -> set[str]:
    skipped = set()
    for summary in (state.get("quality_summary") or {}, state.get("reanalysis_summary") or {}):
        skipped.update(
            _skipped_quality_tools(summary, state.get("tools") or summary.get("selected_tools"))
        )
    for attempt in state.get("repair_attempts", []):
        skipped.update(str(check) for check in attempt.get("skipped_checks", []))
    return skipped


def _partition_findings(
    findings: list[dict],
    failed_agents: set[str],
    skipped_agents: set[str],
) -> tuple[list[dict], list[dict], list[dict]]:
    failed = [item for item in findings if _finding_agent(item) in failed_agents]
    skipped = [item for item in findings if _finding_agent(item) in skipped_agents]
    unavailable = failed_agents | skipped_agents
    comparable = [item for item in findings if _finding_agent(item) not in unavailable]
    return failed, skipped, comparable


def _match_findings(before: list[dict], after: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    indexes: dict[object, dict[tuple, list[int]]] = {
        _finding_identity: {},
        _finding_locationless_identity: {},
        _finding_symbol_identity: {},
    }
    for index, finding in enumerate(after):
        for identity_for, mapping in indexes.items():
            identity = identity_for(finding)
            if identity is not None:
                mapping.setdefault(identity, []).append(index)

    matched_before: set[int] = set()
    matched_after: set[int] = set()
    for identity_for, matches_by_identity in indexes.items():
        for before_index, finding in enumerate(before):
            if before_index in matched_before:
                continue
            identity = identity_for(finding)
            if identity is None:
                continue
            match = next(
                (index for index in matches_by_identity.get(identity, []) if index not in matched_after),
                None,
            )
            if match is not None:
                matched_before.add(before_index)
                matched_after.add(match)

    unchanged = [item for index, item in enumerate(before) if index in matched_before]
    resolved = [item for index, item in enumerate(before) if index not in matched_before]
    introduced = [item for index, item in enumerate(after) if index not in matched_after]
    return unchanged, resolved, introduced


def _comparison_is_complete(
    failed_agents: set[str],
    skipped_before: list[dict],
    skipped_after: list[dict],
    skipped_tools: set[str],
    analysis_stage: dict,
    reanalysis_stage: dict,
) -> bool:
    return (
        not failed_agents
        and not skipped_before
        and not skipped_after
        and not skipped_tools
        and analysis_stage.get("status") == "completed"
        and reanalysis_stage.get("status") == "completed"
    )


def _build_findings_comparison(
    before: list[dict],
    after: list[dict],
    unchanged: list[dict],
    resolved: list[dict],
    introduced: list[dict],
    skipped_agents: set[str],
    skipped_tools: set[str],
    unverified_before: list[dict],
    unverified_after: list[dict],
    skipped_before: list[dict],
    skipped_after: list[dict],
    changed_files: list[str],
    complete: bool,
    reanalysis_stage: dict,
) -> dict:
    unverified = unverified_after or unverified_before
    skipped = skipped_after or skipped_before
    if complete:
        comparison_status = "complete"
    elif reanalysis_stage.get("status") == "skipped":
        comparison_status = "not_run"
    else:
        comparison_status = "partial"
    findings_after = len(after) if complete else None
    findings_remaining = [*unchanged, *introduced, *unverified, *skipped]
    skipped_agent_checks = [
        agent for agent in skipped_agents
        if any(_finding_agent(finding) == agent for finding in [*before, *after])
    ]
    return {
        "status": comparison_status,
        "findings_before": len(before),
        "findings_after": findings_after,
        "resolved_count": len(resolved),
        "unchanged_count": len(unchanged),
        "new_count": len(introduced),
        "unverified_count": max(len(unverified_before), len(unverified_after)),
        "skipped_checks": sorted([*skipped_agent_checks, *skipped_tools]),
        "skipped_findings": skipped,
        "remaining_original_count": len(unchanged) + len(unverified_before) + len(skipped_before),
        "unverified_before_count": len(unverified_before),
        "unverified_after_count": len(unverified_after),
        "resolved_findings": resolved,
        "unchanged_findings": unchanged,
        "new_findings": introduced,
        "unverified_findings": [*unverified_before, *unverified_after],
        "remaining_findings": findings_remaining,
        "resolved_issues": sorted({finding.get("issue", "Unknown issue") for finding in resolved}),
        "remaining_issues": sorted({finding.get("issue", "Unknown issue") for finding in findings_remaining}),
        "unverified_before_findings": unverified_before,
        "unverified_after_findings": unverified_after,
        "skipped_before_findings": skipped_before,
        "skipped_after_findings": skipped_after,
        "changed_files": changed_files,
        "improved": complete and len(after) < len(before),
    }


def _final_repair_status(
    attempt: dict,
    comparison_complete: bool,
    unchanged: list[dict],
    introduced: list[dict],
    resolved: list[dict],
) -> str:
    if not comparison_complete:
        return "needs_review"
    if not unchanged and not introduced:
        return "success"
    if resolved:
        return "partial"
    if attempt.get("candidate_rollback") or not attempt.get("changed_files"):
        return "failed"
    return "needs_review"


def _update_comparison_attempt(
    attempts: list[dict],
    comparison: dict,
    before: list[dict],
    after: list[dict],
    complete: bool,
    unchanged: list[dict],
    resolved: list[dict],
    introduced: list[dict],
    unverified_before: list[dict],
    skipped_before: list[dict],
    unverified_findings: list[dict],
    skipped_findings: list[dict],
) -> list[dict]:
    if not attempts:
        return []
    attempt = attempts[-1]
    final_status = _final_repair_status(attempt, complete, unchanged, introduced, resolved)
    findings_after = len(after) if complete else None
    if attempt.get("candidate_rollback"):
        candidate_status = "rejected"
    elif attempt.get("changed_files") and complete:
        candidate_status = "accepted"
    elif not attempt.get("changed_files"):
        candidate_status = "no_change"
    else:
        candidate_status = "unverified"
    attempts[-1] = {
        **attempt,
        "repair_status": final_status,
        "candidate_status": candidate_status,
        "verification_status": "verified" if complete else "needs_review",
        "verified_resolved_findings": resolved,
        "remaining_findings": [*unchanged, *unverified_before, *skipped_before],
        "introduced_findings": introduced,
        "unverified_findings": unverified_findings,
        "skipped_findings": skipped_findings,
        "skipped_checks": comparison["skipped_checks"],
    }
    outcome = "improved" if comparison["improved"] else "needs_review"
    attempts[-1] = {
        **attempts[-1],
        "findings_before": len(before),
        "findings_after": findings_after,
        "quality_improved": comparison["improved"],
        "final_outcome": outcome,
    }
    return attempts


def compare_results(state: WorkflowState) -> dict:
    """Compare findings without collapsing repeated rules or masking failed agents."""
    before = _dedupe_findings(state.get("findings", []))
    after = _dedupe_findings(state.get("reanalysis_findings", []))
    history = state.get("stage_history", [])
    reanalysis_stage = _stage_by_name(history, "reanalyze")
    analysis_stage = _stage_by_name(history, "analyze")
    failed_agents, skipped_agents = _agent_status_sets(analysis_stage, reanalysis_stage)
    skipped_tools = _comparison_skipped_tools(state)
    unverified_before, skipped_before, comparable_before = _partition_findings(
        before, failed_agents, skipped_agents
    )
    unverified_after, skipped_after, comparable_after = _partition_findings(
        after, failed_agents, skipped_agents
    )
    unchanged, resolved, introduced = _match_findings(comparable_before, comparable_after)
    comparison_complete = _comparison_is_complete(
        failed_agents, skipped_before, skipped_after, skipped_tools, analysis_stage, reanalysis_stage
    )
    changed_files = [
        path for path, content in (state.get("repaired_code") or {}).items()
        if content != state.get("project_files", {}).get(path)
    ]
    comparison = _build_findings_comparison(
        before, after, unchanged, resolved, introduced,
        skipped_agents, skipped_tools,
        unverified_before, unverified_after, skipped_before, skipped_after,
        changed_files, comparison_complete, reanalysis_stage,
    )
    quality_summary = state.get("quality_summary") or {}
    reanalysis_summary = state.get("reanalysis_summary") or {}
    before_score = quality_summary.get("overall_score", quality_summary.get("quality_score"))
    after_score = reanalysis_summary.get("overall_score", reanalysis_summary.get("quality_score"))
    if before_score is not None:
        comparison["quality_score_before"] = before_score
    if comparison_complete and after_score is not None:
        comparison["quality_score_after"] = after_score
    unverified_findings = unverified_after or unverified_before
    skipped_findings = skipped_after or skipped_before
    attempts = _update_comparison_attempt(
        list(state.get("repair_attempts", [])),
        comparison,
        before,
        after,
        comparison_complete,
        unchanged,
        resolved,
        introduced,
        unverified_before,
        skipped_before,
        unverified_findings,
        skipped_findings,
    )
    return {
        "comparison": comparison,
        "repair_attempts": attempts,
        "workflow_trace": [*state.get("workflow_trace", []), "Before and after results compared"],
        "stage_history": [*state.get("stage_history", []), {
            "stage": "compare", "status": "completed", "improved": comparison["improved"]
        }],
    }


def bump_retry(state: WorkflowState) -> dict:
    return {"retry_count": state.get("retry_count", 0) + 1}


def finalize(state: WorkflowState) -> dict:
    """Builds the report returned to the frontend, whether validation
    ultimately passed or the retry budget was exhausted."""
    checks = _analysis_checks(state.get("project_context"))
    stage_history = list(state.get("stage_history", []))
    stage_history.extend(_skipped_stages(checks, stage_history))
    stage_history.append({"stage": "finalize", "status": "completed"})
    report_state = {**state, "stage_history": stage_history}
    final_report = build_report(report_state)
    return {
        "final_code": state.get("repaired_code") or state.get("project_files", {}),
        "validation_status": final_report["validation_status"],
        "workflow_trace": [*state.get("workflow_trace", []), "Workflow report finalized"],
        "final_report": final_report,
        "stage_history": stage_history,
    }


def should_retry(state: WorkflowState) -> str:
    """Conditional-edge function: routes to 'finalize' on pass, back to
    'repair' on fail (up to max_retries), otherwise 'finalize' with a
    failed status so the graph always terminates."""
    validation_status = state.get("validation_status")
    if validation_status == "inconclusive":
        return "finalize"
    checks = _analysis_checks(state.get("project_context"))
    if checks is not None and not checks["automated_repair"]:
        return "finalize"
    repair_attempts = state.get("repair_attempts", [])
    last_attempt = repair_attempts[-1] if repair_attempts else {}
    last_rejected = {
        (item.get("path"), item.get("code"))
        for item in last_attempt.get("rejected_candidates", [])
        if item.get("decision") == "rejected" and item.get("code") is not None
    }
    if last_rejected and any(
        last_rejected & {
            (item.get("path"), item.get("code"))
            for item in attempt.get("rejected_candidates", [])
            if item.get("decision") == "rejected" and item.get("code") is not None
        }
        for attempt in repair_attempts[:-1]
    ):
        return "finalize"
    if (
        len(repair_attempts) > 1
        and not last_attempt.get("changed_files")
        and last_attempt.get("error")
        and any(
            not attempt.get("changed_files")
            and attempt.get("error") == last_attempt.get("error")
            and Counter(_finding_identity(finding) for finding in attempt.get("input_findings", []))
            == Counter(_finding_identity(finding) for finding in last_attempt.get("input_findings", []))
            for attempt in repair_attempts[:-1]
        )
    ):
        return "finalize"
    last_fingerprint = last_attempt.get("candidate_fingerprint")
    if last_fingerprint and any(
        attempt.get("candidate_fingerprint") == last_fingerprint and attempt.get("changed_files")
        for attempt in repair_attempts[:-1]
    ):
        return "finalize"
    if validation_status in {"passed", "needs_review", "blocked"}:
        reanalysis_stage = next(
            (stage for stage in reversed(state.get("stage_history", [])) if stage.get("stage") == "reanalyze"),
            {},
        )
        previous_findings = last_attempt.get("input_findings")
        remaining_findings = state.get("reanalysis_findings", [])
        repair_error = last_attempt.get("error") or ""
        repair_needs_revision = repair_error.startswith((
            REPAIR_REJECTED_PREFIX,
            "Repair made no source changes",
            "Model response",
            "Ollama returned a response that was not valid JSON.",
        ))
        if (
            validation_status == "passed"
            and repair_needs_revision
            and remaining_findings
            and state.get("retry_count", 0) < settings.max_retries
        ):
            return "retry"
        no_finding_progress = (
            len(repair_attempts) > 1
            and previous_findings is not None
            and Counter(_finding_identity(finding) for finding in previous_findings)
            == Counter(_finding_identity(finding) for finding in remaining_findings)
        )
        if (
            remaining_findings
            and reanalysis_stage.get("status") == "completed"
            and last_attempt.get("changed_files")
            and not no_finding_progress
            and state.get("retry_count", 0) < settings.max_retries
        ):
            return "retry"
        if validation_status == "passed":
            return "finalize"
    repair_error = last_attempt.get("error") or ""
    repair_needs_revision = repair_error.startswith((
        REPAIR_REJECTED_PREFIX,
        "Repair made no source changes",
        "Model response",
        "Ollama returned a response that was not valid JSON.",
    ))
    if validation_status == "needs_review" and not repair_needs_revision:
        return "finalize"
    sandbox_result = state.get("sandbox_result", {})
    validation_results = state.get("validation_results", {})
    if sandbox_result and not sandbox_result.get("executed"):
        return "finalize"
    if validation_results.get("error"):
        return "finalize"
    if repair_attempts:
        if last_attempt.get("error") and not last_attempt.get("changed_files"):
            if not repair_needs_revision:
                return "finalize"
    if state.get("retry_count", 0) >= settings.max_retries:
        return "finalize"
    return "retry"
