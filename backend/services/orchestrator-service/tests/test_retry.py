import asyncio

from app.graph.nodes import _validation_outcome, should_retry
from app.config import settings
from app.graph.nodes import validate
from app.graph.nodes import understand_project


def test_browser_runtime_unavailability_is_blocked_not_a_code_execution_failure():
    result = _validation_outcome(
        {
            "status": "unsupported_project",
            "executed": False,
            "error": "Browser APIs need a browser runtime.",
        },
        {"passed": True, "status": "passed"},
    )

    assert result == "blocked"


def test_should_retry_returns_finalize_on_pass():
    state = {"validation_status": "passed", "retry_count": 0}
    assert should_retry(state) == "finalize"


def test_should_retry_when_validation_passes_but_reanalysis_has_remaining_findings():
    state = {
        "validation_status": "passed",
        "retry_count": 0,
        "reanalysis_findings": [{"file": "main.c", "issue": "LANGUAGE_ANALYZER"}],
        "stage_history": [{"stage": "reanalyze", "status": "completed"}],
        "repair_attempts": [{"changed_files": ["main.c"]}],
    }

    assert should_retry(state) == "retry"


def test_should_retry_needs_review_when_reanalysis_has_findings_and_repair_made_progress():
    state = {
        "validation_status": "needs_review",
        "retry_count": 0,
        "reanalysis_findings": [{"file": "main.py", "issue": "C0114"}],
        "stage_history": [{"stage": "reanalyze", "status": "completed"}],
        "repair_attempts": [{"changed_files": ["main.py"]}],
    }

    assert should_retry(state) == "retry"


def test_should_retry_blocked_validation_when_reanalysis_still_has_repairable_findings():
    state = {
        "validation_status": "blocked",
        "retry_count": 0,
        "reanalysis_findings": [{"file": "main.ts", "issue": "@typescript-eslint/no-unused-vars"}],
        "stage_history": [{"stage": "reanalyze", "status": "completed"}],
        "repair_attempts": [{"changed_files": ["main.ts"]}],
        "sandbox_result": {
            "status": "unsupported_project",
            "executed": False,
            "error": "Browser APIs need a browser runtime.",
        },
        "validation_results": {"status": "passed"},
    }

    assert should_retry(state) == "retry"


def test_should_stop_blocked_validation_after_retry_makes_no_finding_progress():
    finding = {"file": "main.ts", "issue": "@typescript-eslint/no-unused-vars"}
    state = {
        "validation_status": "blocked",
        "retry_count": 1,
        "reanalysis_findings": [finding],
        "stage_history": [{"stage": "reanalyze", "status": "completed"}],
        "repair_attempts": [
            {"input_findings": [finding], "changed_files": ["main.ts"]},
            {"input_findings": [finding], "changed_files": ["main.ts"]},
        ],
        "sandbox_result": {"status": "unsupported_project", "executed": False},
        "validation_results": {"status": "passed"},
    }

    assert should_retry(state) == "finalize"


def test_should_finalize_needs_review_when_reanalysis_has_no_findings():
    state = {
        "validation_status": "needs_review",
        "retry_count": 0,
        "reanalysis_findings": [],
        "stage_history": [{"stage": "reanalyze", "status": "completed"}],
        "repair_attempts": [{"changed_files": ["main.py"]}],
    }

    assert should_retry(state) == "finalize"


def test_should_not_retry_passed_validation_when_same_findings_remain_after_repair():
    finding = {
        "category": "code_quality",
        "tool": "pylint",
        "file": "main.py",
        "issue": "C0114",
        "severity": "low",
    }
    state = {
        "validation_status": "passed",
        "retry_count": 2,
        "reanalysis_findings": [finding],
        "stage_history": [{"stage": "reanalyze", "status": "completed"}],
        "repair_attempts": [
            {"input_findings": [], "changed_files": ["main.py"]},
            {"input_findings": [finding], "changed_files": ["main.py"]},
        ],
    }

    assert should_retry(state) == "finalize"


def test_should_retry_passed_validation_when_reanalysis_finds_progress():
    previous = {
        "category": "code_quality",
        "tool": "pylint",
        "file": "main.py",
        "issue": "C0116",
    }
    remaining = {
        **previous,
        "issue": "C0114",
    }
    state = {
        "validation_status": "passed",
        "retry_count": 1,
        "reanalysis_findings": [remaining],
        "stage_history": [{"stage": "reanalyze", "status": "completed"}],
        "repair_attempts": [{
            "input_findings": [previous],
            "changed_files": ["main.py"],
        }],
    }

    assert should_retry(state) == "retry"


def test_should_retry_when_generic_analyzer_diagnostic_changes():
    previous = {
        "category": "code_quality",
        "tool": "ESLint",
        "file": "main.js",
        "issue": "LANGUAGE_ANALYZER",
        "description": "'window' is not defined.",
    }
    remaining = {
        **previous,
        "description": "'document' is not defined.",
    }
    state = {
        "validation_status": "passed",
        "retry_count": 1,
        "reanalysis_findings": [remaining],
        "stage_history": [{"stage": "reanalyze", "status": "completed"}],
        "repair_attempts": [
            {"input_findings": [], "changed_files": ["main.js"]},
            {"input_findings": [previous], "changed_files": ["main.js"]},
        ],
    }

    assert should_retry(state) == "retry"


def test_should_not_retry_passed_validation_without_a_changed_candidate_or_complete_reanalysis():
    unchanged = {
        "validation_status": "passed",
        "retry_count": 0,
        "reanalysis_findings": [{"file": "main.c", "issue": "LANGUAGE_ANALYZER"}],
        "stage_history": [{"stage": "reanalyze", "status": "completed"}],
        "repair_attempts": [{"changed_files": []}],
    }
    failed_reanalysis = {
        **unchanged,
        "repair_attempts": [{"changed_files": ["main.c"]}],
        "stage_history": [{"stage": "reanalyze", "status": "partial"}],
    }

    assert should_retry(unchanged) == "finalize"
    assert should_retry(failed_reanalysis) == "finalize"


def test_should_retry_returns_retry_when_under_budget():
    state = {"validation_status": "failed", "retry_count": 1}
    assert should_retry(state) == "retry"


def test_should_retry_returns_finalize_when_budget_exhausted():
    state = {"validation_status": "failed", "retry_count": settings.max_retries}
    assert should_retry(state) == "finalize"


def test_should_not_retry_an_identical_candidate_fingerprint():
    state = {
        "validation_status": "passed",
        "retry_count": 1,
        "reanalysis_findings": [{"file": "main.js", "issue": "LANGUAGE_ANALYZER"}],
        "stage_history": [{"stage": "reanalyze", "status": "completed"}],
        "repair_attempts": [
            {"changed_files": ["main.js"], "candidate_fingerprint": "same"},
            {"changed_files": ["main.js"], "candidate_fingerprint": "same"},
        ],
    }

    assert should_retry(state) == "finalize"


def test_should_not_repeat_a_rejected_candidate():
    rejected = {
        "path": "java_test.java",
        "code": "public class JavaTest {}\n",
        "reason": "public type does not match the filename",
        "decision": "rejected",
    }
    state = {
        "validation_status": "failed",
        "retry_count": 1,
        "repair_attempts": [
            {"changed_files": [], "rejected_candidates": [rejected]},
            {"changed_files": [], "rejected_candidates": [rejected]},
        ],
    }

    assert should_retry(state) == "finalize"


def test_should_not_repeat_the_same_no_change_failure_for_the_same_findings():
    finding = {"file": "main.js", "issue": "F821"}
    attempt = {
        "changed_files": [],
        "error": "Repair made no source changes; findings remain unresolved.",
        "input_findings": [finding],
    }
    state = {
        "validation_status": "failed",
        "retry_count": 1,
        "repair_attempts": [attempt, attempt],
    }

    assert should_retry(state) == "finalize"


def test_should_retry_does_not_repeat_unavailable_sandbox():
    state = {
        "validation_status": "failed",
        "retry_count": 0,
        "sandbox_result": {"executed": False, "error": "sandbox unavailable"},
    }
    assert should_retry(state) == "finalize"


def test_should_retry_does_not_repeat_unavailable_validation_service():
    state = {
        "validation_status": "failed",
        "retry_count": 0,
        "sandbox_result": {"executed": True},
        "validation_results": {"passed": False, "error": "validation service unavailable"},
    }
    assert should_retry(state) == "finalize"


def test_should_retry_keeps_retrying_repairable_validation_failures():
    state = {
        "validation_status": "failed",
        "retry_count": 1,
        "sandbox_result": {"executed": True},
        "validation_results": {"passed": False, "remaining_findings": [{"issue": "test failed"}]},
    }
    assert should_retry(state) == "retry"


def test_should_not_repeat_failed_repair_without_changes():
    state = {
        "validation_status": "failed",
        "retry_count": 0,
        "sandbox_result": {"executed": True},
        "validation_results": {"passed": False, "findings": [{"issue": "remaining"}]},
        "repair_attempts": [{"outcome": "failed", "error": "Repair service timed out.", "changed_files": []}],
    }
    assert should_retry(state) == "finalize"


def test_should_retry_invalid_model_finding_ids_within_budget():
    state = {
        "validation_status": "failed",
        "retry_count": 1,
        "sandbox_result": {"executed": True},
        "validation_results": {"passed": False, "findings": [{"issue": "SyntaxError"}]},
        "repair_attempts": [{
            "outcome": "failed",
            "error": "Model response contains an unknown resolved finding ID",
            "changed_files": [],
        }],
    }

    assert should_retry(state) == "retry"


def test_should_retry_rejected_repair_with_diagnostics(monkeypatch):
    state = {
        "validation_status": "needs_review",
        "retry_count": 0,
        "sandbox_result": {"executed": True, "passed": True},
        "validation_results": {"passed": True, "findings": []},
        "repair_attempts": [{
            "outcome": "failed",
            "error": "Repair rejected: main.py: Repair introduced Ruff findings: W292 at line 2, column 1",
            "changed_files": [],
        }],
    }

    assert should_retry(state) == "retry"


def test_should_retry_rejected_repair_when_sandbox_validation_passes():
    state = {
        "validation_status": "passed",
        "retry_count": 0,
        "sandbox_result": {"executed": True, "passed": True},
        "validation_results": {"status": "passed", "passed": True, "findings": []},
        "reanalysis_findings": [{"file": "java_test.java", "issue": "LANGUAGE_ANALYZER"}],
        "stage_history": [{"stage": "reanalyze", "status": "completed"}],
        "repair_attempts": [{
            "outcome": "failed",
            "error": "Repair rejected: java_test.java: public type does not match file name",
            "changed_files": [],
        }],
    }

    assert should_retry(state) == "retry"


def test_should_finalize_rejected_repair_when_retry_budget_is_exhausted():
    state = {
        "validation_status": "passed",
        "retry_count": settings.max_retries,
        "validation_results": {"status": "passed", "passed": True, "findings": []},
        "reanalysis_findings": [{"file": "java_test.java", "issue": "LANGUAGE_ANALYZER"}],
        "repair_attempts": [{
            "outcome": "failed",
            "error": "Repair rejected: java_test.java: public type does not match file name",
            "changed_files": [],
        }],
    }

    assert should_retry(state) == "finalize"


def test_should_retry_rejected_repair_stops_at_retry_limit(monkeypatch):
    state = {
        "validation_status": "needs_review",
        "retry_count": settings.max_retries,
        "sandbox_result": {"executed": True, "passed": True},
        "validation_results": {"passed": True, "findings": []},
        "repair_attempts": [{
            "outcome": "failed",
            "error": "Repair rejected: main.py: Repair introduced Ruff findings: W292 at line 2, column 1",
            "changed_files": [],
        }],
    }

    assert should_retry(state) == "finalize"


def test_should_retry_unchanged_repair_with_unresolved_findings():
    state = {
        "validation_status": "needs_review",
        "retry_count": 0,
        "sandbox_result": {"executed": True, "passed": True},
        "validation_results": {"passed": True, "findings": [{"code": "F401"}]},
        "repair_attempts": [{
            "outcome": "failed",
            "error": "Repair made no source changes; findings remain unresolved.",
            "changed_files": [],
        }],
    }

    assert should_retry(state) == "retry"


def test_should_retry_unchanged_candidate_to_allow_a_different_repair_strategy():
    finding = {"file": "main.py", "issue": "F821", "description": "Undefined name"}
    state = {
        "validation_status": "passed",
        "retry_count": 1,
        "reanalysis_findings": [finding],
        "stage_history": [{"stage": "reanalyze", "status": "completed"}],
        "repair_attempts": [{
            "candidate_fingerprint": "unchanged-source",
            "changed_files": [],
            "error": "Repair made no source changes; findings remain unresolved.",
            "input_findings": [finding],
        }],
    }

    assert should_retry(state) == "retry"


def test_should_not_retry_when_unchanged_response_repeats_a_prior_changed_candidate():
    state = {
        "validation_status": "passed",
        "retry_count": 1,
        "reanalysis_findings": [{"file": "main.js", "issue": "LANGUAGE_ANALYZER"}],
        "stage_history": [{"stage": "reanalyze", "status": "completed"}],
        "repair_attempts": [
            {"candidate_fingerprint": "same-candidate", "changed_files": ["main.js"]},
            {"candidate_fingerprint": "same-candidate", "changed_files": []},
        ],
    }

    assert should_retry(state) == "finalize"


def test_should_finalize_review_when_repair_has_no_error():
    state = {
        "validation_status": "needs_review",
        "retry_count": 0,
        "validation_results": {"passed": True, "findings": []},
        "repair_attempts": [{"outcome": "completed", "error": None, "changed_files": []}],
    }

    assert should_retry(state) == "finalize"


def test_repair_retry_receives_previous_rejection_diagnostics(monkeypatch):
    from app.graph import nodes

    captured = {}

    async def repair_candidate(_project_id, files, _findings, repair_feedback=""):
        captured["feedback"] = repair_feedback
        return {
            "status": "failed",
            "repaired_files": [{"path": path, "content": content} for path, content in files.items()],
            "repair_details": [{
                "path": "main.py",
                "changed": False,
                "explanation": "Repair rejected: main.py: Repair introduced Ruff findings: W292 at line 2, column 1",
                "candidate_code": "print('candidate')",
            }],
            "error": "Repair rejected: main.py: Repair introduced Ruff findings: W292 at line 2, column 1",
        }

    monkeypatch.setattr(nodes.repair_client, "repair", repair_candidate)
    result = asyncio.run(nodes.repair({
        "project_id": "repair-feedback",
        "project_files": {"main.py": "print('original')\n"},
        "findings": [],
        "repair_attempt": 1,
        "repair_attempts": [{
            "outcome": "failed",
            "error": "Repair rejected: main.py: Repair introduced Ruff findings: W292 at line 2, column 1",
            "repair_details": [{
                "path": "main.py",
                "explanation": "Repair rejected: Repair introduced Ruff findings: W292 at line 2, column 1",
                "candidate_code": "print('candidate')",
            }],
        }],
    }))

    assert captured["feedback"].startswith(
        "Repair rejected: main.py: Repair introduced Ruff findings: W292 at line 2, column 1\n"
        "Rejected candidate for main.py (do not repeat it):\n"
        "print('candidate')"
    )
    assert "choose a different repair strategy" in captured["feedback"]
    assert result["repair_attempts"][-1]["rejected_candidates"] == [{
        "path": "main.py",
        "code": "print('candidate')",
        "reason": "Repair rejected: main.py: Repair introduced Ruff findings: W292 at line 2, column 1",
        "decision": "rejected",
    }]


def test_repair_retry_targets_current_reanalysis_findings_and_candidate(monkeypatch):
    from app.graph import nodes

    captured = {}
    current_finding = {
        "finding_id": "remaining-c",
        "file": "main.c",
        "line_number": 12,
        "issue": "LANGUAGE_ANALYZER",
        "description": "Add a missing return.",
    }
    candidate = "int main(void) { return 1; }\n"

    async def repair_candidate(_project_id, files, findings, *_args):
        captured["source"] = files["main.c"]
        captured["findings"] = findings
        return {
            "status": "partial",
            "repaired_files": [{"path": "main.c", "content": candidate}],
            "unresolved_findings": findings,
        }

    monkeypatch.setattr(nodes.repair_client, "repair", repair_candidate)
    result = asyncio.run(nodes.repair({
        "project_id": "repair-current-findings",
        "repair_attempt": 1,
        "project_files": {"main.c": "int main(void) { return 0; }\n"},
        "repaired_code": {"main.c": candidate},
        "findings": [{"file": "main.c", "issue": "old finding"}],
        "reanalysis_findings": [current_finding],
        "repair_attempts": [{"changed_files": ["main.c"]}],
    }))

    assert captured["source"] == candidate
    assert captured["findings"] == [current_finding]
    assert result["repair_attempts"][-1]["input_findings"] == [current_finding]
    assert result["repaired_code"]["main.c"] == candidate


def test_repair_retry_receives_malformed_model_response_and_unresolved_findings(monkeypatch):
    from app.graph import nodes

    captured = {}

    async def repair_candidate(_project_id, files, _findings, repair_feedback=""):
        captured["feedback"] = repair_feedback
        return {
            "status": "failed",
            "repaired_files": [{"path": path, "content": content} for path, content in files.items()],
            "repair_details": [],
            "error": "Model response contains an unknown resolved finding ID",
        }

    monkeypatch.setattr(nodes.repair_client, "repair", repair_candidate)
    asyncio.run(nodes.repair({
        "project_id": "repair-malformed-response",
        "project_files": {"main.py": "def add(a, b)\n    return a - b\n"},
        "findings": [{"file": "main.py", "line": 1, "issue": "SYNTAX", "message": "expected ':'"}],
        "repair_attempt": 1,
        "repair_attempts": [{
            "outcome": "failed",
            "error": "Model response contains an unknown resolved finding ID",
            "repair_details": [{
                "path": "main.py",
                "explanation": "Model response contains an unknown resolved finding ID",
            }],
            "unresolved_findings": [{
                "file": "main.py",
                "line": 1,
                "rule_id": "SYNTAX",
                "message": "expected ':'",
            }],
        }],
    }))

    assert captured["feedback"].startswith(
        "Model response contains an unknown resolved finding ID\n"
        "main.py:1 SYNTAX: expected ':' Recommendation: Apply a minimal targeted fix."
    )
    assert "choose a different repair strategy" in captured["feedback"]


def test_disabled_validation_is_reported_as_not_run():
    result = understand_project({
        "project_id": "project-no-validation",
        "project_files": {"main.py": "print(1)\n"},
        "project_context": {"analysis_checks": {"final_validation": False}},
    })

    assert result["validation_status"] == "not_run"


def test_repair_attempt_records_input_and_candidate_snapshots(monkeypatch):
    from app.graph import nodes

    async def repair_candidate(_project_id, _files, _findings, _repair_feedback=""):
        return {
            "status": "partial",
            "repaired_files": [{"path": "main.py", "content": "print(2)\n"}],
            "unresolved_findings": [{"issue": "another finding"}],
            "repair_details": [{"path": "main.py", "changed": True, "explanation": "Updated output."}],
            "error": "One finding remains unresolved.",
        }

    monkeypatch.setattr(nodes.repair_client, "repair", repair_candidate)
    original = "print(1)\n"
    findings = [{"finding_id": "f-1", "issue": "Use corrected output"}]
    result = asyncio.run(nodes.repair({
        "project_id": "repair-snapshot",
        "project_files": {"main.py": original},
        "findings": findings,
    }))
    attempt = result["repair_attempts"][-1]

    assert result["repaired_code"]["main.py"] == "print(2)\n"
    assert attempt["input_findings"] == findings
    assert attempt["input_files"] == {"main.py": original}
    assert attempt["candidate_files"] == {"main.py": "print(2)\n"}
    assert attempt["outcome"] == "needs_review"
    assert attempt["repair_status"] == "needs_review"
    assert attempt["error"] == "One finding remains unresolved."


def test_validate_keeps_static_findings_when_sandbox_fails(monkeypatch):
    from app.graph import nodes

    monkeypatch.setattr(nodes.sandbox_client, "execute", lambda *args: _failed_sandbox())

    async def static_findings(*args):
        return {
            "passed": False,
            "findings": [{"issue": "syntax issue"}],
            "findings_count": 1,
            "error": None,
        }

    monkeypatch.setattr(nodes.validation_client, "validate", static_findings)
    result = asyncio.run(validate({"project_id": "p", "repaired_code": {"main.py": "print(1)"}}))

    assert result["validation_status"] == "error"
    assert result["validation_results"]["passed"] is False
    assert result["validation_results"]["findings"] == [{"issue": "syntax issue"}]
    assert result["agent_errors"][0]["agent"] == "sandbox"


def test_sandbox_and_validation_checks_run_concurrently(monkeypatch):
    from app.graph import nodes

    active_checks = 0
    maximum_active_checks = 0

    async def perform_check():
        nonlocal active_checks, maximum_active_checks
        active_checks += 1
        maximum_active_checks = max(maximum_active_checks, active_checks)
        await asyncio.sleep(0.03)
        active_checks -= 1

    async def sandbox(*_args, **_kwargs):
        await perform_check()
        return {"executed": True, "passed": True, "status": "completed"}

    async def validator(*_args, **_kwargs):
        await perform_check()
        return {"passed": True, "status": "passed", "findings": []}

    monkeypatch.setattr(nodes.sandbox_client, "execute", sandbox)
    monkeypatch.setattr(nodes.validation_client, "validate", validator)
    result = asyncio.run(nodes.validate({
        "project_id": "parallel-validation",
        "repaired_code": {"main.py": "print('ok')\n"},
        "findings": [],
    }))

    assert result["validation_status"] == "passed"
    assert maximum_active_checks == 2


def test_repair_attempt_is_only_successful_after_validation(monkeypatch):
    from app.graph import nodes

    async def sandbox_pass(*_args):
        return {"executed": True, "passed": True}

    async def validation_pass(*_args):
        return {"passed": True, "findings": [], "error": None}

    monkeypatch.setattr(nodes.sandbox_client, "execute", sandbox_pass)
    monkeypatch.setattr(nodes.validation_client, "validate", validation_pass)
    result = asyncio.run(validate({
        "project_id": "p-validated-repair",
        "repaired_code": {"main.py": "print(1)"},
        "repair_attempts": [{"iteration": 1, "outcome": "completed", "changed_files": ["main.py"]}],
    }))

    assert result["validation_status"] == "passed"
    assert result["repair_attempts"][-1]["outcome"] == "validated"


async def _failed_sandbox():
    return {"executed": False, "error": "sandbox unavailable"}
