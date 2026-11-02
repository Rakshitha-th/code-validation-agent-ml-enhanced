import httpx

from app.config import settings


async def analyze(files: dict[str, str]) -> dict:
    async with httpx.AsyncClient(timeout=settings.ai_report_timeout_seconds) as client:
        response = await client.post(
            f"{settings.report_recommendation_service_url.rstrip('/')}/analyze",
            json={"files": files},
        )
        response.raise_for_status()
        return response.json()