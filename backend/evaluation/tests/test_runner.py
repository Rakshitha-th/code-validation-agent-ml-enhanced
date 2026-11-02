import json
from pathlib import Path

import pytest

from backend.evaluation.runner import (
    EvaluationDataError,
    Evaluator,
    _compare,
    _workflow_agent_statuses,
    _workflow_stage_statuses,
    evaluate,
    load_cases,
)


def test_bundled_datasets_load_without_duplicate_ids():
    cases = load_cases(sorted((Path(__file__).parent.parent / "datasets").glob("*.jsonl")))

    assert {case["target"] for case in cases} == {
        "validation",
        "analysis",
        "repair",
        "workflow",
        "security",
        "bug_resource",
    }
    assert len({case["id"] for case in cases}) == len(cases)
    workflow_languages = {
        case["language"] for case in cases if case["target"] == "workflow"
    }
    assert workflow_languages == {
        "python",
        "java",
        "javascript",
        "typescript",
        "c",
        "cpp",
        "csharp",
        "go",
    }
    for target in ("security", "bug_resource"):
        assert {
            case["language"] for case in cases if case["target"] == target
        } == workflow_languages


@pytest.mark.parametrize(
    ("record", "message"),
    [
        ('{"id":"same","language":"ruby","target":"validation","code":"","expected":{}}', "unsupported language"),
        ('{"id":"x","language":"python","target":"other","code":"","expected":{}}', "target must be"),
        ('{"id":"x","language":"python","target":"validation","code":"","expected":{"required_agents":"quality"}}', "list of strings"),
        ('{"id":"x","language":"python","target":"security","code":"print(1)","expected":{"expected_findings":[{"rule_id":3}]}}', "expected_findings"),
    ],
)
def test_loader_rejects_invalid_case_contract(tmp_path, record, message):
    dataset = tmp_path / "cases.jsonl"
    dataset.write_text(record + "\n", encoding="utf-8")

    with pytest.raises(EvaluationDataError, match=message):
        load_cases([dataset])


def test_loader_reports_invalid_json_line(tmp_path):
    dataset = tmp_path / "cases.jsonl"
    valid_case = {
        "id": "one",
        "language": "python",
        "target": "validation",
        "code": "print(1)\n",
        "expected": {"status": "passed"},
    }
    dataset.write_text(json.dumps(valid_case) + "\nnot-json\n", encoding="utf-8")

    with pytest.raises(EvaluationDataError, match=r"cases.jsonl:2: invalid JSON"):
        load_cases([dataset])


def test_loader_rejects_duplicate_case_ids_across_files(tmp_path):
    case = {
        "id": "duplicate",
        "language": "python",
        "target": "validation",
        "code": "print(1)\n",
        "expected": {"status": "passed"},
    }
    first = tmp_path / "first.jsonl"
    second = tmp_path / "second.jsonl"
    serialized = json.dumps(case) + "\n"
    first.write_text(serialized, encoding="utf-8")
    second.write_text(serialized, encoding="utf-8")

    with pytest.raises(EvaluationDataError, match="duplicate case id"):
        load_cases([first, second])


def test_validation_evaluation_matches_expected_findings(monkeypatch):
    monkeypatch.setenv("EVAL_VALIDATION_URL", "http://validation.test")
    captured = {}

    def request_json(url, payload, timeout):
        captured.update(url=url, payload=payload, timeout=timeout)
        return {
            "status": "needs_review",
            "valid": True,
            "total_findings": 1,
            "findings": [{"code": "F401"}],
        }

    case = {
        "id": "unused-import",
        "target": "validation",
        "language": "python",
        "filename": "main.py",
        "code": "import os\n",
        "expected": {"status": "needs_review", "finding_codes": ["F401"]},
    }
    result = Evaluator(request_json=request_json).evaluate_case(case)

    assert result["passed"] is True
    assert captured["url"] == "http://validation.test/validate"
    assert captured["payload"] == {"code": "import os\n", "filename": "main.py"}


def test_non_python_validation_uses_isolated_sandbox():
    captured = {}

    def request_json(url, payload, timeout):
        captured.update(url=url, payload=payload, timeout=timeout)
        return {"status": "completed", "passed": True, "exit_code": 0}

    case = {
        "id": "js-validation",
        "target": "validation",
        "language": "javascript",
        "filename": "main.js",
        "code": 'console.log("ok");\n',
        "expected": {"status": "passed", "valid": True},
    }
    result = Evaluator(request_json=request_json).evaluate_case(case)

    assert result["passed"] is True
    assert captured["url"].endswith("/execute")
    assert captured["payload"]["filename"] == "main.js"
    assert captured["payload"]["files"] == {"main.js": 'console.log("ok");\n'}
    assert captured["payload"]["validation_only"] is True


@pytest.mark.parametrize(
    ("target", "expected_url", "response"),
    [
        (
            "security",
            "http://security.test/scan",
            {
                "status": "completed",
                "total_findings": 1,
                "findings": [{
                    "type": "Gitleaks",
                    "severity": "HIGH",
                    "file": "main.js",
                    "line": 1,
                }],
            },
        ),
        (
            "bug_resource",
            "http://bugs.test/analyze",
            {
                "agent": "bug_resource",
                "findings_count": 1,
                "findings": [{
                    "issue": "BUG005",
                    "severity": "low",
                    "file": "main.go",
                    "line_number": 2,
                }],
            },
        ),
    ],
)
def test_direct_agent_evaluation_targets_check_severity_and_rule(
    monkeypatch, target, expected_url, response
):
    monkeypatch.setenv(
        "EVAL_SECURITY_URL",
        "http://security.test" if target == "security" else "",
    )
    monkeypatch.setenv(
        "EVAL_BUG_RESOURCE_URL",
        "http://bugs.test" if target == "bug_resource" else "",
    )
    captured = {}

    def request_json(url, payload, timeout):
        captured.update(url=url, payload=payload, timeout=timeout)
        return response

    filename = "main.js" if target == "security" else "main.go"
    case = {
        "id": f"{target}-case",
        "target": target,
        "language": "javascript" if target == "security" else "go",
        "filename": filename,
        "code": "fixture source",
        "expected": {
            "status": "completed",
            "minimum_findings": 1,
            "expected_findings": [{
                "rule_id": "Gitleaks" if target == "security" else "BUG005",
                "severity": "high" if target == "security" else "low",
            }],
        },
    }

    result = Evaluator(request_json=request_json).evaluate_case(case)

    assert result["passed"] is True
    assert captured["url"] == expected_url
    if target == "security":
        assert captured["payload"] == {"filename": filename, "code": "fixture source"}
    else:
        assert captured["payload"]["files"] == [
            {"path": filename, "content": "fixture source"}
        ]


def test_expected_findings_require_matching_severity_and_rule():
    failures = _compare(
        {"expected_findings": [{"rule_id": "BUG005", "severity": "low"}]},
        {"finding_details": [{"rule_id": "BUG005", "severity": "medium"}]},
    )

    assert failures == ["missing expected finding: BUG005 (low)"]


def test_workflow_evaluation_checks_actual_ollama_report_output(monkeypatch):
    responses = iter([
        {"status": "accepted"},
        {
            "status": "completed",
            "validation_status": "passed",
            "result": {
                "final_report": {"workflow_status": "SUCCESS"},
                "validation_status": "passed",
                "ai_recommendations_status": {"status": "completed", "provider": "ollama"},
                "code_report": {"scanned_files": 1, "issues": []},
                "stage_history": [
                    {"stage": "analyze", "agents": {
                        "security": "completed",
                        "quality": "completed",
                        "bug_resource": "completed",
                    }},
                    {"stage": "repair", "status": "completed"},
                    {"stage": "validate", "status": "passed"},
                ],
            },
        },
    ])
    evaluator = Evaluator(request_json=lambda *_args: next(responses), poll_interval_seconds=0.01)
    case = {
        "id": "workflow-report",
        "target": "workflow",
        "language": "javascript",
        "filename": "main.js",
        "code": 'console.log("ok");\n',
        "expected": {
            "status": "completed",
            "workflow_status": "SUCCESS",
            "validation_status": "passed",
            "ai_recommendations_status": "completed",
            "ai_recommendations_provider": "ollama",
            "ai_report_scanned_files": 1,
            "required_agents": [
                "security",
                "quality",
                "bug_resource",
                "repair",
                "validation",
                "report_recommendation",
            ],
        },
    }

    result = evaluator.evaluate_case(case)

    assert result["passed"] is True
    assert result["actual"]["ai_report_scanned_files"] == 1


def test_workflow_agent_statuses_collect_all_selected_agents():
    statuses = _workflow_agent_statuses({
        "stage_history": [
            {
                "stage": "analyze",
                "agents": {
                    "security": "completed",
                    "quality": "completed",
                    "bug_resource": "completed",
                },
            },
            {"stage": "repair", "status": "completed"},
            {"stage": "validate", "status": "passed"},
        ],
        "ai_recommendations_status": {"status": "completed"},
    })

    assert statuses == {
        "security": "completed",
        "quality": "completed",
        "bug_resource": "completed",
        "repair": "completed",
        "validation": "passed",
        "report_recommendation": "completed",
    }


def test_workflow_stage_statuses_keep_skips_and_failures():
    statuses = _workflow_stage_statuses({
        "stage_history": [
            {"stage": "analyze", "status": "partial"},
            {"stage": "repair", "status": "not_run"},
            {"stage": "validate", "status": "failed"},
        ],
    })

    assert statuses == {
        "analyze": "partial",
        "repair": "not_run",
        "validate": "failed",
    }


def test_expected_required_agents_fail_if_an_agent_is_missing():
    failures = _compare(
        {"required_agents": ["security", "report_recommendation"]},
        {"agent_statuses": {"security": "completed"}},
    )

    assert failures == [
        "required agents did not complete: report_recommendation (not run)"
    ]


def test_expected_report_file_count_is_checked():
    failures = _compare(
        {"ai_report_scanned_files": 1},
        {"ai_report_scanned_files": None},
    )

    assert failures == ["ai_report_scanned_files: expected 1, got None"]


def test_evaluation_report_has_aggregate_pass_rate():
    class FakeEvaluator:
        def evaluate_case(self, case):
            return {"id": case["id"], "passed": case["passed"]}

    report = evaluate(
        [{"id": "pass", "passed": True}, {"id": "fail", "passed": False}],
        FakeEvaluator(),
    )

    assert report["schema_version"] == 1
    assert report["summary"] == {
        "total": 2,
        "passed": 1,
        "failed": 1,
        "pass_rate": 0.5,
    }
    assert report["per_language"]["unknown"]["total"] == 2
    assert report["per_agent"] == {}
    assert report["per_workflow_stage"] == {}
    assert report["finished_at"] >= report["started_at"]


def test_evaluation_metrics_are_grouped_by_language_and_agent():
    class FakeEvaluator:
        def evaluate_case(self, case):
            return {
                "id": case["id"],
                "language": case["language"],
                "target": case["target"],
                "passed": case["passed"],
                "actual": {
                    "status": case["status"],
                    "workflow_stage_statuses": case.get("workflow_stage_statuses", {}),
                },
            }

    report = evaluate([
        {"id": "one", "language": "javascript", "target": "analysis", "passed": True, "status": "completed"},
        {"id": "two", "language": "javascript", "target": "validation", "passed": False, "status": "failed"},
        {"id": "three", "language": "javascript", "target": "validation", "passed": True, "status": "failed"},
    ], FakeEvaluator())

    assert report["per_language"]["javascript"] == {
        "total": 3, "passed": 2, "failed": 1, "pass_rate": 0.6667,
    }
    assert report["per_agent"]["code_quality"]["passed"] == 1
    assert report["per_agent"]["validation"] == {
        "total": 2, "passed": 1, "failed": 1, "skipped": 0, "pass_rate": 0.5,
    }
    assert report["per_workflow_stage"] == {}


def test_workflow_stage_metrics_group_completed_failed_and_skipped_stages():
    class FakeEvaluator:
        def evaluate_case(self, case):
            return {
                "id": case["id"],
                "language": case["language"],
                "target": case["target"],
                "passed": True,
                "actual": {"workflow_stage_statuses": case["workflow_stage_statuses"]},
            }

    report = evaluate([
        {
            "id": "one",
            "language": "java",
            "target": "workflow",
            "passed": True,
            "workflow_stage_statuses": {"analyze": "completed", "repair": "skipped"},
        },
        {
            "id": "two",
            "language": "java",
            "target": "workflow",
            "passed": True,
            "workflow_stage_statuses": {"analyze": "partial", "repair": "completed"},
        },
        {
            "id": "three",
            "language": "java",
            "target": "workflow",
            "passed": True,
            "workflow_stage_statuses": {
                "repair": "not_run",
                "report_recommend": "disabled",
            },
        },
    ], FakeEvaluator())

    assert report["per_workflow_stage"] == {
        "analyze": {
            "total": 2, "passed": 1, "failed": 1, "skipped": 0, "pass_rate": 0.5,
        },
        "repair": {
            "total": 3, "passed": 1, "failed": 0, "skipped": 2, "pass_rate": 1.0,
        },
        "report_recommend": {
            "total": 1, "passed": 0, "failed": 0, "skipped": 1, "pass_rate": 0,
        },
    }


def test_workflow_agent_metrics_count_not_run_as_skipped():
    class FakeEvaluator:
        def evaluate_case(self, case):
            return {
                "id": case["id"],
                "language": case["language"],
                "target": case["target"],
                "passed": True,
                "actual": {
                    "agent_statuses": {
                        "repair": "not_run",
                        "report_recommendation": "disabled",
                    },
                },
            }

    report = evaluate(
        [{"id": "one", "language": "java", "target": "workflow"}],
        FakeEvaluator(),
    )

    assert report["per_agent"] == {
        "repair": {
            "total": 1, "passed": 0, "failed": 0, "skipped": 1, "pass_rate": 0,
        },
        "report_recommendation": {
            "total": 1, "passed": 0, "failed": 0, "skipped": 1, "pass_rate": 0,
        },
    }
