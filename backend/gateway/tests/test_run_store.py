import pytest

from app.models import AnalysisRunRecord
from app.services.run_store import normalize_terminal_validation_status


@pytest.mark.parametrize(
    "status",
    [
        "passed",
        "failed",
        "needs_review",
        "error",
        "tool_error",
        "skipped",
        "not_run",
        "blocked",
        "inconclusive",
    ],
)
def test_terminal_validation_status_preserves_known_outcomes(status):
    run = AnalysisRunRecord(
        run_id="run-1",
        project_id="project-1",
        workflow_id="workflow-1",
        status="completed",
        validation_status=status,
        project_context={},
        result={
            "validation_status": status,
            "validation_results": {"passed": False},
            "final_report": {"validation_status": status},
        },
    )

    assert normalize_terminal_validation_status(run) == status
    assert run.validation_status == status
    assert run.result["validation_status"] == status
    assert run.result["final_report"]["validation_status"] == status


@pytest.mark.parametrize(
    ("passed", "expected"),
    [(True, "passed"), (False, "failed")],
)
def test_legacy_terminal_runs_infer_status_when_status_is_missing(passed, expected):
    run = AnalysisRunRecord(
        run_id="run-2",
        project_id="project-2",
        workflow_id="workflow-2",
        status="completed",
        project_context={},
        result={"validation_results": {"passed": passed}},
    )

    assert normalize_terminal_validation_status(run) == expected
