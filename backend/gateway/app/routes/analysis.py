import json
import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import SessionFactory, get_session
from app.models import AnalysisRunRecord, ProjectRecord, UserRecord
from app.security import get_current_user, require_owned_project, require_roles
from app.services.run_store import (
    create_run,
    latest_run_for_project,
    latest_run_for_workflow,
    persist_snapshot,
    prepare_project,
)
from app.services.store import file_payload, project_files
from shared.languages import (
    SOURCE_LANGUAGES,
    detect_language,
    is_source_path,
    strip_markdown_code_fence,
    validate_project_path,
)

logger = logging.getLogger(__name__)
ORCHESTRATOR_UNAVAILABLE = "Orchestrator unavailable"
PROJECT_NOT_FOUND = "Project not found"
ANALYSIS_RUN_NOT_FOUND = "Analysis run not found"


class ProjectFileIn(BaseModel):
    path: str
    content: str

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return validate_project_path(value)

    @field_validator("content")
    @classmethod
    def strip_wrapping_code_fence(cls, value: str, info: ValidationInfo) -> str:
        path = info.data.get("path")
        return strip_markdown_code_fence(path, value) if path else value


class SandboxTestCaseIn(BaseModel):
    input: str = Field(default="", max_length=100_000)
    expected: str | None = Field(default=None, max_length=100_000)


class StartAnalysisRequest(BaseModel):
    project_id: str
    files: list[ProjectFileIn]
    language: str | None = None
    project_type: str | None = None
    project_context: dict = Field(default_factory=dict)
    tools: list[str] | None = None
    test_cases: list[SandboxTestCaseIn] = Field(default_factory=list, max_length=10)

    @field_validator("files")
    @classmethod
    def require_supported_source(cls, files: list[ProjectFileIn]) -> list[ProjectFileIn]:
        source_paths = [item.path for item in files if is_source_path(item.path)]
        try:
            detect_language(source_paths)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        if not source_paths:
            raise ValueError("at least one supported source file is required for analysis")
        return files

    @field_validator("language")
    @classmethod
    def validate_language(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        if normalized not in SOURCE_LANGUAGES:
            raise ValueError(f"Unsupported language {value!r}.")
        return normalized

    @model_validator(mode="after")
    def validate_language_matches_files(self):
        paths = [item.path for item in self.files if is_source_path(item.path)]
        detected = detect_language(paths)
        explicit = self.language or self.project_context.get("language")
        if explicit and explicit != detected:
            raise ValueError(
                f"Selected language {explicit!r} does not match detected source language {detected!r}."
            )
        project_type = self.project_type or self.project_context.get("project_type")
        if project_type not in {None, "single_file", "dotnet"}:
            raise ValueError(f"Unsupported project type {project_type!r}.")
        if project_type == "dotnet" and (
            detected != "csharp"
            or not any(item.path.lower().endswith(".csproj") for item in self.files)
        ):
            raise ValueError("A .NET project requires C# source files and a .csproj manifest.")
        return self

router = APIRouter(prefix="/analysis", tags=["analysis"], dependencies=[Depends(get_current_user)])
api_router = APIRouter(prefix="/api", tags=["analysis"], dependencies=[Depends(get_current_user)])


async def _create_workflow_run(
    project_id: str,
    files: list[dict[str, str]],
    project_context: dict,
    tools: list[str] | None,
    session: AsyncSession,
) -> dict:
    await prepare_project(session, project_id, files)
    run = await create_run(session, project_id, None, tools, project_context)
    await session.commit()
    try:
        async with httpx.AsyncClient(timeout=settings.request_timeout_seconds) as client:
            response = await client.post(
                f"{settings.orchestrator_url.rstrip('/')}/workflow/start",
                json={
                    "project_id": project_id,
                    "workflow_id": run.workflow_id,
                    "files": files,
                    "language": project_context.get("language"),
                    "project_type": project_context.get("project_type"),
                    "project_context": project_context,
                    "tools": tools,
                },
            )
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        run.status = "failed"
        run.error = "The orchestrator rejected this analysis request."
        await session.commit()
        raise HTTPException(status_code=exc.response.status_code, detail=exc.response.text) from exc
    except httpx.HTTPError as exc:
        run.status = "failed"
        run.error = "The orchestrator is unavailable."
        await session.commit()
        raise HTTPException(status_code=502, detail=ORCHESTRATOR_UNAVAILABLE) from exc

    started = response.json()
    return {**started, "project_id": project_id, "run_id": run.run_id}


async def _resolve_workflow(
    identifier: str,
    session: AsyncSession,
    current_user: UserRecord,
) -> tuple[str, AnalysisRunRecord | None]:
    run = await session.get(AnalysisRunRecord, identifier)
    if run is not None:
        await require_owned_project(session, run.project_id, current_user)
        return run.workflow_id, run

    await require_owned_project(session, identifier, current_user)
    run = await latest_run_for_project(session, identifier)
    return (run.workflow_id if run is not None else identifier), run


async def _fetch_workflow_status(workflow_id: str) -> dict:
    try:
        async with httpx.AsyncClient(timeout=settings.request_timeout_seconds) as client:
            response = await client.get(
                f"{settings.orchestrator_url.rstrip('/')}/workflow/status/{workflow_id}"
            )
            response.raise_for_status()
            return response.json()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=exc.response.status_code, detail=exc.response.text) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=ORCHESTRATOR_UNAVAILABLE) from exc


@router.post("/run", responses={502: {"description": ORCHESTRATOR_UNAVAILABLE}})
async def run_analysis(
    request: StartAnalysisRequest,
    session: AsyncSession = Depends(get_session),
    current_user: UserRecord = Depends(require_roles("Developer", "Administrator")),
):
    await require_owned_project(session, request.project_id, current_user)
    return await _create_workflow_run(
        request.project_id,
        [item.model_dump() for item in request.files],
        {
            **request.project_context,
            **({"language": request.language} if request.language else {}),
            **({"project_type": request.project_type} if request.project_type else {}),
            **({"test_cases": [test_case.model_dump() for test_case in request.test_cases]} if request.test_cases else {}),
        },
        request.tools,
        session,
    )


@router.get("/stream/{project_id}", responses={404: {"description": PROJECT_NOT_FOUND}, 502: {"description": ORCHESTRATOR_UNAVAILABLE}})
async def stream_analysis(
    project_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: UserRecord = Depends(get_current_user),
):
    workflow_id, _run = await _resolve_workflow(project_id, session, current_user)

    return StreamingResponse(_stream_workflow_events(workflow_id), media_type="text/event-stream")


async def _persist_stream_snapshot(workflow_id: str, data: str) -> None:
    try:
        snapshot = json.loads(data)
    except (ValueError, TypeError):
        logger.warning("Ignoring an invalid workflow snapshot for %s", workflow_id)
        return
    if not isinstance(snapshot, dict):
        logger.warning("Ignoring a non-object workflow snapshot for %s", workflow_id)
        return
    async with SessionFactory() as session:
        run = await latest_run_for_workflow(session, workflow_id)
        if run is not None:
            await persist_snapshot(session, run, snapshot)
            await session.commit()


async def _stream_workflow_events(workflow_id: str):
    try:
        async with httpx.AsyncClient(timeout=None) as client, client.stream(
            "GET",
            f"{settings.orchestrator_url.rstrip('/')}/workflow/stream/{workflow_id}",
        ) as response:
            if response.status_code >= 400:
                detail = await response.aread()
                raise HTTPException(status_code=response.status_code, detail=detail.decode())
            async for line in response.aiter_lines():
                if line.startswith("data:"):
                    await _persist_stream_snapshot(workflow_id, line[5:].strip())
                yield f"{line}\n"
                if not line:
                    yield "\n"
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=ORCHESTRATOR_UNAVAILABLE) from exc


@router.get("/status/{project_id}", responses={404: {"description": PROJECT_NOT_FOUND}, 502: {"description": ORCHESTRATOR_UNAVAILABLE}})
async def analysis_status(
    project_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: UserRecord = Depends(get_current_user),
):
    workflow_id, run = await _resolve_workflow(project_id, session, current_user)
    status = await _fetch_workflow_status(workflow_id)
    if run is not None:
        status.setdefault("run_id", run.run_id)
        status.setdefault("project_id", run.project_id)
    return status


@router.get("/{project_id}/findings", responses={404: {"description": PROJECT_NOT_FOUND}, 502: {"description": ORCHESTRATOR_UNAVAILABLE}})
async def analysis_findings(
    project_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: UserRecord = Depends(get_current_user),
):
    run_status = await analysis_status(project_id, session, current_user)
    result = run_status.get("result", {})
    return {
        "project_id": project_id,
        "findings": result.get("findings", []),
        "findings_count": len(result.get("findings", [])),
    }


@api_router.post("/analysis", responses={404: {"description": PROJECT_NOT_FOUND}, 502: {"description": ORCHESTRATOR_UNAVAILABLE}})
async def start_api_analysis(
    request: dict,
    session: AsyncSession = Depends(get_session),
    current_user: UserRecord = Depends(require_roles("Developer", "Administrator")),
):
    project_id = request.get("project_id")
    if not project_id:
        raise HTTPException(status_code=404, detail=PROJECT_NOT_FOUND)
    await require_owned_project(session, project_id, current_user)
    project_source_files = [file_payload(item) for item in await project_files(session, project_id)]
    started = await _create_workflow_run(
        project_id,
        project_source_files,
        request.get("project_context", {}),
        request.get("tools"),
        session,
    )
    return {**started, "status": "started"}


async def _api_status(run_id: str, session: AsyncSession, current_user: UserRecord) -> dict:
    run = await session.get(AnalysisRunRecord, run_id)
    project_id = run.project_id if run is not None else run_id
    await require_owned_project(session, project_id, current_user)
    workflow_id = run.workflow_id if run is not None else run_id
    status = await _fetch_workflow_status(workflow_id)
    if run is not None:
        await persist_snapshot(session, run, status)
        await session.commit()
        return {**status, "run_id": run.run_id, "project_id": run.project_id}
    return status


@api_router.get("/analysis/{run_id}/status", responses={404: {"description": ANALYSIS_RUN_NOT_FOUND}, 502: {"description": ORCHESTRATOR_UNAVAILABLE}})
async def api_analysis_status(
    run_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: UserRecord = Depends(get_current_user),
):
    status = await _api_status(run_id, session, current_user)
    return {
        "run_id": status.get("run_id", run_id),
        "project_id": status.get("project_id"),
        "status": status.get("status", "failed"),
        "current_agent": status.get("progress"),
        "started_at": status.get("started_at"),
        "completed_at": status.get("completed_at"),
        "error": status.get("error"),
    }


@api_router.post("/analysis/{run_id}/cancel", responses={404: {"description": ANALYSIS_RUN_NOT_FOUND}, 502: {"description": ORCHESTRATOR_UNAVAILABLE}})
async def api_cancel_analysis(
    run_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: UserRecord = Depends(require_roles("Developer", "Administrator")),
):
    run = await session.get(AnalysisRunRecord, run_id)
    if run is None:
        await require_owned_project(session, run_id, current_user)
        run = await latest_run_for_project(session, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=ANALYSIS_RUN_NOT_FOUND)
    await require_owned_project(session, run.project_id, current_user)
    try:
        async with httpx.AsyncClient(timeout=settings.request_timeout_seconds) as client:
            response = await client.post(
                f"{settings.orchestrator_url.rstrip('/')}/workflow/cancel/{run.workflow_id}",
                json={},
            )
            response.raise_for_status()
            snapshot = response.json()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=exc.response.status_code, detail=exc.response.text) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=ORCHESTRATOR_UNAVAILABLE) from exc
    await persist_snapshot(session, run, snapshot)
    await session.commit()
    return {"run_id": run.run_id, "project_id": run.project_id, "status": run.status}


@api_router.get("/analysis/{run_id}/results", responses={404: {"description": ANALYSIS_RUN_NOT_FOUND}, 502: {"description": ORCHESTRATOR_UNAVAILABLE}})
async def api_analysis_results(
    run_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: UserRecord = Depends(get_current_user),
):
    status = await _api_status(run_id, session, current_user)
    result = status.get("result", {})
    findings = result.get("findings", [])
    return {
        "run_id": run_id,
        "project_id": status.get("project_id"),
        "status": status.get("status", "failed"),
        "findings": findings,
        "summary": result.get("final_report", {}).get("findings_summary", {}),
        "result": result,
        "error": status.get("error"),
    }


@api_router.post(
    "/agents/code-quality/analyze",
    responses={502: {"description": "Code Quality service unavailable"}},
)
async def api_code_quality_analysis(request: dict):
    try:
        async with httpx.AsyncClient(timeout=settings.request_timeout_seconds) as client:
            response = await client.post(
                f"{settings.quality_service_url.rstrip('/')}/analyze",
                json=request,
            )
            response.raise_for_status()
            return response.json()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=exc.response.status_code, detail=exc.response.text) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Code Quality service unavailable") from exc