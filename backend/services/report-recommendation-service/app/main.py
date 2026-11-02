import logging
import os
from pathlib import PurePosixPath
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, ValidationError, field_validator

from app.agent import generate_gemini_report_and_recommendations, generate_report_and_recommendations
from shared.languages import detect_language, is_source_path


app = FastAPI(title="Report and Recommendation Agent", version="1.0.0")
logger = logging.getLogger(__name__)
MAX_INPUT_CHARACTERS = 300_000


def provider_failure_detail(error: Exception) -> str:
    if isinstance(error, httpx.TimeoutException):
        return "AI provider request timed out. Check provider availability or increase the configured timeout."
    if isinstance(error, httpx.ConnectError):
        return "AI provider is unreachable. Check the configured endpoint and network access."
    if isinstance(error, httpx.HTTPStatusError):
        return f"AI provider returned HTTP {error.response.status_code}."
    if isinstance(error, ValidationError):
        fields = sorted({".".join(str(part) for part in item["loc"]) for item in error.errors(include_input=False)})
        suffix = f" Invalid fields: {', '.join(fields[:5])}." if fields else ""
        return f"AI provider response did not match the required schema.{suffix}"
    return f"AI provider request failed ({type(error).__name__})."


class AnalyzeRequest(BaseModel):
    files: dict[str, str] = Field(min_length=1)

    @field_validator("files")
    @classmethod
    def validate_files(cls, files: dict[str, str]) -> dict[str, str]:
        source_paths: list[str] = []
        for name, content in files.items():
            path = PurePosixPath(name.replace("\\", "/"))
            has_windows_drive = bool(path.parts) and len(path.parts[0]) == 2 and path.parts[0][1] == ":"
            if path.is_absolute() or has_windows_drive or ".." in path.parts or not is_source_path(path.as_posix()):
                raise ValueError("Only relative source files in a supported language are accepted")
            if not isinstance(content, str):
                raise ValueError("Source file content must be text")
            source_paths.append(name)
        try:
            detect_language(source_paths)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        if sum(len(content) for content in files.values()) > MAX_INPUT_CHARACTERS:
            raise ValueError("Project exceeds the AI report input limit")
        return files


class AnalyzeResponse(BaseModel):
    status: Literal["completed", "skipped"]
    provider: Literal["ollama", "gemini"] | None = None
    code_report: dict = Field(default_factory=dict)
    recommendation_report: dict = Field(default_factory=dict)
    message: str | None = None


@app.get("/health")
async def health() -> dict:
    provider = os.getenv("REPORT_RECOMMENDATION_PROVIDER", "ollama").strip().lower()
    if provider == "gemini":
        configured = bool(os.getenv("GEMINI_API_KEY"))
        return {
            "status": "ok",
            "service": "report-recommendation-service",
            "configured": configured,
            "provider": "gemini",
            "provider_ready": configured,
            "provider_error": None if configured else "GEMINI_API_KEY is not configured.",
        }
    if provider != "ollama":
        return {
            "status": "ok",
            "service": "report-recommendation-service",
            "configured": False,
            "provider_ready": False,
            "provider_error": "Unsupported report recommendation provider.",
        }

    ollama_url = os.getenv("OLLAMA_BASE_URL")
    if not ollama_url:
        return {
            "status": "ok",
            "service": "report-recommendation-service",
            "configured": False,
            "provider_ready": False,
        }

    parsed = urlsplit(ollama_url)
    path = parsed.path
    if path.endswith("/api/generate"):
        path = path[:-len("generate")] + "tags"
    else:
        path = path.rstrip("/") + "/api/tags"
    tags_url = urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, parsed.fragment))
    model_name = os.getenv("OLLAMA_MODEL", "qwen2.5-coder:3b")
    provider_error = None
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(tags_url)
            response.raise_for_status()
            models = response.json().get("models", [])
        provider_ready = any(
            isinstance(model, dict) and model_name in {model.get("name"), model.get("model")}
            for model in models
        )
        if not provider_ready:
            provider_error = "The configured Ollama model is not installed."
    except (httpx.HTTPError, ValueError):
        provider_ready = False
        provider_error = "Ollama is unreachable or returned an invalid model list."
    return {
        "status": "ok",
        "service": "report-recommendation-service",
        "configured": True,
        "provider": "ollama",
        "provider_ready": provider_ready,
        "provider_error": provider_error,
    }


@app.post(
    "/analyze",
    responses={502: {"description": "AI report/recommendation generation failed"}},
)
async def analyze(request: AnalyzeRequest) -> AnalyzeResponse:
    provider = os.getenv("REPORT_RECOMMENDATION_PROVIDER", "ollama").strip().lower()
    if provider == "gemini":
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            return AnalyzeResponse(status="skipped", message="No Gemini API key is configured.")
        try:
            code_report, recommendation_report = await generate_gemini_report_and_recommendations(
                request.files,
                api_key,
                model_name=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
            )
        except Exception as exc:  # noqa: BLE001 - keep provider details and secrets out of API responses
            detail = provider_failure_detail(exc)
            logger.warning("Gemini report/recommendation request failed (%s): %s", type(exc).__name__, detail)
            raise HTTPException(status_code=502, detail=detail) from exc

        return AnalyzeResponse(
            status="completed",
            provider="gemini",
            code_report=code_report,
            recommendation_report=recommendation_report,
        )
    if provider != "ollama":
        return AnalyzeResponse(status="skipped", message="No supported AI report provider is configured.")

    ollama_base_url = os.getenv("OLLAMA_BASE_URL")
    if not ollama_base_url:
        return AnalyzeResponse(status="skipped", message="No Ollama endpoint is configured.")

    try:
        code_report, recommendation_report = await generate_report_and_recommendations(
            request.files,
            ollama_base_url,
            ollama_model=os.getenv("OLLAMA_MODEL", "qwen2.5-coder:3b"),
        )
    except Exception as exc:  # noqa: BLE001 - keep provider details and secrets out of API responses
        detail = provider_failure_detail(exc)
        logger.warning("AI report/recommendation request failed (%s): %s", type(exc).__name__, detail)
        raise HTTPException(status_code=502, detail=detail) from exc

    return AnalyzeResponse(
        status="completed",
        provider="ollama",
        code_report=code_report,
        recommendation_report=recommendation_report,
    )