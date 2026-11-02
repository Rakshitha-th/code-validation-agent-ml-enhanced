from app.aggregation.finding_aggregator import summarize
from app.config import settings


CHECK_NAMES = ("code_quality", "security", "bug_resource", "complexity", "automated_repair", "final_validation")


def _analysis_checks(state: dict) -> dict[str, bool]:
    raw_checks = (state.get("project_context") or {}).get("analysis_checks")
    if not isinstance(raw_checks, dict):
        return dict.fromkeys(CHECK_NAMES, True)
    return {name: bool(raw_checks.get(name, True)) for name in CHECK_NAMES}


def _quality_evidence_failed(state: dict, checks: dict[str, bool]) -> bool:
    if not (checks["code_quality"] or checks["complexity"]):
        return False
    comparison = state.get("comparison") or {}
    reanalysis_summary = state.get("reanalysis_summary") or {}
    quality_summary = (
        reanalysis_summary
        if comparison.get("status") == "complete" and reanalysis_summary
        else state.get("quality_summary") or {}
    )
    quality_gate = quality_summary.get("quality_gate_status") or quality_summary.get("quality_gate", {}).get("status")
    selected_tools = quality_summary.get("selected_tools")
    required_tools = selected_tools if selected_tools is not None else state.get("tools") or []
    completed_tools = {
        run.get("tool")
        for run in quality_summary.get("tool_runs", [])
        if isinstance(run, dict) and run.get("status") == "completed"
    }
    return quality_gate != "PASS" or not required_tools or not set(required_tools).issubset(completed_tools)


def _agent_error_is_selected(error: dict, checks: dict[str, bool], quality_selected: bool) -> bool:
    agent = error.get("agent")
    selected_agents = {
        "code_quality": quality_selected,
        "security": checks["security"],
        "bug_resource": checks["bug_resource"],
        "repair": checks["automated_repair"],
        "code_quality_reanalysis": checks["automated_repair"],
        "sandbox": checks["final_validation"],
        "validation": checks["final_validation"],
    }
    return selected_agents.get(agent, False)


def _repair_candidate_was_validated(attempts: list[dict]) -> bool:
    validated_attempts = [
        index for index, attempt in enumerate(attempts)
        if attempt.get("outcome") == "validated"
    ]
    if not validated_attempts:
        return False
    last_validated = validated_attempts[-1]
    return all(not attempt.get("changed_files") for attempt in attempts[last_validated + 1:])


def _repair_status(state: dict) -> str:
    attempts = state.get("repair_attempts", [])
    if not attempts:
        return "not_run"

    comparison = state.get("comparison") or {}
    if comparison.get("status") != "complete":
        return "needs_review"
    unresolved = (
        comparison.get("remaining_original_count", 0)
        or comparison.get("new_count", 0)
        or comparison.get("unverified_count", 0)
        or comparison.get("remaining_findings")
    )
    if not unresolved:
        return "success"
    if comparison.get("resolved_count", 0):
        return "partial"
    if comparison.get("new_count") or comparison.get("unverified_count"):
        return "needs_review"
    return "failed"


def _effective_validation_status(state: dict) -> str:
    status = state.get("validation_status", "unknown")
    if status != "passed":
        return status

    checks = _analysis_checks(state)
    quality_selected = checks["code_quality"] or checks["complexity"]
    if _quality_evidence_failed(state, checks):
        return "failed"
    if any(
        _agent_error_is_selected(error, checks, quality_selected)
        and error.get("agent") != "repair"
        for error in state.get("agent_errors", [])
    ):
        return "failed"

    comparison = state.get("comparison") or {}
    attempts = state.get("repair_attempts", [])
    if attempts and comparison.get("status") == "complete":
        if comparison.get("unverified_count", 0):
            return "needs_review"
    return status


def _unresolved_repair_error(state: dict, checks: dict[str, bool]) -> dict | None:
    if not checks["automated_repair"] or _repair_candidate_was_validated(state.get("repair_attempts", [])):
        return None
    return next(
        (error for error in state.get("agent_errors", []) if error.get("agent") == "repair"),
        None,
    )


def _workflow_status(state: dict) -> str:
    validation_status = _effective_validation_status(state)
    retry_count = state.get("retry_count", 0)
    agent_failures = state.get("agent_errors", [])
    comparison = state.get("comparison") or {}
    reanalysis_summary = state.get("reanalysis_summary") or {}
    quality_summary = (
        reanalysis_summary
        if comparison.get("status") == "complete" and reanalysis_summary
        else state.get("quality_summary") or {}
    )
    quality_gate = quality_summary.get("quality_gate_status") or quality_summary.get("quality_gate", {}).get("status")

    if validation_status == "passed":
        if _unresolved_repair_error(state, _analysis_checks(state)):
            return "AGENT_FAILURE"
        if state.get("repair_attempts") and comparison.get("status") != "complete":
            return "REVIEW_REQUIRED"
        if comparison.get("status") == "complete" and (
            comparison.get("remaining_original_count", 0)
            or comparison.get("new_count", 0)
        ):
            return "REVIEW_REQUIRED"
        return "SUCCESS"
    if validation_status == "error":
        return "ERROR"
    if validation_status in {"blocked", "inconclusive"}:
        return "BLOCKED"
    if validation_status == "needs_review":
        return "REVIEW_REQUIRED"
    if retry_count >= settings.max_retries:
        return "MAX_REPAIR_ATTEMPTS_REACHED"
    if agent_failures:
        return "AGENT_FAILURE" if quality_gate != "FAIL" else "PARTIAL_RESULT"
    if validation_status == "failed":
        return "FAILED"
    if quality_gate == "FAIL":
        return "REVIEW_REQUIRED"
    return "PARTIAL_RESULT"


def _workflow_status_reason(state: dict) -> str:
    validation_status = _effective_validation_status(state)
    comparison = state.get("comparison") or {}
    attempts = state.get("repair_attempts", [])
    rejected_attempt = next(
        (attempt for attempt in reversed(attempts) if attempt.get("candidate_rejection_reason")),
        None,
    )
    if validation_status in {"failed", "error"} and rejected_attempt:
        return (
            "The repair candidate was rejected during final verification: "
            f"{rejected_attempt['candidate_rejection_reason']}"
        )
    if validation_status == "passed":
        repair_error = _unresolved_repair_error(state, _analysis_checks(state))
        if repair_error:
            return (
                f"Required repair agent failed: {repair_error.get('error', 'No details returned')}. "
                "Validation itself passed."
            )
        if _workflow_status(state) == "REVIEW_REQUIRED":
            if comparison.get("status") != "complete":
                skipped_checks = comparison.get("skipped_checks", [])
                if skipped_checks:
                    return (
                        "Validation passed, but repair verification is incomplete because these "
                        f"checks were skipped or unavailable: {', '.join(skipped_checks)}."
                    )
                unverified_count = comparison.get("unverified_count", 0)
                if unverified_count:
                    suffix = "" if unverified_count == 1 else "s"
                    return (
                        f"Validation passed, but {unverified_count} finding{suffix} could not "
                        "be verified during re-analysis."
                    )
                return "Validation passed, but repair could not be fully verified by re-analysis."
            return (
                "Sandbox and validation passed, but one or more findings remain "
                "after re-analysis and require review."
            )
        return "All selected validation checks passed."
    if validation_status == "needs_review":
        attempts = state.get("repair_attempts", [])
        last_attempt = attempts[-1] if attempts else {}
        repair_error = last_attempt.get("error")
        if repair_error and str(repair_error).startswith((
            "Repair rejected:",
            "Repair made no source changes",
        )):
            if state.get("retry_count", 0) >= settings.max_retries:
                comparison = state.get("comparison") or {}
                remaining_findings = comparison.get("remaining_findings")
                remaining_count = (
                    len(remaining_findings)
                    if isinstance(remaining_findings, list)
                    else comparison.get("remaining_original_count", 0) + comparison.get("new_count", 0)
                )
                if remaining_count:
                    return (
                        f"Repair stopped after {len(attempts)} attempts; "
                        f"{remaining_count} finding(s) remain for review."
                    )
                return f"Repair stopped after {len(attempts)} attempts: {repair_error}"
            comparison = state.get("comparison") or {}
            if (
                comparison.get("status") == "complete"
                and (comparison.get("remaining_original_count", 0) or comparison.get("new_count", 0))
                and (state.get("validation_results") or {}).get("status") == "passed"
            ):
                return (
                    "Sandbox and validation passed, but one or more findings remain "
                    f"after re-analysis. Latest repair attempt: {repair_error}"
                )
            return f"Repair needs another attempt: {repair_error}"
        comparison = state.get("comparison") or {}
        if comparison.get("status") == "complete" and (
            comparison.get("remaining_original_count", 0) or comparison.get("new_count", 0)
        ):
            return (
                "Sandbox and validation passed, but one or more findings remain "
                "after re-analysis and require review."
            )
        unverified_count = comparison.get("unverified_count", 0)
        if unverified_count:
            suffix = "" if unverified_count == 1 else "s"
            return f"Validation completed, but {unverified_count} finding{suffix} could not be verified."
        return "Validation completed, but one or more findings require human review."
    if validation_status in {"blocked", "inconclusive"}:
        return "A required input or prerequisite prevented a complete validation result."
    if validation_status == "error":
        validation_error = (state.get("validation_results") or {}).get("error")
        if validation_error:
            return str(validation_error)
        sandbox_error = (state.get("sandbox_result") or {}).get("error")
        if sandbox_error:
            return str(sandbox_error)
        return "A required validation operation could not complete."

    checks = _analysis_checks(state)
    if checks["code_quality"] or checks["complexity"]:
        comparison = state.get("comparison") or {}
        final_quality = (
            state.get("reanalysis_summary") or {}
            if comparison.get("status") == "complete"
            else state.get("quality_summary") or {}
        )
        quality_gate = final_quality.get("quality_gate_status") or final_quality.get("quality_gate", {}).get("status")
        if quality_gate == "FAIL":
            validation_result = state.get("validation_results") or {}
            repair_error = next(
                (
                    attempt.get("error")
                    for attempt in reversed(state.get("repair_attempts", []))
                    if attempt.get("error")
                ),
                None,
            )
            if validation_result.get("status") == "passed":
                reason = "Sandbox/compiler validation passed, but the code-quality gate failed."
                if repair_error:
                    reason += f" The repair candidate was rejected: {str(repair_error)[:300]}"
                return reason
            return "One or more required code-quality thresholds failed."

    for error in state.get("agent_errors", []):
        if _agent_error_is_selected(error, checks, checks["code_quality"] or checks["complexity"]):
            return f"Required {error.get('agent', 'workflow')} agent failed: {error.get('error', 'No details returned')}"
    if (state.get("validation_results") or {}).get("findings"):
        return "Final validation returned blocking findings."
    return "The workflow did not meet all required acceptance criteria."


def build_report(state: dict) -> dict:
    findings = state.get("findings", [])
    quality_summary = state.get("quality_summary", {})
    reanalysis_summary = state.get("reanalysis_summary") or {}
    comparison = state.get("comparison") or {}
    final_quality_summary = (
        reanalysis_summary
        if comparison.get("status") == "complete" and reanalysis_summary
        else quality_summary
    )
    validation_status = _effective_validation_status(state)
    report_state = {**state, "validation_status": validation_status}
    workflow_status = _workflow_status(report_state)
    workflow_status_reason = _workflow_status_reason(report_state)
    repair_status = _repair_status(report_state)
    workflow_summary = {
        "workflow_status": workflow_status,
        "workflow_status_reason": workflow_status_reason,
        "repair_status": repair_status,
        "total_findings": len(findings),
        "findings_by_severity": summarize(findings).get("by_severity", {}),
        "quality_score": final_quality_summary.get("overall_score", final_quality_summary.get("quality_score")),
        "quality_gate": final_quality_summary.get("quality_gate_status") or final_quality_summary.get("quality_gate", {}).get("status"),
        "repair_attempts": state.get("repair_attempts", []),
        "validation_result": state.get("validation_results", {}),
        "agent_failures": state.get("agent_errors", []),
        "workflow_duration_seconds": state.get("workflow_duration_seconds"),
    }
    return {
        "project_id": state["project_id"],
        "run_id": state.get("run_id"),
        "analysis_id": state.get("analysis_id") or state.get("run_id") or state["project_id"],
        "validation_status": validation_status,
        "repair_status": repair_status,
        "workflow_status": workflow_status,
        "workflow_status_reason": workflow_status_reason,
        "retry_count": state.get("retry_count", 0),
        "max_retries": settings.max_retries,
        "findings_summary": summarize(findings),
        "findings": findings,
        "quality_summary": quality_summary,
        "workflow_summary": workflow_summary,
        "prioritized_findings": state.get("prioritized_findings", findings),
        "explanations": state.get("explanations", []),
        "code_report": state.get("code_report", {}),
        "recommendation_report": state.get("recommendation_report", {}),
        "ai_recommendations_status": state.get("ai_recommendations_status", {"status": "disabled"}),
        "reanalysis_findings": state.get("reanalysis_findings", []),
        "reanalysis_summary": state.get("reanalysis_summary", {}),
        "comparison": comparison,
        "validation_results": state.get("validation_results", {}),
        "sandbox_result": state.get("sandbox_result", {}),
        "agent_errors": state.get("agent_errors", []),
        "stage_history": state.get("stage_history", []),
        "workflow_trace": state.get("workflow_trace", [item.get("stage") for item in state.get("stage_history", [])]),
        "analysis_checks": state.get("project_context", {}).get("analysis_checks"),
        "sandbox_stdin": (state.get("project_context") or {}).get("sandbox_stdin", ""),
        "test_cases": (state.get("project_context") or {}).get("test_cases", []),
        "project_type": (state.get("project_context") or {}).get("project_type"),
        "code_structure": state.get("code_structure", {}),
        "repair_attempts": state.get("repair_attempts", []),
        "final_code": state.get("repaired_code") or state.get("project_files", {}),
        "recommendations": [
            finding.get("recommendation")
            for finding in findings
            if finding.get("recommendation")
        ],
    }