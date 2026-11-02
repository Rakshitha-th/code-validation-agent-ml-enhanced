from collections import Counter, defaultdict
from datetime import datetime, time, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models import (
    AnalysisRunRecord,
    FindingRecord,
    ProjectFileRecord,
    ProjectRecord,
    ReportRecord,
    UserRecord,
    ValidationRecord,
)
from app.security import get_current_user
from shared.languages import LANGUAGE_ADAPTERS, language_for_path


router = APIRouter(
    prefix="/api/overview",
    tags=["overview"],
    dependencies=[Depends(get_current_user)],
)

SEVERITIES = ("critical", "high", "medium", "low", "info")
REPAIR_STATUSES = {"success", "partial", "failed", "needs_review", "running"}


@router.get("")
async def get_overview(
    session: AsyncSession = Depends(get_session),
    current_user: UserRecord = Depends(get_current_user),
) -> dict:
    project_count = await session.scalar(
        select(func.count(ProjectRecord.project_id))
        .where(ProjectRecord.owner_id == current_user.user_id)
    ) or 0
    run_scope = (
        select(AnalysisRunRecord.run_id)
        .join(ProjectRecord, ProjectRecord.project_id == AnalysisRunRecord.project_id)
        .where(ProjectRecord.owner_id == current_user.user_id)
    )
    run_count = await session.scalar(
        select(func.count()).select_from(run_scope.subquery())
    ) or 0
    midnight = datetime.combine(datetime.now(timezone.utc).date(), time.min, tzinfo=timezone.utc)
    runs_today = await session.scalar(
        select(func.count(AnalysisRunRecord.run_id))
        .join(ProjectRecord, ProjectRecord.project_id == AnalysisRunRecord.project_id)
        .where(
            ProjectRecord.owner_id == current_user.user_id,
            AnalysisRunRecord.started_at >= midnight,
        )
    ) or 0

    validations = list((await session.execute(
        select(ValidationRecord.passed)
        .join(AnalysisRunRecord, AnalysisRunRecord.run_id == ValidationRecord.run_id)
        .join(ProjectRecord, ProjectRecord.project_id == AnalysisRunRecord.project_id)
        .where(ProjectRecord.owner_id == current_user.user_id)
    )).scalars())
    counted_validations = sum(value is not None for value in validations)
    passed_validations = sum(value is True for value in validations)

    report_payloads = list((await session.execute(
        select(ReportRecord.payload)
        .join(AnalysisRunRecord, AnalysisRunRecord.run_id == ReportRecord.run_id)
        .join(ProjectRecord, ProjectRecord.project_id == AnalysisRunRecord.project_id)
        .where(ProjectRecord.owner_id == current_user.user_id)
    )).scalars())
    repair_counts = Counter(
        payload.get("repair_status")
        for payload in report_payloads
        if isinstance(payload, dict) and payload.get("repair_status") in REPAIR_STATUSES | {"not_run"}
    )
    repair_attempted = sum(count for status, count in repair_counts.items() if status != "not_run")

    project_files = list((await session.execute(
        select(ProjectFileRecord.project_id, ProjectFileRecord.path)
        .join(ProjectRecord, ProjectRecord.project_id == ProjectFileRecord.project_id)
        .where(ProjectRecord.owner_id == current_user.user_id)
    )).all())
    languages_by_project: dict[str, set[str]] = defaultdict(set)
    for project_id, path in project_files:
        language = language_for_path(path)
        if language:
            languages_by_project[project_id].add(language)
    language_project_counts = Counter(
        language
        for languages in languages_by_project.values()
        for language in languages
    )

    latest_rows = list((await session.execute(
        select(AnalysisRunRecord, ProjectRecord.name)
        .join(ProjectRecord, ProjectRecord.project_id == AnalysisRunRecord.project_id)
        .where(ProjectRecord.owner_id == current_user.user_id)
        .order_by(AnalysisRunRecord.started_at.desc())
        .limit(5)
    )).all())
    run_ids = [run.run_id for run, _ in latest_rows]
    finding_rows = []
    if run_ids:
        finding_rows = list((await session.execute(
            select(FindingRecord)
            .where(FindingRecord.run_id.in_(run_ids))
        )).scalars())
    findings_by_run: dict[str, list[FindingRecord]] = defaultdict(list)
    for finding in finding_rows:
        findings_by_run[finding.run_id].append(finding)

    recent_runs = []
    for run, project_name in latest_rows:
        context = run.project_context if isinstance(run.project_context, dict) else {}
        language = context.get("language")
        if language not in LANGUAGE_ADAPTERS:
            language = next(iter(languages_by_project.get(run.project_id, set())), None)
        recent_runs.append({
            "run_id": run.run_id,
            "project_id": run.project_id,
            "project_name": project_name,
            "status": run.status,
            "validation_status": run.validation_status or "unknown",
            "finding_count": len(findings_by_run[run.run_id]),
            "retry_count": (run.result or {}).get("retry_count", 0) if isinstance(run.result, dict) else 0,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "language": language,
        })

    latest = latest_rows[0][0] if latest_rows else None
    latest_findings = findings_by_run[latest.run_id] if latest else []
    severity_counts = Counter(
        (finding.severity or "info").lower()
        for finding in latest_findings
        if (finding.severity or "info").lower() in SEVERITIES
    )
    language_finding_counts = Counter()
    for finding in latest_findings:
        language = language_for_path(finding.file_path or "")
        if language:
            language_finding_counts[language] += 1
    security_findings = sum(
        finding.category.lower() == "security" or (finding.agent or "").lower() == "security"
        for finding in latest_findings
    )

    return {
        "metrics": {
            "project_count": project_count,
            "run_count": run_count,
            "runs_today": runs_today,
            "latest_security_findings": security_findings,
            "latest_high_security_findings": sum(
                (
                    finding.category.lower() == "security"
                    or (finding.agent or "").lower() == "security"
                )
                and finding.severity.lower() in {"high", "critical"}
                for finding in latest_findings
            ),
            "repair_successes": repair_counts["success"],
            "repair_attempted": repair_attempted,
            "validation_passed": passed_validations,
            "validation_counted": counted_validations,
        },
        "latest_run_id": latest.run_id if latest else None,
        "recent_runs": recent_runs,
        "latest_findings_by_severity": {
            severity: severity_counts[severity] for severity in SEVERITIES
        },
        "latest_findings_by_language": {
            language: language_finding_counts[language]
            for language in LANGUAGE_ADAPTERS
        },
        "languages": [
            {
                "key": key,
                "label": adapter["label"],
                "support": adapter["support"],
                "project_count": language_project_counts[key],
            }
            for key, adapter in LANGUAGE_ADAPTERS.items()
        ],
    }
