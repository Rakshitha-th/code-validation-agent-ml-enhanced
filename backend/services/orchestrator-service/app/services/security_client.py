"""HTTP adapter for the team's per-file Security Agent API."""
import httpx

from app.config import settings
from app.services.finding_mapper import normalize_finding


async def analyze(project_id: str, files: dict[str, str], project_context: dict | None = None) -> dict:
    findings = []
    async with httpx.AsyncClient(timeout=settings.security_call_timeout_seconds) as client:
        try:
            response = await client.post(
                f"{settings.security_service_url}/scan-project",
                json={"project_id": project_id, "files": files},
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or not isinstance(payload.get("findings"), list):
                raise ValueError("security-service returned a malformed project scan response")
            for item in payload["findings"]:
                if not isinstance(item, dict):
                    raise ValueError("security-service returned a malformed finding")
                path = item.get("file") or "unknown"
                findings.append(normalize_finding(item, path, "SEC", len(findings) + 1, "security"))
            error = (
                "Security scanner did not complete successfully."
                if payload.get("status") != "completed"
                else None
            )
        except (httpx.HTTPError, ValueError) as exc:
            error = f"security-service request failed: {exc}"

    return {
        "project_id": project_id,
        "agent": "security",
        "findings": findings,
        "findings_count": len(findings),
        "error": error,
    }
