from fastapi import FastAPI

from app.api.routes import router
from app.services.ollama_service import model_status


app = FastAPI(title="Fix and Regeneration Agent", version="1.0.0")


@app.get("/health")
async def health() -> dict:
    provider_ready, provider_error = await model_status()
    return {
        "status": "ok",
        "service": "repair-service",
        "configured": True,
        "provider": "ollama",
        "provider_ready": provider_ready,
        "provider_error": provider_error,
    }


app.include_router(router)