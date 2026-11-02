from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import check_database, close_database, initialize_database
from app.routes import admin, analysis, auth, overview, projects, results, system
from app.routes import files


@asynccontextmanager
async def lifespan(app: FastAPI):
    await initialize_database()
    await auth.ensure_bootstrap_admin()
    yield
    await close_database()

app = FastAPI(title="Coding Validation Gateway", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Accept", "Content-Type", "X-Requested-With"],
)


@app.get("/health")
async def health():
    await check_database()
    return {"status": "ok", "service": settings.service_name, "database": "connected"}


app.include_router(projects.router)
app.include_router(projects.api_router)
app.include_router(files.router)
app.include_router(analysis.router)
app.include_router(analysis.api_router)
app.include_router(results.router)
app.include_router(results.history_router)
app.include_router(overview.router)
app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(system.public_router)
app.include_router(system.router)
app.include_router(system.public_router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=True)