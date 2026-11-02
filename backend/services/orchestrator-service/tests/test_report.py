from app.reporting.report_builder import build_report
from app.graph.nodes import finalize


def test_selected_quality_gate_failure_cannot_report_validation_pass():
	report = build_report({
		"project_id": "quality-failure",
		"validation_status": "passed",
		"project_context": {"analysis_checks": {
			"code_quality": True,
			"security": False,
			"bug_resource": False,
			"complexity": True,
			"automated_repair": False,
			"final_validation": True,
		}},
		"quality_summary": {"quality_gate_status": "FAIL"},
		"agent_errors": [{"agent": "code_quality", "error": "pylint: failed"}],
	})

	assert report["validation_status"] == "failed"
	assert report["workflow_status"] != "SUCCESS"


def test_quality_failure_reason_distinguishes_passed_compiler_and_rejected_repair():
	report = build_report({
		"project_id": "java-repair-rejected",
		"validation_status": "passed",
		"project_context": {"analysis_checks": {
			"code_quality": True,
			"security": True,
			"bug_resource": True,
			"complexity": True,
			"automated_repair": True,
			"final_validation": True,
		}},
		"quality_summary": {"quality_gate_status": "FAIL"},
		"validation_results": {"status": "passed", "passed": True},
		"repair_attempts": [{
			"error": "Repair rejected: java_test.java: public type does not match file name",
		}],
	})

	assert report["validation_status"] == "failed"
	assert "Sandbox/compiler validation passed" in report["workflow_status_reason"]
	assert "repair candidate was rejected" in report["workflow_status_reason"]
	assert "java_test.java" in report["workflow_status_reason"]


def test_finalizer_persists_effective_validation_status():
	result = finalize({
		"project_id": "quality-failure",
		"validation_status": "passed",
		"project_files": {"main.py": "print(1)\n"},
		"project_context": {"analysis_checks": {
			"code_quality": True,
			"security": False,
			"bug_resource": False,
			"complexity": True,
			"automated_repair": False,
			"final_validation": True,
		}},
		"quality_summary": {"quality_gate_status": "FAIL"},
		"agent_errors": [{"agent": "code_quality", "error": "ruff unavailable"}],
	})

	assert result["validation_status"] == "failed"
	assert result["final_report"]["validation_status"] == "failed"


def test_unselected_quality_failure_does_not_override_selected_validation():
	report = build_report({
		"project_id": "quality-disabled",
		"validation_status": "passed",
		"project_context": {"analysis_checks": {
			"code_quality": False,
			"security": False,
			"bug_resource": False,
			"complexity": False,
			"automated_repair": False,
			"final_validation": True,
		}},
		"quality_summary": {"quality_gate_status": "FAIL"},
		"agent_errors": [{"agent": "code_quality", "error": "ruff: unavailable"}],
	})

	assert report["validation_status"] == "passed"


def test_selected_quality_without_complete_tool_evidence_cannot_report_pass():
	report = build_report({
		"project_id": "missing-quality-evidence",
		"validation_status": "passed",
		"project_context": {"analysis_checks": {
			"code_quality": True,
			"security": False,
			"bug_resource": False,
			"complexity": False,
			"automated_repair": False,
			"final_validation": True,
		}},
		"tools": ["ruff"],
		"quality_summary": {
			"quality_gate_status": "PASS",
			"tool_runs": [{"tool": "ruff", "status": "unavailable"}],
		},
	})

	assert report["validation_status"] == "failed"


def test_python_quality_gate_uses_selected_tools_as_expected_analyzers():
	report = build_report({
		"project_id": "python-quality-evidence",
		"validation_status": "passed",
		"project_context": {"analysis_checks": {
			"code_quality": True,
			"security": False,
			"bug_resource": False,
			"complexity": False,
			"automated_repair": False,
			"final_validation": True,
		}},
		"tools": ["ruff"],
		"quality_summary": {
			"quality_gate_status": "PASS",
			"selected_tools": ["ruff"],
			"tool_runs": [{"tool": "ruff", "status": "completed"}],
		},
	})

	assert report["validation_status"] == "passed"


def test_non_python_quality_gate_uses_effective_language_analyzer():
	report = build_report({
		"project_id": "javascript-quality-evidence",
		"validation_status": "passed",
		"project_context": {"analysis_checks": {
			"code_quality": True,
			"security": False,
			"bug_resource": False,
			"complexity": False,
			"automated_repair": False,
			"final_validation": True,
		}},
		"tools": ["ruff"],
		"quality_summary": {
			"quality_gate_status": "PASS",
			"selected_tools": ["ESLint"],
			"tool_runs": [{"tool": "ESLint", "status": "completed"}],
		},
	})

	assert report["validation_status"] == "passed"


def test_non_python_quality_gate_fails_when_effective_analyzer_did_not_complete():
	report = build_report({
		"project_id": "javascript-quality-incomplete",
		"validation_status": "passed",
		"project_context": {"analysis_checks": {
			"code_quality": True,
			"security": False,
			"bug_resource": False,
			"complexity": False,
			"automated_repair": False,
			"final_validation": True,
		}},
		"tools": ["ruff"],
		"quality_summary": {
			"quality_gate_status": "PASS",
			"selected_tools": ["ESLint"],
			"tool_runs": [{"tool": "ESLint", "status": "unavailable"}],
		},
	})

	assert report["validation_status"] == "failed"


def test_report_uses_completed_reanalysis_score_as_final_without_fabricating_missing_scores():
	report = build_report({
	    "project_id": "final-score",
	    "validation_status": "needs_review",
	    "quality_summary": {"quality_score": 78},
	    "reanalysis_summary": {"quality_score": 98},
	    "comparison": {"status": "complete"},
	})

	assert report["workflow_summary"]["quality_score"] == 98
	assert report["workflow_summary"]["quality_gate"] is None

	no_scores = build_report({
	    "project_id": "missing-score",
	    "validation_status": "error",
	})
	assert no_scores["workflow_summary"]["quality_score"] is None
	assert no_scores["workflow_status"] == "ERROR"
	assert report["workflow_status"] != "SUCCESS"


def test_report_keeps_rejected_repair_and_validation_reason_visible():
	report = build_report({
		"project_id": "rejected-repair",
		"validation_status": "needs_review",
		"project_context": {"analysis_checks": {
			"code_quality": True,
			"security": False,
			"bug_resource": False,
			"complexity": True,
			"automated_repair": True,
			"final_validation": True,
		}},
		"repair_attempts": [{
			"outcome": "failed",
			"error": "Repair rejected: Repair introduced Ruff findings: W292 at line 2, column 1",
			"rejected_candidates": [{"path": "main.py", "code": "print('candidate')"}],
		}],
		"validation_results": {
			"status": "needs_review",
			"findings": [{"code": "E305", "file": "main.py", "line": 4}],
		},
	})

	assert report["workflow_status"] == "REVIEW_REQUIRED"
	assert "Repair needs another attempt" in report["workflow_status_reason"]
	assert report["repair_attempts"][0]["rejected_candidates"][0]["code"] == "print('candidate')"


def test_successful_retry_can_clear_prior_repair_attempt_error():
	report = build_report({
		"project_id": "repair-retry-succeeded",
		"validation_status": "passed",
		"project_context": {"analysis_checks": {
			"code_quality": False,
			"security": False,
			"bug_resource": False,
			"complexity": False,
			"automated_repair": True,
			"final_validation": True,
		}},
		"repair_attempts": [
			{"iteration": 1, "outcome": "failed", "error": "Repair rejected: W292"},
			{"iteration": 2, "outcome": "validated", "validation_status": "passed"},
		],
		"comparison": {
			"status": "complete",
			"remaining_original_count": 0,
			"new_count": 0,
			"resolved_count": 1,
		},
		"agent_errors": [{"agent": "repair", "error": "Repair rejected: W292"}],
	})

	assert report["validation_status"] == "passed"
	assert report["workflow_status"] == "SUCCESS"


def test_repair_failure_does_not_rewrite_successful_validation_status():
	report = build_report({
		"project_id": "repair-failure-validation-passed",
		"validation_status": "passed",
		"project_context": {"analysis_checks": {
			"code_quality": False,
			"security": False,
			"bug_resource": False,
			"complexity": False,
			"automated_repair": True,
			"final_validation": True,
		}},
		"repair_attempts": [{
			"outcome": "failed",
			"changed_files": [],
			"error": "Repair made no source changes; findings remain unresolved.",
		}],
		"agent_errors": [{
			"agent": "repair",
			"error": "Repair made no source changes; findings remain unresolved.",
		}],
		"comparison": {
			"status": "complete",
			"remaining_original_count": 1,
			"new_count": 0,
		},
		"validation_results": {"status": "passed", "passed": True, "findings": []},
	})

	assert report["validation_status"] == "needs_review"
	assert report["repair_status"] == "failed"
	assert report["workflow_status"] == "REVIEW_REQUIRED"
	assert "Sandbox and validation passed" in report["workflow_status_reason"]


def test_unchanged_failed_retry_does_not_override_validation_of_last_candidate():
	report = build_report({
		"project_id": "validated-candidate-after-no-progress",
		"validation_status": "passed",
		"project_context": {"analysis_checks": {
			"code_quality": True,
			"security": False,
			"bug_resource": False,
			"complexity": False,
			"automated_repair": True,
			"final_validation": True,
		}},
		"quality_summary": {
			"quality_gate_status": "PASS",
			"selected_tools": ["ruff"],
			"tool_runs": [{"tool": "ruff", "status": "completed"}],
		},
		"reanalysis_summary": {
			"quality_gate_status": "PASS",
			"selected_tools": ["ruff"],
			"tool_runs": [{"tool": "ruff", "status": "completed"}],
		},
		"repair_attempts": [
			{"outcome": "validated", "changed_files": ["main.py"]},
			{"outcome": "failed", "changed_files": [], "error": "Repair made no source changes."},
		],
		"agent_errors": [{
			"agent": "repair",
			"error": "Repair rejected: main.py: Ruff reported an undefined name.",
		}],
		"comparison": {
			"status": "complete",
			"remaining_original_count": 1,
			"new_count": 0,
			"resolved_count": 1,
			"remaining_findings": [{"issue": "C0114", "severity": "low"}],
		},
		"validation_results": {"status": "passed", "passed": True, "findings": []},
	})

	assert report["validation_status"] == "passed"
	assert report["workflow_status"] == "REVIEW_REQUIRED"
	assert report["repair_status"] == "partial"
	assert report["workflow_summary"]["repair_status"] == "partial"
	assert "Sandbox and validation passed" in report["workflow_status_reason"]


def test_exhausted_repair_reports_remaining_findings_without_suggesting_another_attempt():
	report = build_report({
		"project_id": "exhausted-repair-with-findings",
		"validation_status": "needs_review",
		"retry_count": 3,
		"repair_attempts": [
			{"outcome": "needs_review", "changed_files": ["main.py"]},
			{"outcome": "needs_review", "changed_files": ["main.py"]},
			{"outcome": "needs_review", "changed_files": ["main.py"]},
			{
				"outcome": "failed",
				"changed_files": [],
				"error": "Repair made no source changes; findings remain unresolved.",
			},
		],
		"comparison": {
			"status": "complete",
			"remaining_original_count": 1,
			"new_count": 1,
			"remaining_findings": [{"issue": "C0116"}, {"issue": "C0114"}],
		},
	})

	assert report["workflow_status"] == "REVIEW_REQUIRED"
	assert report["workflow_status_reason"] == (
		"Repair stopped after 4 attempts; 2 finding(s) remain for review."
	)


def test_passed_validation_with_remaining_reanalyzed_findings_requires_review():
	report = build_report({
		"project_id": "remaining-finding-after-validation",
		"validation_status": "passed",
		"project_context": {"analysis_checks": {
			"code_quality": False,
			"security": False,
			"bug_resource": False,
			"complexity": False,
			"automated_repair": True,
			"final_validation": True,
		}},
		"comparison": {
			"status": "complete",
			"remaining_original_count": 1,
			"new_count": 0,
		},
	})

	assert report["validation_status"] == "passed"
	assert report["workflow_status"] == "REVIEW_REQUIRED"


def test_repair_status_is_needs_review_when_finding_comparison_is_unavailable():
	report = build_report({
		"project_id": "repair-not-verified",
		"validation_status": "passed",
		"project_context": {"analysis_checks": {
			"code_quality": False,
			"security": False,
			"bug_resource": False,
			"complexity": False,
			"automated_repair": True,
			"final_validation": True,
		}},
		"repair_attempts": [{"changed_files": ["main.js"], "outcome": "validated"}],
		"comparison": {
			"status": "partial",
			"skipped_checks": ["eslint"],
			"unverified_count": 1,
		},
	})

	assert report["validation_status"] == "passed"
	assert report["repair_status"] == "needs_review"
	assert report["workflow_status"] != "SUCCESS"


def test_repair_status_is_not_run_when_repair_was_disabled():
	report = build_report({
		"project_id": "repair-disabled",
		"validation_status": "not_run",
		"repair_attempts": [],
	})

	assert report["repair_status"] == "not_run"


def test_remaining_findings_prevent_final_validation_success():
	report = build_report({
		"project_id": "unresolved-findings",
		"validation_status": "passed",
		"findings": [{
			"file": "main.go",
			"issue": "GO001",
			"description": "Unresolved analyzer finding",
		}],
		"reanalysis_findings": [{
			"file": "main.go",
			"issue": "GO001",
			"description": "Unresolved analyzer finding",
		}],
		"repair_attempts": [{"changed_files": ["main.go"]}],
		"project_context": {"analysis_checks": {
			"code_quality": False,
			"complexity": False,
			"security": True,
			"bug_resource": False,
		}},
		"comparison": {
			"status": "complete",
			"remaining_original_count": 1,
			"new_count": 0,
			"remaining_findings": [{
				"file": "main.go",
				"issue": "GO001",
				"category": "security",
				"description": "Unresolved analyzer finding",
			}],
		},
	})

	assert report["validation_status"] == "passed"
	assert report["workflow_status"] == "REVIEW_REQUIRED"


def test_findings_remain_review_required_when_automated_repair_is_disabled():
	report = build_report({
		"project_id": "repair-disabled-with-findings",
		"validation_status": "passed",
		"findings": [{"file": "main.py", "issue": "F401"}],
		"project_context": {"analysis_checks": {
			"code_quality": False,
			"security": True,
			"bug_resource": False,
			"complexity": False,
			"automated_repair": False,
			"final_validation": True,
		}},
	})

	assert report["validation_status"] == "passed"
	assert report["workflow_status"] == "SUCCESS"


def test_unverified_findings_cannot_be_reported_as_successful_repair():
	report = build_report({
		"project_id": "unverified-findings",
		"validation_status": "passed",
		"project_context": {"analysis_checks": {
			"code_quality": False,
			"security": False,
			"bug_resource": False,
			"complexity": False,
			"automated_repair": True,
			"final_validation": True,
		}},
		"repair_attempts": [{"outcome": "validated", "changed_files": ["main.py"]}],
		"comparison": {
			"status": "complete",
			"remaining_original_count": 0,
			"new_count": 0,
			"unverified_count": 1,
			"unverified_findings": [{"issue": "RULE-1", "severity": "medium"}],
		},
	})

	assert report["validation_status"] == "needs_review"
	assert report["repair_status"] == "needs_review"
	assert report["workflow_status"] == "REVIEW_REQUIRED"
