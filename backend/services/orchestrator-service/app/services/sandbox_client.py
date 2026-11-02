"""
HTTP client for the Docker Sandbox Service (Shifa's sandbox-service).
The orchestrator never runs untrusted code itself — it always delegates
execution to this isolated service.
"""
import httpx

from app.config import settings
from shared.languages import source_files


async def execute(
    project_id: str,
    files: dict[str, str],
    stdin: str = "",
    *,
    validation_only: bool = False,
    analysis_only: bool = False,
    test_cases: list[dict[str, str | None]] | None = None,
) -> dict:
    source_code_files = source_files(files)
    if not source_code_files:
        return {
            "project_id": project_id,
            "status": "project_incomplete",
            "executed": False,
            "passed": False,
            "error": "No supported source files were supplied.",
        }

    selected_path = next(
        (path for path in source_code_files if path.rsplit("/", 1)[-1].lower() == "manage.py"),
        next(
        (path for path in source_code_files if path.rsplit("/", 1)[-1].lower() == "main.py"),
        next(iter(source_code_files)),
        ),
    )

    async with httpx.AsyncClient(timeout=settings.sandbox_call_timeout_seconds) as client:
        try:
            payload = {
                "code": source_code_files[selected_path],
                "filename": selected_path,
                "files": files,
                "stdin": stdin,
                "validation_only": validation_only,
                "analysis_only": analysis_only,
            }
            if test_cases:
                payload["test_cases"] = test_cases
            response = await client.post(
                f"{settings.sandbox_service_url}/execute",
                json=payload,
            )
            response.raise_for_status()
            result = response.json()
            status = result.get("status")
            check_passed = result.get("passed_execution", result.get("passed", True))
            passed = status == "completed" and result.get("exit_code") == 0 and check_passed is not False
            executed = bool(result.get("executed", status in {"completed", "failed", "timeout"}))
            failure_detail = None
            if not passed:
                failure_detail = result.get("error") or result.get("stderr") or result.get("error_type") or f"Sandbox execution ended with status {status} (exit code {result.get('exit_code')})."
            return {
                **result,
                "project_id": project_id,
                "selected_file": selected_path,
                "executed": executed,
                "passed": passed,
                "error": str(failure_detail)[:4000] if failure_detail else None,
            }
        except httpx.HTTPError as exc:
            return {
                "project_id": project_id,
                "executed": False,
                "error": f"sandbox-service unreachable (not yet integrated?): {exc}",
            }
