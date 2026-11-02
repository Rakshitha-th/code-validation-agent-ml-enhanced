"""
The LangGraph state that flows through every node of the orchestration
graph. This is the "single source of truth" object — every node reads
from it and returns a partial update that LangGraph merges in.
"""
from typing import TypedDict, Optional


class WorkflowState(TypedDict, total=False):
    project_id: str
    run_id: str

    # Input
    project_files: dict           # {path: content}
    project_context: dict
    tools: list[str]
    analysis_id: str
    code_structure: dict

    # Analysis stage
    findings: list                # merged, aggregated findings (dicts)
    prioritized_findings: list
    explanations: list[dict]
    code_report: dict
    recommendation_report: dict
    ai_recommendations_status: dict
    security_findings: list
    quality_findings: list
    quality_summary: dict
    resource_findings: list
    repair_attempts: list[dict]
    agent_errors: list[dict]
    stage_history: list[dict]
    workflow_trace: list[str]
    workflow_status: str
    quality_gate_status: str

    # Repair stage
    repair_attempt: int
    retry_count: int
    repaired_code: dict           # {path: content} after repair
    reanalysis_findings: list
    reanalysis_summary: dict
    best_repair_candidate: dict
    comparison: dict

    # Validation stage
    validation_results: dict
    sandbox_result: dict
    validation_status: str        # pending, not_run, passed, failed, needs_review, error, blocked, or inconclusive

    # Output
    final_code: dict
    final_report: dict
    error: Optional[str]
