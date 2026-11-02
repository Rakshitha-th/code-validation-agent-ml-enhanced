from datetime import datetime, timezone
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models import AnalysisRunRecord, ProjectRecord
from app.models import UserRecord
from app.security import get_current_user, require_owned_project, require_roles
from app.services.store import project_files, project_summary, save_project
from shared.languages import detect_language, is_source_path, validate_project_path
from shared.schemas.project import ProjectMeta
from shared.schemas.workflow import ProjectFile

router = APIRouter(prefix="/projects", tags=["projects"], dependencies=[Depends(get_current_user)])
api_router = APIRouter(prefix="/api/projects", tags=["projects"], dependencies=[Depends(get_current_user)])

class ProjectCreate(BaseModel):
    name: str = "Untitled project"
    files: list[ProjectFile] = Field(default_factory=list)
    source_code: str | None = None
    project_context: dict = Field(default_factory=dict)

    @field_validator("files")
    @classmethod
    def validate_files(cls, value: list[ProjectFile]) -> list[ProjectFile]:
        paths = [validate_project_path(item.path) for item in value]
        detect_language([path for path in paths if is_source_path(path)])
        if len(paths) != len(set(paths)):
            raise ValueError("project files must have unique paths")
        return [ProjectFile(path=path, content=item.content) for item, path in zip(value, paths)]


class ProjectPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None


def _project_meta(record: ProjectRecord) -> ProjectMeta:
    created_at = record.created_at or datetime.now(timezone.utc)
    return ProjectMeta(
        project_id=record.project_id,
        name=record.name,
        status=record.status,
        file_count=record.file_count,
        created_at=created_at.isoformat(),
    )


async def _create_project(project: ProjectCreate, session: AsyncSession, owner: UserRecord) -> ProjectMeta:
    project_id = f"proj-{uuid4().hex}"
    files = [item.model_dump() for item in project.files]
    if not files and project.source_code:
        files = [{"path": "main.py", "content": project.source_code}]
    record = await save_project(session, project_id, project.name, files, owner_id=owner.user_id)
    await session.commit()
    return _project_meta(record)


@router.post("", response_model=ProjectMeta)
async def create_project(project: ProjectMeta) -> ProjectMeta:
    """Accept project metadata using the shared project contract."""
    return project


@api_router.post("", response_model=ProjectMeta)
async def create_api_project(
    project: ProjectCreate,
    session: AsyncSession = Depends(get_session),
    current_user: UserRecord = Depends(require_roles("Developer", "Administrator")),
) -> ProjectMeta:
    return await _create_project(project, session, current_user)


@api_router.get("")
async def list_projects(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    search: str = "",
    sort: str = "created_at",
    session: AsyncSession = Depends(get_session),
    current_user: UserRecord = Depends(get_current_user),
) -> dict:
    statement = select(ProjectRecord).where(ProjectRecord.owner_id == current_user.user_id)
    if search.strip():
        statement = statement.where(ProjectRecord.name.ilike(f"%{search.strip()}%"))
    total = await session.scalar(select(func.count()).select_from(statement.subquery())) or 0
    if sort == "name":
        statement = statement.order_by(ProjectRecord.name.asc())
    else:
        statement = statement.order_by(ProjectRecord.created_at.desc())
    records = list((await session.scalars(statement.offset((page - 1) * page_size).limit(page_size))).all())
    items = []
    for record in records:
        latest_run = await session.scalar(
            select(AnalysisRunRecord)
            .where(AnalysisRunRecord.project_id == record.project_id)
            .order_by(AnalysisRunRecord.started_at.desc())
            .limit(1)
        )
        items.append(project_summary(record, latest_run.started_at.isoformat() if latest_run else None))
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@api_router.get("/{project_id}")
async def get_api_project(
    project_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: UserRecord = Depends(get_current_user),
) -> dict:
    record = await require_owned_project(session, project_id, current_user)
    files = await project_files(session, project_id)
    runs = list((await session.scalars(
        select(AnalysisRunRecord)
        .where(AnalysisRunRecord.project_id == project_id)
        .order_by(AnalysisRunRecord.started_at.desc())
    )).all())
    result = project_summary(record)
    result["files"] = [
        {"path": item.path, "size": item.size_bytes, "last_modified": item.updated_at.isoformat() if item.updated_at else None}
        for item in files
    ]
    result["run_history"] = [
        {
            "run_id": run.run_id,
            "project_id": run.project_id,
            "status": run.status,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        }
        for run in runs
    ]
    return result


@api_router.patch("/{project_id}")
async def update_api_project(
    project_id: str,
    changes: ProjectPatch,
    session: AsyncSession = Depends(get_session),
    current_user: UserRecord = Depends(require_roles("Developer", "Administrator")),
) -> dict:
    record = await require_owned_project(session, project_id, current_user)
    patch = changes.model_dump(exclude_unset=True)
    if "name" in patch:
        if not patch["name"].strip():
            raise HTTPException(status_code=422, detail="Project name cannot be empty")
        record.name = patch["name"].strip()
    if "description" in patch:
        record.description = patch["description"]
    await session.commit()
    await session.refresh(record)
    result = project_summary(record)
    result["files"] = [
        {"path": item.path, "size": item.size_bytes, "last_modified": item.updated_at.isoformat() if item.updated_at else None}
        for item in await project_files(session, project_id)
    ]
    runs = list((await session.scalars(
        select(AnalysisRunRecord)
        .where(AnalysisRunRecord.project_id == project_id)
        .order_by(AnalysisRunRecord.started_at.desc())
    )).all())
    result["run_history"] = [
        {
            "run_id": run.run_id,
            "project_id": run.project_id,
            "status": run.status,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        }
        for run in runs
    ]
    return result


@api_router.delete("/{project_id}", status_code=204)
async def delete_api_project(
    project_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: UserRecord = Depends(require_roles("Developer", "Administrator")),
) -> None:
    record = await require_owned_project(session, project_id, current_user)
    await session.delete(record)
    await session.commit()


@router.post("/upload")
async def upload_project(
    name: Annotated[str, Form(...)],
    files: Annotated[list[UploadFile], File(...)],
    session: AsyncSession = Depends(get_session),
    current_user: UserRecord = Depends(require_roles("Developer", "Administrator")),
) -> ProjectMeta:
    contents = []
    for file in files:
        try:
            path = validate_project_path(file.filename or "")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        contents.append({"path": path, "content": (await file.read()).decode("utf-8")})
    return await _create_project(ProjectCreate(name=name, files=contents), session, current_user)