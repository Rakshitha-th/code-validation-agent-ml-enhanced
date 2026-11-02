
import logging
import os
import re

import httpx
from fastapi import FastAPI, HTTPException

from shared.languages import detect_language, is_source_path, validate_project_path
from .schemas import ValidationRequest, ValidationResponse
from .validator import validate_code


logger = logging.getLogger(__name__)

SANDBOX_SERVICE_URL = os.getenv("VALIDATION_SANDBOX_SERVICE_URL")
SANDBOX_TIMEOUT_SECONDS = 75

app = FastAPI(
    title="Code Validation Agent",
    description="Language-aware static validation service",
    version="1.1.0",
)


@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "service": "validation-agent",
        "sandbox_configured": bool(SANDBOX_SERVICE_URL),
    }


def _project_files(request: ValidationRequest) -> dict[str, str]:
    files = request.files or {request.filename: request.code}
    normalized = {}

    for path, content in files.items():
        safe_path = validate_project_path(path)

        if safe_path in normalized:
            raise ValueError("Project file paths must be unique")

        normalized[safe_path] = content

    source_paths = [
        path for path in normalized
        if is_source_path(path)
    ]

    if not source_paths:
        raise ValueError("At least one supported source file is required")

    detect_language(source_paths)

    total_size = sum(
        len(content.encode("utf-8"))
        for content in normalized.values()
    )

    if len(normalized) > 100 or total_size > 1_000_000:
        raise ValueError("Project exceeds the validation input limits")

    if any(
        len(content.encode("utf-8")) > 500_000
        for content in normalized.values()
    ):
        raise ValueError("A project file exceeds the validation size limit")

    return normalized


def _entry_file(files: dict[str, str]) -> str:
    source_paths = [
        path for path in files
        if is_source_path(path)
    ]

    preferred = {
        "main.py",
        "main.js",
        "index.js",
        "main.ts",
        "index.ts",
        "main.java",
        "main.c",
        "main.cpp",
        "main.go",
        "program.cs",
    }

    return next(
        (
            path for path in source_paths
            if path.rsplit("/", 1)[-1].lower() in preferred
        ),
        source_paths[0],
    )


def _compiler_finding(
    filename: str,
    files: dict[str, str],
    output: str,
    status: str,
) -> dict | None:

    output_lines = [
        line.strip()
        for line in output.splitlines()
        if line.strip()
    ]

    diagnostic_pattern = re.compile(
        r"^(?P<file>.+?)(?::(?P<line>\d+)(?::(?P<column>\d+))?"
        r"|\((?P<msline>\d+),(?P<mscolumn>\d+)\)):\s*"
        r"(?P<message>.*)$"
    )

    diagnostic = next(
        (
            diagnostic_pattern.match(line)
            for line in output_lines
            if diagnostic_pattern.match(line)
        ),
        None,
    )

    diagnostic_file = filename

    if diagnostic:
        reported_path = diagnostic.group("file").replace("\\", "/")

        diagnostic_file = next(
            (
                path for path in files
                if reported_path.endswith(f"/{path}")
                or reported_path == path
            ),
            filename,
        )

    if status == "completed":
        if (
            not diagnostic
            or not re.search(
                r"\bwarning\b",
                diagnostic.group("message"),
                re.IGNORECASE,
            )
        ):
            return None

        message = diagnostic.group("message")
        code = "COMPILER_WARNING"
        severity = "LOW"

    else:
        message = (
            diagnostic.group("message")
            if diagnostic
            else "\n".join(output_lines)
        )

        message = (
            message
            or "Language validation failed without compiler diagnostics."
        )

        code = "COMPILATION_FAILED"
        severity = "HIGH"

    line = (
        diagnostic.group("line") or diagnostic.group("msline")
        if diagnostic else None
    )

    column = (
        diagnostic.group("column") or diagnostic.group("mscolumn")
        if diagnostic else None
    )

    return {
        "type": "CompilerDiagnostic",
        "severity": severity,
        "file": diagnostic_file,
        "line": int(line) if line else None,
        "column": int(column) if column else None,
        "message": message[:1200],
        "code": code,
        "recommendation": (
            "Review the compiler diagnostic and update the source "
            "before proceeding."
        ),
    }


def _validate_non_python(
    files: dict[str, str],
    language: str,
    filename: str,
) -> dict:

    if not SANDBOX_SERVICE_URL:
        detail = (
            "The isolated language validation sandbox is not configured."
        )

        return {
            "status": "error",
            "filename": filename,
            "valid": False,
            "passed": False,
            "needs_review": False,
            "total_findings": 1,
            "findings": [{
                "type": "ValidationError",
                "severity": "HIGH",
                "file": filename,
                "message": detail,
                "code": "VALIDATOR_UNAVAILABLE",
                "recommendation": (
                    "Configure VALIDATION_SANDBOX_SERVICE_URL and retry."
                ),
            }],
            "language": language,
            "validator": "isolated_language_compiler",
        }

    entry = _entry_file(files)

    try:
        response = httpx.post(
            f"{SANDBOX_SERVICE_URL.rstrip('/')}/execute",
            json={
                "project_id": "validation",
                "filename": entry,
                "code": files[entry],
                "files": files,
                "validation_only": True,
            },
            timeout=SANDBOX_TIMEOUT_SECONDS,
        )

        response.raise_for_status()
        sandbox_result = response.json()

    except httpx.TimeoutException:
        return {
            "status": "failed",
            "filename": filename,
            "valid": False,
            "passed": False,
            "needs_review": False,
            "total_findings": 1,
            "findings": [{
                "type": "ValidationError",
                "severity": "HIGH",
                "file": filename,
                "message": "Language validation exceeded its time limit.",
                "code": "VALIDATOR_TIMEOUT",
                "recommendation": (
                    "Reduce project size or investigate the sandbox "
                    "before retrying."
                ),
            }],
            "language": language,
            "validator": "isolated_language_compiler",
        }

    except (httpx.HTTPError, ValueError) as error:
        logger.error(
            "language_validation_sandbox_request_failed exception=%s",
            type(error).__name__,
        )

        return {
            "status": "error",
            "filename": filename,
            "valid": False,
            "passed": False,
            "needs_review": False,
            "total_findings": 1,
            "findings": [{
                "type": "ValidationError",
                "severity": "HIGH",
                "file": filename,
                "message": (
                    "The isolated language validation service "
                    "could not be reached."
                ),
                "code": "VALIDATOR_UNAVAILABLE",
                "recommendation": (
                    "Check sandbox health and retry validation."
                ),
            }],
            "language": language,
            "validator": "isolated_language_compiler",
        }

    if (
        not isinstance(sandbox_result, dict)
        or not sandbox_result.get("executed")
    ):
        detail = (
            sandbox_result.get("error")
            or sandbox_result.get("stderr")
            if isinstance(sandbox_result, dict)
            else None
        ) or "The isolated language validator did not execute."

        blocked = (
            isinstance(sandbox_result, dict)
            and sandbox_result.get("status") in {
                "dependency_missing",
                "project_incomplete",
                "unsupported_project",
            }
        )

        finding_code = (
            "VALIDATION_BLOCKED"
            if blocked
            else "VALIDATOR_UNAVAILABLE"
        )

        status = "blocked" if blocked else "error"

    elif sandbox_result.get("status") == "timeout":
        detail = (
            sandbox_result.get("stderr")
            or "Language validation timed out."
        )

        finding_code = "VALIDATOR_TIMEOUT"
        status = "failed"

    elif (
        sandbox_result.get("status") != "completed"
        or sandbox_result.get("exit_code") != 0
    ):
        detail = (
            sandbox_result.get("stderr")
            or sandbox_result.get("error")
            or "Compilation failed."
        )

        finding_code = "COMPILATION_FAILED"
        status = "failed"

    else:
        detail = "\n".join(
            part
            for part in (
                sandbox_result.get("stdout"),
                sandbox_result.get("stderr"),
            )
            if part
        )

        finding_code = ""
        status = "completed"

    finding = _compiler_finding(
        filename,
        files,
        detail,
        status,
    )

    if finding and status in {"error", "blocked"}:
        finding["type"] = "ValidationError"
        finding["code"] = finding_code

    elif finding and sandbox_result.get("status") == "timeout":
        finding["type"] = "ValidationError"
        finding["code"] = "VALIDATOR_TIMEOUT"

    if finding is None and status != "completed":
        finding = {
            "type": "ValidationError",
            "severity": "HIGH",
            "file": filename,
            "message": detail[:1200],
            "code": finding_code,
            "recommendation": (
                "Review the validation error and ensure the project "
                "can compile in the configured environment."
            ),
        }

    findings = [finding] if finding else []

    passed = status == "completed"

    return {
        "status": (
            "blocked" if status == "blocked"
            else "error" if status == "error"
            else "failed" if status == "failed"
            else "passed"
        ),
        "filename": filename,
        "valid": passed,
        "passed": passed,
        "needs_review": False,
        "total_findings": len(findings),
        "findings": findings,
        "language": language,
        "validator": (
            sandbox_result.get("validator")
            or "isolated_language_compiler"
        ),
        "sandbox_result": sandbox_result,
    }


@app.post("/validate", response_model=ValidationResponse)
def validate(request: ValidationRequest):

    try:
        files = _project_files(request)

    except ValueError as error:
        raise HTTPException(
            status_code=422,
            detail=str(error),
        ) from error

    source_paths = [
        path for path in files
        if is_source_path(path)
    ]

    language = detect_language(source_paths)

    filename = (
        request.filename
        if request.filename in source_paths
        else _entry_file(files)
    )

    if language != "python":
        return _validate_non_python(
            files,
            language,
            filename,
        )

    findings = []
    per_file_metrics = []

    for path in source_paths:
        file_findings, metrics = validate_code(
            files[path],
            path,
        )

        findings.extend(file_findings)
        per_file_metrics.append(metrics)

    metrics = {
        "lines_of_code": sum(
            item["lines_of_code"]
            for item in per_file_metrics
        ),
        "functions": sum(
            item["functions"]
            for item in per_file_metrics
        ),
        "classes": sum(
            item["classes"]
            for item in per_file_metrics
        ),
        "imports": sum(
            item["imports"]
            for item in per_file_metrics
        ),
        "max_function_length": max(
            item["max_function_length"]
            for item in per_file_metrics
        ),
        "max_nesting_depth": max(
            item["max_nesting_depth"]
            for item in per_file_metrics
        ),
    }

    blocking_codes = {
        "SYNTAX",
        "VALIDATOR_ERROR",
        "VALIDATOR_TIMEOUT",
    }

    blocking_findings = [
        item
        for item in findings
        if item.get("code") in blocking_codes
    ]

    if blocking_findings:
        status = "failed"
    else:
        status = "passed"

    passed = not blocking_findings

    return {
        "status": status,
        "filename": filename,
        "valid": passed,
        "passed": passed,
        "needs_review": False,
        "total_findings": len(findings),
        "findings": findings,
        "metrics": metrics,
        "language": language,
        "validator": "Python AST + Flake8",
    }
