from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models import UserRecord
from app.security import get_current_user, require_owned_project, require_roles
from app.services.store import file_payload, project_files, save_project
from shared.languages import detect_language, is_source_path, validate_project_path
from shared.schemas.workflow import ProjectFile

router = APIRouter(prefix="/api/files", tags=["files"], dependencies=[Depends(get_current_user)])


class SaveFilesRequest(BaseModel):
    project_id: str
    files: list[ProjectFile]

    @field_validator("files")
    @classmethod
    def validate_files(cls, files: list[ProjectFile]) -> list[ProjectFile]:
        normalized = []
        paths = set()
        for item in files:
            path = validate_project_path(item.path)
            if path in paths:
                raise ValueError("Project files must have unique paths.")
            paths.add(path)
            normalized.append(ProjectFile(path=path, content=item.content))
        detect_language([path for path in paths if is_source_path(path)])
        return normalized


@router.get("")
async def get_project_files(
    project_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: UserRecord = Depends(get_current_user),
) -> dict:
    await require_owned_project(session, project_id, current_user)
    records = await project_files(session, project_id)
    return {"project_id": project_id, "files": [file_payload(record) for record in records]}


@router.post("")
async def save_project_files(
    request: SaveFilesRequest,
    session: AsyncSession = Depends(get_session),
    current_user: UserRecord = Depends(require_roles("Developer", "Administrator")),
) -> dict:
    project = await require_owned_project(session, request.project_id, current_user)
    files = [item.model_dump() for item in request.files]
    await save_project(
        session,
        project.project_id,
        project.name,
        files,
        description=project.description,
    )
    await session.commit()
    return {"project_id": project.project_id, "files": files}