import json
import logging
import os
import re
from pathlib import PurePosixPath
from time import perf_counter

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import httpx

from app.analyzers.ruff_analyzer import run_ruff
from app.analyzers.pylint_analyzer import run_pylint
from app.analyzers.radon_analyzer import run_radon
from app.analyzers.mypy_analyzer import PartialMypyAnalysisError, run_mypy
from app.parsers.result_normalizer import normalize
from app.schemas.finding import AnalyzeRequest
from app.config import settings
from shared.languages import detect_language, is_project_manifest, is_source_path
from shared.schemas.finding import FindingsResponse

app = FastAPI(title="Code Quality Service", version="1.0.0")
logger = logging.getLogger(__name__)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _build_gate_thresholds() -> dict:
    return {
        "error_limit": settings.quality_gate_error_threshold,
        "warning_limit": settings.quality_gate_warning_threshold,
        "complexity_limit": settings.quality_gate_complexity_threshold,
        "minimum_score": settings.quality_gate_min_score,
    }


def _quality_summary(files: dict[str, str], findings: list) -> dict:
    by_tool = {"ruff": 0, "pylint": 0, "radon": 0, "mypy": 0}
    by_severity = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
    complex_functions = 0
    style_issues = 0
    by_category: dict[str, int] = {}
    for finding in findings:
        tool = finding.tool or "unknown"
        if tool in by_tool:
            by_tool[tool] += 1
        severity = finding.severity.value if hasattr(finding.severity, "value") else finding.severity
        if severity in by_severity:
            by_severity[severity] += 1
        if finding.issue.startswith("COMPLEXITY"):
            complex_functions += 1
        category = finding.category.value if hasattr(finding.category, "value") else str(finding.category)
        by_category[category] = by_category.get(category, 0) + 1
        subcategory = finding.subcategory.value if hasattr(finding.subcategory, "value") else str(finding.subcategory or "")
        if subcategory in {"style", "convention"}:
            style_issues += 1

    errors = by_severity["critical"] + by_severity["high"]
    warnings = by_severity["medium"]
    info = by_severity["low"] + by_severity["info"]
    overall_score = max(0, 100 - (errors * 12 + warnings * 5 + info * 1 + complex_functions * 4))
    thresholds = _build_gate_thresholds()
    gate_status = "PASS" if (
        errors <= thresholds["error_limit"]
        and warnings <= thresholds["warning_limit"]
        and complex_functions <= thresholds["complexity_limit"]
        and overall_score >= thresholds["minimum_score"]
    ) else "FAIL"

    return {
        "files_analyzed": len(files),
        "total_findings": len(findings),
        "errors": errors,
        "warnings": warnings,
        "info": info,
        "complex_functions": complex_functions,
        "complexity_issues": complex_functions,
        "style_issues": style_issues,
        "by_category": by_category,
        "overall_score": overall_score,
        "quality_gate_status": gate_status,
        "quality_gate": {
            "status": gate_status,
            "overall_score": overall_score,
            "thresholds": thresholds,
        },
        "by_tool": by_tool,
        "by_severity": by_severity,
        "maintainability": {
            "complexity_issues": complex_functions,
            "high_risk_items": max(0, errors + warnings),
            "measured_values": {
                "errors": errors,
                "warnings": warnings,
                "info_items": info,
                "complex_functions": complex_functions,
            },
        },
    }


def _quality_score(summary: dict) -> int:
    """Return a transparent score for comparing runs, not a pass/fail verdict."""
    return int(summary.get("overall_score", 0))


def _detect_hotspots(raw_issues: list[dict]) -> list[dict]:
    hotspots: list[dict] = []
    for issue in raw_issues:
        code = issue.get("code") or ""
        if not code.startswith("COMPLEXITY"):
            continue
        complexity = 0
        label = code.replace("COMPLEXITY_", "")
        complexity_map = {"A": 5, "B": 10, "C": 15, "D": 20, "E": 27, "F": 40}
        complexity = complexity_map.get(label, 0)
        if complexity >= 20:
            risk_level = "High"
        elif complexity >= 10:
            risk_level = "Medium"
        else:
            risk_level = "Low"
        hotspots.append({
            "function_name": issue.get("function_name") or issue.get("message", "complex_function").split("'")[1] if "'" in (issue.get("message") or "") else "complex_function",
            "file": issue.get("file"),
            "line": issue.get("line_number"),
            "complexity": complexity,
            "risk_level": risk_level,
            "source": issue.get("source"),
        })
    hotspots.sort(key=lambda entry: (-entry["complexity"], entry["file"] or "", entry["line"] or 0))
    return hotspots[:10]


def per_file_summary(files: dict[str, str], findings: list) -> dict:
    def new_file_summary() -> dict:
        return {"errors": 0, "warnings": 0, "complexity": "Low", "issues": 0}

    grouped = {path: new_file_summary() for path in files}
    for finding in findings:
        file_summary = grouped.setdefault(finding.file, new_file_summary())
        file_summary["issues"] += 1
        severity = finding.severity.value if hasattr(finding.severity, "value") else str(finding.severity)
        file_summary["errors"] += severity in {"high", "critical"}
        file_summary["warnings"] += severity == "medium"
        if finding.issue.startswith("COMPLEXITY"):
            file_summary["complexity"] = "High"

    for file_summary in grouped.values():
        if file_summary["warnings"] >= 3:
            file_summary["complexity"] = "High"
        elif file_summary["errors"] > 0:
            file_summary["complexity"] = "Medium"
    return grouped


def _run_language_analyzer(project_id: str, files: dict[str, str], language: str) -> tuple[list[dict], dict]:
    started = perf_counter()
    sandbox_url = os.getenv("QUALITY_SANDBOX_SERVICE_URL")
    if not sandbox_url:
        raise RuntimeError("Language analyzer sandbox is not configured.")
    source_paths = [path for path in files if is_source_path(path)]
    if not source_paths:
        raise RuntimeError("No supported source files were supplied to the language analyzer.")
    entry = next(
        (path for path in source_paths if path.rsplit("/", 1)[-1].lower() in {"main.java", "main.c", "main.cpp", "main.go", "program.cs"}),
        source_paths[0],
    )
    try:
        response = httpx.post(
            f"{sandbox_url.rstrip('/')}/execute",
            json={
                "project_id": project_id,
                "filename": entry,
                "code": files[entry],
                "files": files,
                "analysis_only": True,
                "timeout": 180,
            },
            timeout=210,
        )
        response.raise_for_status()
        result = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise RuntimeError(f"Language analyzer service request failed: {type(exc).__name__}.") from exc
    if not isinstance(result, dict) or not result.get("executed"):
        detail = result.get("error") if isinstance(result, dict) else None
        raise RuntimeError(str(detail or "Language analyzer did not execute."))
    if result.get("status") in {"unavailable", "unsupported_project", "dependency_missing"}:
        raise RuntimeError(str(result.get("error") or result.get("stderr") or "Language analyzer is unavailable."))

    output = "\n".join(part for part in (result.get("stdout"), result.get("stderr")) if part).strip()
    tool = result.get("validator") or "language_compiler"
    stderr = result.get("stderr") or ""
    if re.search(r"(not found|no such file or directory)", stderr, re.IGNORECASE):
        raise RuntimeError(f"Required language analyzer is unavailable: {tool}.")
    raw_issues = []
    normalized_source_paths = {
        path.replace("\\", "/"): path for path in source_paths
    }
    source_paths_by_name: dict[str, list[str]] = {}
    for path in source_paths:
        source_paths_by_name.setdefault(path.rsplit("/", 1)[-1], []).append(path)
    for normalized_line in _language_diagnostic_lines(output):
        if normalized_line.startswith("CODE_VALIDATION_SPOTBUGS:"):
            try:
                spotbugs_result = json.loads(normalized_line.removeprefix("CODE_VALIDATION_SPOTBUGS:"))
            except json.JSONDecodeError:
                raw_issues.append({
                    "source": "spotbugs",
                    "file": entry,
                    "line_number": None,
                    "code": "SPOTBUGS_INVALID_OUTPUT",
                    "message": "SpotBugs returned malformed structured diagnostics.",
                    "severity_hint": "high",
                })
                continue
            for item in spotbugs_result.get("findings", []):
                reported_path = str(item.get("file") or "")
                normalized_path = reported_path.replace("\\", "/")
                matched_path = normalized_source_paths.get(normalized_path)
                if matched_path is None and reported_path:
                    same_name = source_paths_by_name.get(PurePosixPath(normalized_path).name, [])
                    matched_path = same_name[0] if len(same_name) == 1 else reported_path
                raw_issues.append({
                    "source": "spotbugs",
                    "file": matched_path or entry,
                    "line_number": item.get("line"),
                    "code": item.get("rule_id") or item.get("type") or "SPOTBUGS",
                    "message": item.get("message") or item.get("type") or "SpotBugs finding",
                    "severity_hint": item.get("severity", "medium"),
                    "evidence": {
                        "priority": item.get("priority"),
                        "class": item.get("class"),
                        "method": item.get("method"),
                        "source_file": reported_path or None,
                    },
                })
            if spotbugs_result.get("error"):
                raw_issues.append({
                    "source": "spotbugs",
                    "file": entry,
                    "line_number": None,
                    "code": "SPOTBUGS_ANALYZER_ERROR",
                    "message": spotbugs_result["error"],
                    "severity_hint": "high",
                })
            continue
        if "cppcheck" in tool.lower() and not re.match(
            r"^.+?:\d+:\d+:\s*(?:error|warning|style|performance|portability|information):",
            normalized_line,
            re.IGNORECASE,
        ):
            continue
        dotnet_match = re.match(
            r"^(.*?)\((\d+),\d+\):\s*(warning|error)\s+([A-Z]+\d+):\s*(.*)$",
            normalized_line,
            re.IGNORECASE,
        )
        match = re.match(r"^(.*?):(\d+)(?::(\d+))?:\s*(.*)$", normalized_line)
        diagnostic_path = (
            dotnet_match.group(1).replace("\\", "/")
            if dotnet_match
            else match.group(1).replace("\\", "/")
            if match
            else entry
        )
        matched_path = normalized_source_paths.get(diagnostic_path)
        if matched_path is None and (match or dotnet_match):
            filename_matches = source_paths_by_name.get(diagnostic_path.rsplit("/", 1)[-1], [])
            if len(filename_matches) == 1:
                matched_path = filename_matches[0]
        if dotnet_match:
            line_number = int(dotnet_match.group(2))
            diagnostic = f"[{dotnet_match.group(4)}] {dotnet_match.group(5)}"
        else:
            line_number = int(match.group(2)) if match else None
            diagnostic = match.group(4) if match else normalized_line
        rule_match = re.match(r"^\[([^\]]+)\]\s*(.*)$", diagnostic)
        raw_issues.append({
            "source": tool,
            "file": matched_path or entry,
            "line_number": line_number,
            "code": rule_match.group(1) if rule_match else "LANGUAGE_ANALYZER",
            "message": rule_match.group(2) if rule_match else diagnostic,
            "severity_hint": (
                "high"
                if re.search(r"\b(error|fatal)\b", normalized_line, re.IGNORECASE)
                else "medium"
            ),
        })
    if result.get("status") != "completed" and not raw_issues:
        raw_issues.append({
            "source": tool,
            "file": entry,
            "line_number": None,
            "code": "LANGUAGE_ANALYZER_FAILED",
            "message": str(result.get("stderr") or result.get("error") or "Language analysis failed."),
            "severity_hint": "high",
        })
    return raw_issues, {
        "tool": tool,
        "status": "completed" if result.get("status") == "completed" else "findings",
        "finding_count": len(raw_issues),
        "duration_ms": round((perf_counter() - started) * 1000, 2),
    }


def _language_diagnostic_lines(output: str) -> list[str]:
    ignored_exact = {"Starting audit...", "Audit done.", "[]", "Build succeeded."}
    ignored_prefixes = (
        "Picked up JAVA_TOOL_OPTIONS:",
        "[warning] /usr/bin/checkstyle: Unable to locate commons-cli",
        "An issue was encountered verifying workloads.",
    )
    ignored_fragments = ("Progressbar rendering conflicts with reporting to STDOUT.",)
    diagnostics = []
    seen = set()
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if (
            not line
            or line in ignored_exact
            or line.startswith(ignored_prefixes)
            or any(fragment in line for fragment in ignored_fragments)
            or re.fullmatch(
                r"(?:Compilation succeeded(?: - \d+ warning\(s\))?|\d+ (?:warning|error)\(s\)|Time Elapsed .+)",
                line,
                re.IGNORECASE,
            )
        ):
            continue
        if line not in seen:
            diagnostics.append(line)
            seen.add(line)
    return diagnostics


@app.get("/health")
def health():
    return {"status": "ok", "service": settings.service_name}


@app.post("/analyze", response_model=FindingsResponse)
def analyze(request: AnalyzeRequest):
    """Run Python linters or language-neutral quality checks on supported source files."""
    files = {f.path: f.content for f in request.files}

    if not files:
        return FindingsResponse(
            project_id=request.project_id,
            agent="code_quality",
            findings=[],
            findings_count=0,
            error="No files supplied",
        )

    if len(files) > settings.max_files:
        return FindingsResponse(
            project_id=request.project_id,
            agent="code_quality",
            findings=[],
            findings_count=0,
            error=f"Too many files supplied; maximum is {settings.max_files}",
        )

    oversized = [
        path for path, content in files.items()
        if len(content.encode("utf-8")) > settings.max_file_size_bytes
    ]
    if oversized:
        return FindingsResponse(
            project_id=request.project_id,
            agent="code_quality",
            findings=[],
            findings_count=0,
            error=f"File exceeds size limit: {oversized[0]}",
        )

    source_paths = [path for path in files if is_source_path(path)]
    language = detect_language(source_paths)
    if language is None:
        return FindingsResponse(
            project_id=request.project_id,
            agent="code_quality",
            findings=[],
            findings_count=0,
            error="No supported source files supplied",
        )
    source_code_files = {path: files[path] for path in source_paths}
    if language != "python":
        if request.tools:
            return FindingsResponse(
                project_id=request.project_id,
                agent="code_quality",
                findings=[],
                findings_count=0,
                error="Ruff, Pylint, and Radon are Python-only; omit tools for language-neutral checks.",
            )
        checks = request.project_context.get("analysis_checks", {})
        code_quality_enabled = bool(checks.get("code_quality", True))
        complexity_enabled = bool(checks.get("complexity", True))
        raw_issues: list[dict] = []
        tool_runs: list[dict] = []
        analyzer_error = None
        if code_quality_enabled or complexity_enabled:
            try:
                raw_issues, tool_run = _run_language_analyzer(
                    request.project_id, files, language
                )
                tool_runs.append(tool_run)
            except RuntimeError as exc:
                analyzer_error = str(exc)
        findings = normalize(raw_issues)
        quality_summary = _quality_summary(source_code_files, findings)
        quality_summary["selected_tools"] = [item["tool"] for item in tool_runs]
        quality_summary["tool_runs"] = tool_runs
        quality_summary["tool_results"] = {
            item["tool"]: {
                "status": item["status"],
                "finding_count": item["finding_count"],
            }
            for item in tool_runs
        }
        quality_summary["hotspots"] = _detect_hotspots(raw_issues)
        quality_summary["per_file"] = per_file_summary(source_code_files, findings)
        if analyzer_error:
            quality_summary["quality_gate_status"] = "FAIL"
            quality_summary["quality_gate"]["status"] = "FAIL"
        return FindingsResponse(
            project_id=request.project_id,
            agent="code_quality",
            findings=findings,
            findings_count=len(findings),
            quality_summary=quality_summary,
            error=analyzer_error,
        )

    tools = request.tools if request.tools is not None else settings.default_tools
    if not tools:
        return FindingsResponse(
            project_id=request.project_id,
            agent="code_quality",
            findings=[],
            findings_count=0,
            error="Select at least one Python analyzer or omit tools to use the configured defaults.",
        )
    raw_issues: list[dict] = []
    tool_errors: list[str] = []
    tool_runs: list[dict] = []
    analyzers = {
        "ruff": run_ruff,
        "pylint": run_pylint,
        "radon": run_radon,
        "mypy": run_mypy,
    }
    for tool in tools:
        started = perf_counter()
        try:
            tool_issues = analyzers[tool](source_code_files)
            raw_issues += tool_issues
            tool_runs.append({
                "tool": tool,
                "status": "completed",
                "finding_count": len(tool_issues),
                "duration_ms": round((perf_counter() - started) * 1000, 2),
                "findings": tool_issues,
            })
        except PartialMypyAnalysisError as exc:
            raw_issues += exc.issues
            safe_error = "Analyzer returned diagnostics but did not complete analysis."
            logger.warning("Code quality tool %s returned partial diagnostics", tool)
            tool_errors.append(f"{tool}: partial")
            tool_runs.append({
                "tool": tool,
                "status": "partial",
                "finding_count": len(exc.issues),
                "duration_ms": round((perf_counter() - started) * 1000, 2),
                "error": safe_error,
                "findings": exc.issues,
            })
        except Exception as exc:  # noqa: BLE001 - preserve other tool results
            if isinstance(exc, FileNotFoundError):
                tool_status = "unavailable"
                safe_error = "Analyzer executable is unavailable."
                finding_code = f"{tool.upper()}_UNAVAILABLE"
            elif isinstance(exc, TimeoutError):
                tool_status = "timed_out"
                safe_error = "Analyzer exceeded its execution time limit."
                finding_code = f"{tool.upper()}_TIMEOUT"
            else:
                tool_status = "failed"
                safe_error = "Analyzer failed; check service logs for details."
                finding_code = f"{tool.upper()}_FAILED"
            logger.warning("Code quality tool %s %s (%s)", tool, tool_status, type(exc).__name__)
            tool_errors.append(f"{tool}: {tool_status}")
            raw_issues.append(
                {
                    "source": tool,
                    "file": None,
                    "line_number": None,
                    "code": finding_code,
                    "message": safe_error,
                    "severity_hint": "low",
                }
            )
            tool_runs.append({
                "tool": tool,
                "status": tool_status,
                "finding_count": 1,
                "duration_ms": round((perf_counter() - started) * 1000, 2),
                "error": safe_error,
                "findings": [],
            })

    findings = normalize(raw_issues)
    quality_summary = _quality_summary(source_code_files, findings)
    unavailable_tools = [
        {"tool": item["tool"], "status": item["status"]}
        for item in tool_runs
        if item["status"] != "completed"
    ]
    if unavailable_tools:
        quality_summary["quality_gate_status"] = "FAIL"
        quality_summary["quality_gate"]["status"] = "FAIL"
        quality_summary["quality_gate"]["blocking_tools"] = unavailable_tools
    quality_summary["quality_score"] = _quality_score(quality_summary)
    quality_summary["selected_tools"] = tools
    quality_summary["tool_runs"] = tool_runs
    quality_summary["tool_results"] = {item["tool"]: {"status": item["status"], "finding_count": item["finding_count"], "error": item.get("error"), "findings": item.get("findings", [])} for item in tool_runs}
    quality_summary["hotspots"] = _detect_hotspots(raw_issues)
    quality_summary["per_file"] = per_file_summary(source_code_files, findings)

    return FindingsResponse(
        project_id=request.project_id,
        agent="code_quality",
        findings=findings,
        findings_count=len(findings),
        error="; ".join(tool_errors) if tool_errors else None,
        quality_summary=quality_summary,
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=True)
