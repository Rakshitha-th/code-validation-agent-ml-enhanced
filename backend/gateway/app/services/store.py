from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ProjectFileRecord, ProjectRecord


async def save_project(
    session: AsyncSession,
    project_id: str,
    name: str,
    files: list[dict[str, str]],
    description: str | None = None,
    owner_id: str | None = None,
    *,
    replace_files: bool = True,
) -> ProjectRecord:
    project = await session.get(ProjectRecord, project_id)
    if project is None:
        project = ProjectRecord(
            project_id=project_id,
            owner_id=owner_id,
            name=name,
            description=description,
            status="uploaded",
        )
        session.add(project)
    else:
        project.name = name
        if description is not None:
            project.description = description

    if replace_files:
        await session.execute(delete(ProjectFileRecord).where(ProjectFileRecord.project_id == project_id))
        session.add_all([
            ProjectFileRecord(
                project_id=project_id,
                path=item["path"],
                content=item["content"],
                size_bytes=len(item["content"].encode("utf-8")),
            )
            for item in files
        ])
        project.file_count = len(files)
    await session.flush()
    await session.refresh(project)
    return project


async def project_files(session: AsyncSession, project_id: str) -> list[ProjectFileRecord]:
    result = await session.scalars(
        select(ProjectFileRecord)
        .where(ProjectFileRecord.project_id == project_id)
        .order_by(ProjectFileRecord.path)
    )
    return list(result)


def file_payload(record: ProjectFileRecord) -> dict[str, str]:
    return {"path": record.path, "content": record.content}


def project_summary(record: ProjectRecord, last_run: str | None = None) -> dict:
    created_at = record.created_at or datetime.now(timezone.utc)
    return {
        "project_id": record.project_id,
        "name": record.name,
        "description": record.description,
        "status": record.status,
        "file_count": record.file_count,
        "created_at": created_at.isoformat(),
        "last_run": last_run,
        "files_persisted": True,
    }