from fastapi import FastAPI

from app.api.routes import router


app = FastAPI(
    title="Fix & Regeneration Agent",
    description="AI-powered code fixing and regeneration service",
    version="1.0.0"
)


@app.get("/")
def root():
    return {
        "service": "Fix & Regeneration Agent",
        "status": "running"
    }


@app.get("/health")
def health():
    return {
        "status": "healthy"
    }


app.include_router(router)