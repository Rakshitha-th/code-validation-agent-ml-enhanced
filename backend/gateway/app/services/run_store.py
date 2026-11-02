from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AnalysisRunRecord,
    FindingRecord,
    ProjectRecord,
    RepairRecord,
    ReportRecord,
    ToolResultRecord,
    ValidationRecord,
    WorkflowEventRecord,
)
from app.services.store import save_project

TERMINAL_RUN_STATUSES = {"completed", "failed", "cancelled"}
KNOWN_VALIDATION_STATUSES = {
    "passed",
    "failed",
    "needs_review",
    "error",
    "tool_error",
    "skipped",
    "not_run",
    "blocked",
    "inconclusive",
}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _run_result_and_report(run: AnalysisRunRecord) -> tuple[dict, dict]:
    result = run.result if isinstance(run.result, dict) else {}
    report = result.get("final_report") if isinstance(result.get("final_report"), dict) else {}
    return result, report


def _validation_is_disabled(run: AnalysisRunRecord, report: dict) -> bool:
    report_checks = report.get("analysis_checks")
    checks = report_checks if isinstance(report_checks, dict) else None
    if checks is None and isinstance(run.project_context, dict):
        checks = run.project_context.get("analysis_checks")
    return isinstance(checks, dict) and checks.get("final_validation") is False


def _terminal_validation_status(
    run: AnalysisRunRecord,
    result: dict,
    report: dict,
) -> str:
    validation = result.get("validation_results") or report.get("validation_results") or {}
    status = run.validation_status or result.get("validation_status") or report.get("validation_status")
    if status in KNOWN_VALIDATION_STATUSES:
        return status
    if _validation_is_disabled(run, report):
        return "not_run"
    if isinstance(validation, dict) and isinstance(validation.get("passed"), bool):
        return "passed" if validation["passed"] else "failed"
    return "blocked" if run.status == "failed" else "inconclusive"


def normalize_terminal_validation_status(run: AnalysisRunRecord) -> str | None:
    if run.status not in TERMINAL_RUN_STATUSES:
        return run.validation_status

    result, report = _run_result_and_report(run)
    status = _terminal_validation_status(run, result, report)

    run.validation_status = status
    if result:
        result["validation_status"] = status
        if report:
            report["validation_status"] = status
        run.result = deepcopy(result)
    return status


async def prepare_project(
    session: AsyncSession,
    project_id: str,
    files: list[dict[str, str]],
    name: str = "Python project",
) -> ProjectRecord:
    current = await session.get(ProjectRecord, project_id)
    if current is not None:
        name = current.name
    return await save_project(session, project_id, name, files)


async def create_run(
    session: AsyncSession,
    project_id: str,
    workflow_id: str | None,
    tools: list[str] | None,
    project_context: dict,
) -> AnalysisRunRecord:
    run_id = f"run-{uuid4().hex}"
    record = AnalysisRunRecord(
        run_id=run_id,
        project_id=project_id,
        workflow_id=workflow_id or run_id,
        status="submitted",
        progress="queued",
        tools=tools or [],
        project_context=project_context,
    )
    session.add(record)
    session.add(WorkflowEventRecord(
        run_id=record.run_id,
        event="submitted",
        status="submitted",
        message="Analysis submitted to the orchestrator.",
        payload={"workflow_id": record.workflow_id},
    ))
    await session.flush()
    return record


async def latest_run_for_workflow(session: AsyncSession, workflow_id: str) -> AnalysisRunRecord | None:
    return await session.scalar(
        select(AnalysisRunRecord)
        .where(AnalysisRunRecord.workflow_id == workflow_id)
        .order_by(AnalysisRunRecord.started_at.desc())
        .limit(1)
    )


async def latest_run_for_project(session: AsyncSession, project_id: str) -> AnalysisRunRecord | None:
    return await session.scalar(
        select(AnalysisRunRecord)
        .where(AnalysisRunRecord.project_id == project_id)
        .order_by(AnalysisRunRecord.started_at.desc())
        .limit(1)
    )


async def persist_snapshot(
    session: AsyncSession,
    run: AnalysisRunRecord,
    snapshot: dict,
) -> None:
    previous = (run.status, run.progress)
    await _update_run_status(session, run, snapshot)
    result = _snapshot_result(run, snapshot)

    if (run.status, run.progress) != previous:
        _append_workflow_event(session, run, snapshot)
    if run.status in TERMINAL_RUN_STATUSES:
        run.completed_at = run.completed_at or now_utc()
        normalize_terminal_validation_status(run)
    if result is not None:
        await _persist_result_children(session, run.run_id, result, result["final_report"])
    await session.flush()


async def _update_run_status(session: AsyncSession, run: AnalysisRunRecord, snapshot: dict) -> None:
    status = snapshot.get("status")
    if isinstance(status, str):
        run.status = status
        project = await session.get(ProjectRecord, run.project_id)
        if project is not None:
            project.status = {
                "submitted": "analyzing",
                "working": "analyzing",
                "completed": "completed",
                "failed": "failed",
                "cancelled": "cancelled",
            }.get(status, project.status)
    progress = snapshot.get("progress")
    if isinstance(progress, str):
        run.progress = progress
    error = snapshot.get("error")
    if isinstance(error, str):
        run.error = error
    validation_status = snapshot.get("validation_status")
    if isinstance(validation_status, str):
        run.validation_status = validation_status


def _snapshot_result(run: AnalysisRunRecord, snapshot: dict) -> dict | None:
    result = snapshot.get("result")
    if not isinstance(result, dict):
        return None
    report = result.get("final_report")
    if not isinstance(report, dict):
        run.result = result
        return None
    run.result = result
    run.validation_status = result.get("validation_status") or report.get("validation_status")
    return result


def _append_workflow_event(session: AsyncSession, run: AnalysisRunRecord, snapshot: dict) -> None:
    session.add(WorkflowEventRecord(
        run_id=run.run_id,
        event=run.progress or "workflow",
        status=str(snapshot.get("stage_status") or run.status),
        message=snapshot.get("message") or snapshot.get("error"),
        payload={key: value for key, value in snapshot.items() if key != "result"},
    ))


async def _persist_result_children(
    session: AsyncSession,
    run_id: str,
    result: dict,
    report: dict,
) -> None:
    for model in (FindingRecord, ToolResultRecord, RepairRecord, ValidationRecord, ReportRecord):
        await session.execute(delete(model).where(model.run_id == run_id))

    _persist_findings(session, run_id, result.get("findings") or report.get("findings") or [])
    quality = result.get("quality_summary") or report.get("quality_summary") or {}
    _persist_tool_results(session, run_id, quality.get("tool_runs") or [])
    _persist_repairs(session, run_id, result.get("repair_attempts") or report.get("repair_attempts") or [])
    _persist_validation_report(session, run_id, result, report)


def _persist_findings(session: AsyncSession, run_id: str, findings: list) -> None:
    for index, finding in enumerate(findings):
        if not isinstance(finding, dict):
            continue
        session.add(FindingRecord(
            run_id=run_id,
            finding_id=str(finding.get("finding_id") or f"{run_id}-finding-{index}"),
            agent=finding.get("agent") or finding.get("detected_by"),
            category=str(finding.get("category") or "unknown"),
            severity=str(finding.get("severity") or "info"),
            file_path=finding.get("file"),
            line_number=finding.get("line_number"),
            status=str(finding.get("status") or "open"),
            payload=finding,
        ))

def _persist_tool_results(session: AsyncSession, run_id: str, tool_runs: list) -> None:
    for tool in tool_runs:
        if not isinstance(tool, dict) or not tool.get("tool"):
            continue
        session.add(ToolResultRecord(
            run_id=run_id,
            tool_name=str(tool["tool"]),
            status=str(tool.get("status") or "unknown"),
            duration_ms=tool.get("duration_ms"),
            error=tool.get("error"),
            payload=tool,
        ))

def _persist_repairs(session: AsyncSession, run_id: str, repairs: list) -> None:
    for index, repair in enumerate(repairs, start=1):
        if isinstance(repair, dict):
            iteration = int(repair.get("iteration") or index)
            session.add(RepairRecord(
                run_id=run_id,
                iteration=iteration,
                status=str(repair.get("outcome") or repair.get("status") or "unknown"),
                payload=repair,
            ))

def _persist_validation_report(session: AsyncSession, run_id: str, result: dict, report: dict) -> None:
    validation = result.get("validation_results") or report.get("validation_results") or {}
    if isinstance(validation, dict) and validation:
        session.add(ValidationRecord(
            run_id=run_id,
            passed=validation.get("passed") if isinstance(validation.get("passed"), bool) else None,
            payload=validation,
        ))

    session.add(ReportRecord(run_id=run_id, payload=report))