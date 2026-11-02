
"""
HTTP client for the Fix & Regeneration Service. ("Rakshitha")
"""
import httpx

from app.config import settings
from shared.languages import source_files


def _failed_result(project_id: str, files: dict[str, str], findings: list[dict], message: str) -> dict:
    return {
        "project_id": project_id,
        "mode": "targeted",
        "status": "failed",
        "repair_status": "failed",
        "outcome": "no_change",
        "changed_files": [],
        "repaired_files": [
            {"path": path, "content": content}
            for path, content in files.items()
        ],
        "unresolved_findings": findings,
        "repair_details": [],
        "validation_status": "not_run",
        "error": message,
    }


def _finding_file(finding: dict, files: dict[str, str], fallback: str) -> str:
    path = finding.get("file")
    source_paths = source_files(files)
    return path if isinstance(path, str) and path in source_paths else fallback


def _finding_line(finding: dict) -> int | None:
    value = finding.get("line_number", finding.get("line"))
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


async def repair(
    project_id: str,
    files: dict[str, str],
    findings: list[dict],
    repair_feedback: str = "",
    language: str | None = None,
) -> dict:
    source_code_files = source_files(files)
    fallback_path = next(iter(source_code_files), next(iter(files), ""))
    request = {
        "project_id": project_id,
        "language": language or "python",
        "files": [
            {"path": path, "content": content}
            for path, content in files.items()
        ],
        "findings": [
            {
                "finding_id": finding.get("finding_id"),
                "file": _finding_file(finding, files, fallback_path),
                "line": _finding_line(finding),
                "rule_id": finding.get("rule_id") or finding.get("issue") or finding.get("code"),
                "type": finding.get("category") or finding.get("issue", "bug"),
                "category": finding.get("category"),
                "severity": finding.get("severity", "medium"),
                "message": finding.get("description") or finding.get("message") or finding.get("issue", ""),
                "recommendation": finding.get("recommendation"),
                "evidence": finding.get("evidence"),
                "tool": finding.get("tool"),
                "subcategory": finding.get("subcategory"),
            }
            for finding in findings
            if finding.get("file") in files
        ],
        "repair_feedback": repair_feedback,
    }

    try:
        async with httpx.AsyncClient(
            timeout=settings.repair_call_timeout_seconds
        ) as client:
            response = await client.post(
                f"{settings.repair_service_url.rstrip('/')}/repair",
                json=request,
            )
            response.raise_for_status()
            result = response.json()

    except httpx.TimeoutException:
        return _failed_result(
            project_id, files, findings,
            "Repair service timed out before returning a candidate."
        )

    except httpx.ConnectError:
        return _failed_result(
            project_id, files, findings,
            "Could not connect to the configured repair service."
        )

    except httpx.HTTPStatusError as exc:
        return _failed_result(
            project_id,
            files,
            findings,
            f"Repair service returned HTTP {exc.response.status_code}: "
            f"{exc.response.text[:1000]}",
        )

    except (httpx.HTTPError, ValueError) as exc:
        return _failed_result(
            project_id,
            files,
            findings,
            f"Repair service request failed ({type(exc).__name__})."
        )

    if not isinstance(result, dict):
        return _failed_result(
            project_id, files, findings,
            "Repair service returned an invalid response object."
        )

    if result.get("status") not in {"completed", "partial", "failed"}:
        return _failed_result(
            project_id, files, findings,
            "Repair service returned an unknown repair status."
        )

    repair_status = result.get("repair_status")

    if repair_status not in {
        "success", "partial", "failed",
        "needs_review", "not_run", "running"
    }:
        repair_status = {
            "completed": "needs_review" if findings else "not_run",
            "partial": "needs_review",
            "failed": "failed",
        }[result["status"]]

    repaired_files = dict(files)
    repair_details = []

    for repair_result in result.get("repairs", []):
        path = repair_result.get("path")

        if path in repaired_files:
            repaired_files[path] = repair_result.get(
                "regenerated_code",
                repaired_files[path],
            )

        detail = {
            "path": path,
            "changed": bool(repair_result.get("changed")),
            "explanation": repair_result.get("explanation"),
            "resolved_finding_ids": repair_result.get(
                "resolved_finding_ids", []
            ),
        }

        if repair_result.get("candidate_code") is not None:
            detail["candidate_code"] = repair_result["candidate_code"]

        repair_details.append(detail)

    status = result.get("status", "failed")
    error = result.get("error")

    changed_files = [
        path for path, content in repaired_files.items()
        if content != files.get(path)
    ]

    if status == "completed" and not changed_files and findings:
        status = "failed"
        repair_status = "failed"
        error = error or (
            "Repair service returned no changed files; "
            "findings remain unresolved."
        )

    if status == "failed" and not error:
        error = next(
            (
                detail["explanation"]
                for detail in repair_details
                if not detail["changed"] and detail.get("explanation")
            ),
            "Repair service did not produce a usable candidate.",
        )

    elif status == "partial" and not error and not changed_files:
        error = "Repair service left one or more findings unresolved."

    return {
        "project_id": project_id,
        "mode": "targeted",
        "status": status,
        "repair_status": repair_status,
        "outcome": result.get(
            "outcome",
            "no_change" if not changed_files else "partially_applied",
        ),
        "changed_files": changed_files,
        "repaired_files": [
            {"path": path, "content": content}
            for path, content in repaired_files.items()
        ],
        "unresolved_findings": result.get("unresolved_findings", []),
        "repair_details": repair_details,
        "validation_status": result.get("validation_status"),
        "skipped_checks": result.get("skipped_checks", []),
        "error": error,
    }
