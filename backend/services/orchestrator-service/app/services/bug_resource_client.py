"""
HTTP client for the Bug & Resource Service (Shifa's bug-resource-service).
Same graceful-degradation pattern as security_client.py — safe to call
before that service exists.
"""
import httpx

from app.config import settings


async def analyze(project_id: str, files: dict[str, str], project_context: dict | None = None) -> dict:
    payload = {
        "project_id": project_id,
        "files": [{"path": p, "content": c} for p, c in files.items()],
        "project_context": project_context or {},
    }

    async with httpx.AsyncClient(timeout=settings.agent_call_timeout_seconds) as client:
        try:
            resp = await client.post(f"{settings.bug_resource_service_url}/analyze", json=payload)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as exc:
            return {
                "project_id": project_id,
                "agent": "bug_resource",
                "findings": [],
                "findings_count": 0,
                "error": f"bug-resource-service unreachable (not yet integrated?): {exc}",
            }
