"""
HTTP client for the Code Quality Service (also owned by Sreya).
Kept as a client rather than a direct import so the orchestrator never
depends on the quality service's internals — only its HTTP contract.
"""
import httpx

from app.config import settings


async def analyze(
    project_id: str,
    files: dict[str, str],
    project_context: dict | None = None,
    tools: list[str] | None = None,
) -> dict:
    """
    files: {path: content}
    Returns the FindingsResponse dict from the code-quality-service.
    """
    payload = {
        "project_id": project_id,
        "files": [{"path": p, "content": c} for p, c in files.items()],
        "project_context": project_context or {},
        "tools": tools,
    }

    async with httpx.AsyncClient(timeout=settings.quality_call_timeout_seconds) as client:
        try:
            resp = await client.post(f"{settings.quality_service_url}/analyze", json=payload)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as exc:
            return {
                "project_id": project_id,
                "agent": "code_quality",
                "findings": [],
                "findings_count": 0,
                "error": f"quality-service unreachable: {exc}",
            }
