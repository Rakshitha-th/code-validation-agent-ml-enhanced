import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_session
from app.models import AnalysisRunRecord, ProjectRecord, UserRecord
from app.security import get_current_user, require_owned_project
from app.services.run_store import normalize_terminal_validation_status

router = APIRouter(prefix="/results", tags=["results"], dependencies=[Depends(get_current_user)])
history_router = APIRouter(prefix="/api/history", tags=["history"], dependencies=[Depends(get_current_user)])


@history_router.get("")
async def list_history(
    limit: int = Query(default=50, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
    current_user: UserRecord = Depends(get_current_user),
) -> dict:
    rows = list((await session.execute(
        select(AnalysisRunRecord, ProjectRecord.name)
        .join(ProjectRecord, ProjectRecord.project_id == AnalysisRunRecord.project_id)
        .where(ProjectRecord.owner_id == current_user.user_id)
        .order_by(AnalysisRunRecord.started_at.desc())
        .limit(limit)
    )).all())
    items = []
    for run, project_name in rows:
        validation_status = normalize_terminal_validation_status(run)
        snapshot = run.result or {}
        report = snapshot.get("final_report") or {}
        items.append({
            "run_id": run.run_id,
            "project_id": run.project_id,
            "project_name": project_name,
            "status": run.status,
            "validation_status": validation_status or "unknown",
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "completed_at": run.completed_at.isoformat() if run.completed_at else None,
            "finding_count": len(snapshot.get("findings") or report.get("findings") or []),
            "retry_count": snapshot.get("retry_count", report.get("retry_count", 0)),
        })
    await session.commit()
    return {"items": items}


@router.get("/{project_id}", responses={502: {"description": "Orchestrator unavailable"}})
async def get_results(
    project_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: UserRecord = Depends(get_current_user),
):
    run = await session.scalar(
        select(AnalysisRunRecord)
        .where((AnalysisRunRecord.project_id == project_id) | (AnalysisRunRecord.run_id == project_id))
        .order_by(AnalysisRunRecord.started_at.desc())
        .limit(1)
    )
    if run is not None:
        project = await require_owned_project(session, run.project_id, current_user)
        normalize_terminal_validation_status(run)
        await session.commit()
        snapshot = run.result or {}
        report = snapshot.get("final_report") if snapshot else None
        return {
            "project_id": run.project_id,
            "run_id": run.run_id,
            "project_name": project.name,
            "status": run.status,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "completed_at": run.completed_at.isoformat() if run.completed_at else None,
            "report": report if run.status == "completed" else None,
            "result": snapshot if run.status == "completed" else None,
        }

    await require_owned_project(session, project_id, current_user)

    try:
        async with httpx.AsyncClient(timeout=settings.request_timeout_seconds) as client:
            response = await client.get(
                f"{settings.orchestrator_url.rstrip('/')}/workflow/status/{project_id}"
            )
            response.raise_for_status()
            status = response.json()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=exc.response.status_code, detail=exc.response.text) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Orchestrator unavailable") from exc

    if status.get("status") != "completed":
        return {"project_id": project_id, "status": status.get("status", "unknown"), "report": None}
    return {
        "project_id": project_id,
        "status": status.get("status"),
        "report": status.get("result", {}).get("final_report", {}),
        "result": status.get("result", {}),
    }