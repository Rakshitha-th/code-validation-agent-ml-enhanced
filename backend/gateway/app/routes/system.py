import asyncio

import httpx
from fastapi import APIRouter, Depends

from app.config import settings
from app.security import get_current_user

router = APIRouter(prefix="/api/system", tags=["system"], dependencies=[Depends(get_current_user)])
public_router = APIRouter(prefix="/api/system", tags=["public system status"])

SERVICES = {
    "orchestrator": settings.orchestrator_url,
    "code_quality": settings.quality_service_url,
    "security": settings.security_service_url,
    "bug_resource": settings.bug_resource_service_url,
    "repair": settings.repair_service_url,
    "sandbox": settings.sandbox_service_url,
    "validation": settings.validation_service_url,
    "report_recommendation": settings.report_recommendation_service_url,
}


async def _check_service(client: httpx.AsyncClient, name: str, url: str | None) -> tuple[str, dict]:
    if not url:
        return name, {"status": "not_configured", "healthy": False}
    try:
        response = await client.get(f"{url.rstrip('/')}/health")
        response.raise_for_status()
        payload = response.json() if "application/json" in response.headers.get("content-type", "") else {}
        if payload.get("configured") is False:
            return name, {
                "status": "not_configured",
                "healthy": False,
                "reachable": True,
                "details": payload,
            }
        if payload.get("provider_ready") is False:
            return name, {
                "status": "unavailable",
                "healthy": False,
                "reachable": True,
                "details": payload,
            }
        if payload.get("provider_ready") is None and payload.get("provider"):
            return name, {
                "status": "configured",
                "healthy": False,
                "reachable": True,
                "details": payload,
            }
        return name, {"status": "healthy", "healthy": True, "reachable": True, "details": payload}
    except (httpx.HTTPError, ValueError):
        return name, {"status": "unavailable", "healthy": False, "reachable": False}


async def _collect_service_statuses() -> dict[str, dict]:
    async with httpx.AsyncClient(timeout=3.0) as client:
        checks = await asyncio.gather(*(
            _check_service(client, name, url) for name, url in SERVICES.items()
        ))
    return dict(checks)


@router.get("/agents")
async def agent_health() -> dict:
    services = await _collect_service_statuses()
    return {
        "services": services,
        "ready": sum(1 for item in services.values() if item["healthy"]),
        "configured": len(services),
    }


@public_router.get("/public-agents")
async def public_agent_status() -> dict[str, dict[str, bool]]:
    statuses = await _collect_service_statuses()
    services = {}
    for name, status in statuses.items():
        available = status["healthy"]
        if name == "report_recommendation":
            available = available and status.get("details", {}).get("configured", True)
        services[name] = bool(available)
    return {"services": services}
