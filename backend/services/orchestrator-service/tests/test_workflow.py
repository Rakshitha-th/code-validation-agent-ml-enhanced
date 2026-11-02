import asyncio
import pytest

from app.graph.workflow import _track_stage, build_workflow
from app.schemas.requests import StartAnalysisRequest


def test_tracked_stage_records_timing_metadata():
    async def node(_state):
        return {}

    result = asyncio.run(_track_stage("test-stage", node)({"project_id": "timed-run"}))
    stage = result["stage_history"][-1]

    assert stage["stage"] == "test-stage"
    assert stage["started_at"]
    assert stage["completed_at"]
    assert stage["duration_ms"] >= 0


def test_repair_baseline_respects_selected_analysis_agents(monkeypatch):
    from app.graph import nodes
    from app.services import bug_resource_client, quality_client, security_client

    calls = []

    async def security(*_args, **_kwargs):
        calls.append("security")
        return {"findings": []}

    async def quality(*_args, **_kwargs):
        calls.append("quality")
        return {"findings": [], "quality_summary": {"tool_runs": []}}

    async def bug_resource(*_args, **_kwargs):
        calls.append("bug_resource")
        return {"findings": []}

    monkeypatch.setattr(security_client, "analyze", security)
    monkeypatch.setattr(quality_client, "analyze", quality)
    monkeypatch.setattr(bug_resource_client, "analyze", bug_resource)

    result = asyncio.run(nodes.analyze({
        "project_id": "mandatory-repair-checks",
        "project_files": {"main.py": "print('ok')\n"},
        "project_context": {"analysis_checks": {
            "code_quality": True,
            "security": False,
            "bug_resource": False,
            "complexity": False,
            "automated_repair": True,
            "final_validation": False,
        }},
        "tools": None,
        "stage_history": [],
        "workflow_trace": [],
    }))

    assert calls == ["quality"]
    assert result["stage_history"][-1]["agents"] == {
        "security": "skipped",
        "quality": "completed",
        "bug_resource": "skipped",
    }


def test_repair_reanalysis_requires_final_validation_when_ui_option_is_disabled():
    from app.graph.workflow import _after_reanalyze

    assert _after_reanalyze({
        "repair_attempts": [{"changed_files": ["main.py"]}],
        "project_context": {"analysis_checks": {"final_validation": False}},
    }) == "validate"


@pytest.mark.parametrize(
    ("filename", "expected_tools"),
    [
        ("main.py", ["ruff"]),
        ("Main.java", None),
        ("main.js", None),
        ("main.ts", None),
        ("main.c", None),
        ("main.cpp", None),
        ("Program.cs", None),
        ("main.go", None),
    ],
)
def test_analysis_only_passes_python_linters_to_python_quality_agent(
    monkeypatch, filename, expected_tools
):
    from app.graph import nodes
    from app.services import quality_client

    received = []

    async def quality(_project_id, _files, _context, tools):
        received.append(tools)
        return {"findings": []}

    monkeypatch.setattr(quality_client, "analyze", quality)

    asyncio.run(nodes._collect_analysis_responses(
        "language-tools",
        {filename: "source"},
        {},
        ["ruff"],
        False,
        True,
        False,
    ))

    assert received == [expected_tools]


@pytest.mark.parametrize(
    ("language", "filename", "source"),
    [
        ("python", "main.py", 'print("ok")\n'),
        ("java", "Main.java", 'class Main { public static void main(String[] args) {} }\n'),
        ("javascript", "main.js", 'console.log("ok");\n'),
        ("typescript", "main.ts", 'const value: string = "ok";\n'),
        ("c", "main.c", "int main(void) { return 0; }\n"),
        ("cpp", "main.cpp", "int main() { return 0; }\n"),
        ("csharp", "Program.cs", "class Program { static void Main() {} }\n"),
        ("go", "main.go", "package main\nfunc main() {}\n"),
    ],
)
def test_workflow_runs_selected_agents_for_every_supported_language(
    monkeypatch, language, filename, source
):
    from app.services import (
        bug_resource_client,
        quality_client,
        repair_client,
        report_recommendation_client,
        sandbox_client,
        security_client,
        validation_client,
    )
    from app.graph import nodes

    calls = []
    source_files = {filename: source}
    quality_tool = "ruff" if language == "python" else "language-analyzer"
    monkeypatch.setattr(nodes.settings, "bug_resource_service_url", "http://bug-resource")

    async def security(*_args, **_kwargs):
        calls.append("security")
        return {"findings": []}

    async def quality(_project_id, _files, _context, tools):
        calls.append(("quality", tools))
        return {
            "findings": [],
            "quality_summary": {
                "overall_score": 100,
                "quality_gate_status": "PASS",
                "selected_tools": [quality_tool],
                "tool_runs": [{"tool": quality_tool, "status": "completed"}],
            },
        }

    async def bug_resource(*_args, **_kwargs):
        calls.append("bug_resource")
        return {"findings": []}

    async def report_recommendation(_files):
        calls.append("report_recommendation")
        return {
            "status": "completed",
            "provider": "ollama",
            "code_report": {"scanned_files": 1},
            "recommendation_report": {},
        }

    async def repair(_project_id, files, _findings, *_args, **_kwargs):
        calls.append("repair")
        return {
            "status": "completed",
            "repaired_files": [
                {"path": path, "content": content}
                for path, content in files.items()
            ],
            "unresolved_findings": [],
        }

    async def sandbox(_project_id, _files, *_args, **kwargs):
        calls.append(("sandbox", kwargs.get("validation_only", False)))
        return {
            "status": "completed",
            "executed": True,
            "passed": True,
            "exit_code": 0,
            "validator": "test compiler",
        }

    async def validation(_project_id, files, *_args, **_kwargs):
        calls.append("validation")
        result = {"status": "passed", "passed": True, "findings": []}
        if not next(iter(files)).endswith(".py"):
            result["sandbox_result"] = {
                "status": "completed",
                "executed": True,
                "passed": True,
                "exit_code": 0,
            }
        return result

    monkeypatch.setattr(security_client, "analyze", security)
    monkeypatch.setattr(quality_client, "analyze", quality)
    monkeypatch.setattr(bug_resource_client, "analyze", bug_resource)
    monkeypatch.setattr(report_recommendation_client, "analyze", report_recommendation)
    monkeypatch.setattr(repair_client, "repair", repair)
    monkeypatch.setattr(sandbox_client, "execute", sandbox)
    monkeypatch.setattr(validation_client, "validate", validation)

    result = asyncio.run(build_workflow().ainvoke({
        "project_id": f"supported-{language}",
        "project_files": source_files,
        "project_context": {"analysis_checks": {
            "code_quality": True,
            "security": True,
            "bug_resource": True,
            "complexity": True,
            "automated_repair": True,
            "final_validation": True,
            "ai_report_recommendations": True,
        }},
        "tools": ["ruff"],
    }))

    assert result["validation_status"] == "passed"
    assert result["final_report"]["workflow_status"] == "SUCCESS"
    assert result["ai_recommendations_status"]["status"] == "completed"
    assert "security" in calls
    assert "bug_resource" in calls
    assert "report_recommendation" in calls
    assert "repair" in calls
    assert "validation" in calls
    assert ("quality", ["ruff"] if language == "python" else None) in calls
    assert ("sandbox", False) in calls


@pytest.mark.parametrize(
    ("language", "filename", "source"),
    [
        ("python", "main.py", '# BUG\nprint("wrong")\n'),
        ("java", "Main.java", "// BUG\nclass Main { public static void main(String[] args) {} }\n"),
        ("javascript", "main.js", '// BUG\nconsole.log("wrong");\n'),
        ("typescript", "main.ts", '// BUG\nconsole.log("wrong");\n'),
        ("c", "main.c", '// BUG\nint main(void) { return 1; }\n'),
        ("cpp", "main.cpp", '// BUG\nint main() { return 1; }\n'),
        ("csharp", "Program.cs", "// BUG\nclass Program { static void Main() {} }\n"),
        ("go", "main.go", "package main\n// BUG\nfunc main() {}\n"),
    ],
)
def test_faulty_source_is_repaired_reanalyzed_and_verified_for_every_language(
    monkeypatch, language, filename, source
):
    from app.graph import nodes
    from app.services import (
        bug_resource_client,
        quality_client,
        repair_client,
        sandbox_client,
        security_client,
        validation_client,
    )

    calls = []

    async def quality(_project_id, files, _context, tools):
        calls.append(("quality", tools, files[filename]))
        findings = [{
            "finding_id": f"{language}-bug",
            "category": "code_quality",
            "file": filename,
            "line_number": 1,
            "issue": "TEST-BUG",
            "description": "BUG marker remains",
            "tool": "fixture-analyzer",
        }] if "BUG" in files[filename] else []
        analyzer = "ruff" if language == "python" else "language-analyzer"
        return {
            "findings": findings,
            "quality_summary": {
                "overall_score": 100 if not findings else 80,
                "quality_gate_status": "PASS",
                "selected_tools": [analyzer],
                "tool_runs": [{"tool": analyzer, "status": "completed", "finding_count": len(findings)}],
            },
        }

    async def no_security_findings(*_args, **_kwargs):
        calls.append(("security",))
        return {"findings": []}

    async def no_resource_findings(*_args, **_kwargs):
        calls.append(("bug_resource",))
        return {"findings": []}

    async def repair(_project_id, files, findings, _feedback="", submitted_language=None):
        calls.append(("repair", submitted_language or language, len(findings)))
        return {
            "status": "completed",
            "repair_status": "success",
            "repaired_files": [
                {"path": path, "content": content.replace("BUG", "FIXED")}
                for path, content in files.items()
            ],
            "unresolved_findings": [],
            "repair_details": [],
        }

    async def sandbox(_project_id, _files, *_args, **_kwargs):
        calls.append(("sandbox", language))
        return {"status": "completed", "executed": True, "passed": True, "exit_code": 0}

    async def validation(_project_id, _files, _findings):
        calls.append(("validation", language))
        result = {"status": "passed", "passed": True, "findings": []}
        if language != "python":
            result["sandbox_result"] = {
                "status": "completed",
                "executed": True,
                "passed": True,
                "exit_code": 0,
            }
        return result

    monkeypatch.setattr(nodes.settings, "bug_resource_service_url", "http://bug-resource")
    monkeypatch.setattr(security_client, "analyze", no_security_findings)
    monkeypatch.setattr(quality_client, "analyze", quality)
    monkeypatch.setattr(bug_resource_client, "analyze", no_resource_findings)
    monkeypatch.setattr(repair_client, "repair", repair)
    monkeypatch.setattr(sandbox_client, "execute", sandbox)
    monkeypatch.setattr(validation_client, "validate", validation)

    result = asyncio.run(build_workflow().ainvoke({
        "project_id": f"faulty-{language}",
        "project_files": {filename: source},
        "project_context": {"analysis_checks": {
            "code_quality": True,
            "security": False,
            "bug_resource": False,
            "complexity": False,
            "automated_repair": True,
            "final_validation": True,
            "ai_report_recommendations": False,
        }},
        "tools": ["ruff"] if language == "python" else None,
    }))

    assert result["final_code"][filename].replace("FIXED", "BUG") == source
    assert result["comparison"]["status"] == "complete"
    assert result["comparison"]["resolved_count"] == 1
    assert result["comparison"]["remaining_findings"] == []
    assert result["final_report"]["validation_status"] == "passed"
    assert result["final_report"]["workflow_status"] == "SUCCESS"
    assert result["repair_attempts"][0]["candidate_status"] == "accepted"
    assert [call[0] for call in calls].count("quality") == 2
    assert not any(call[0] in {"security", "bug_resource"} for call in calls)
    assert ("repair", language, 1) in calls
    assert ("sandbox", language) in calls
    assert ("validation", language) in calls


@pytest.fixture(autouse=True)
def _langchain_debug_compat(monkeypatch):
    import langchain

    monkeypatch.setattr(langchain, "debug", False, raising=False)


def test_workflow_runs_end_to_end_with_downstream_boundaries(monkeypatch):
    """
    Downstream boundaries are mocked so this graph test remains fast and
    deterministic. Client fallback behavior is tested at each HTTP client.
    """
    from app.services import (
        bug_resource_client,
        quality_client,
        repair_client,
        sandbox_client,
        security_client,
        validation_client,
    )

    async def no_findings(*args, **kwargs):
        return {"findings": []}

    async def unchanged_repair(*args, **kwargs):
        files = args[1]
        return {"repaired_files": [{"path": path, "content": content} for path, content in files.items()]}

    async def sandbox_pass(*args, **kwargs):
        return {"executed": True, "exit_code": 0}

    async def validation_pass(*args, **kwargs):
        return {"passed": True, "tests": {"passed": 1}}

    monkeypatch.setattr(security_client, "analyze", no_findings)
    monkeypatch.setattr(quality_client, "analyze", no_findings)
    monkeypatch.setattr(bug_resource_client, "analyze", no_findings)
    monkeypatch.setattr(repair_client, "repair", unchanged_repair)
    monkeypatch.setattr(sandbox_client, "execute", sandbox_pass)
    monkeypatch.setattr(validation_client, "validate", validation_pass)

    wf = build_workflow()
    stage_events = []

    result = asyncio.run(wf.ainvoke(
        {
            "project_id": "proj-test",
            "project_files": {"app.py": "print('hi')\n"},
            "project_context": {},
        },
        config={"configurable": {"on_stage": lambda stage, status: stage_events.append((stage, status))}},
    ))

    assert result["project_id"] == "proj-test" if "project_id" in result else True
    assert result.get("validation_status") in ("passed", "failed")
    assert result.get("retry_count", 0) <= 3
    assert "final_report" in result
    assert result["final_report"]["code_structure"]["files"][0]["path"] == "app.py"
    analyze_stage = next(stage for stage in result["stage_history"] if stage["stage"] == "analyze")
    assert analyze_stage["agents"]["bug_resource"] == "completed"
    assert analyze_stage["agents"]["security"] == "completed"
    assert analyze_stage["agents"]["quality"] == "completed"

    stages = [item["stage"] for item in result.get("stage_history", [])]
    assert stages.index("explain_prioritize") < stages.index("repair")
    assert stages.index("repair") < stages.index("reanalyze")
    assert stages.index("reanalyze") < stages.index("validate")
    assert stages.index("validate") < stages.index("compare")
    assert "explanations" in result["final_report"]
    assert "reanalysis_findings" in result["final_report"]
    assert "comparison" in result["final_report"]
    assert result["final_report"]["comparison"]["findings_before"] == len(result["findings"])
    assert result["final_report"]["stage_history"][-2]["stage"] == "compare"
    assert ("understand", "running") in stage_events
    assert ("analyze", "running") in stage_events
    assert ("compare", "completed") in stage_events


def test_workflow_reanalyzes_repair_candidate_before_sandbox_and_validation(monkeypatch):
    from app.services import bug_resource_client, quality_client, repair_client, sandbox_client, security_client, validation_client

    events = []
    original = "import os\nprint('ready')\n"
    candidate = "print('ready')\n"

    async def quality(_project_id, files, _context, _tools):
        events.append(("analyze", files["main.py"]))
        findings = [{"file": "main.py", "issue": "F401", "category": "code_quality", "tool": "ruff"}] if "import os" in files["main.py"] else []
        return {
            "findings": findings,
            "quality_summary": {
                "overall_score": 95,
                "quality_gate_status": "PASS",
                "selected_tools": ["ruff"],
                "tool_runs": [{"tool": "ruff", "status": "completed"}],
            },
        }

    async def no_findings(*_args, **_kwargs):
        return {"findings": []}

    async def repair(_project_id, files, _findings, *_args):
        events.append(("repair", files["main.py"]))
        return {"status": "completed", "repaired_files": [{"path": "main.py", "content": candidate}]}

    async def sandbox(_project_id, files, *_args, **_kwargs):
        events.append(("sandbox", files["main.py"]))
        return {"status": "completed", "executed": True, "passed": True, "exit_code": 0}

    async def validation(_project_id, files, _findings):
        events.append(("validation", files["main.py"]))
        return {"passed": True, "status": "passed", "findings": [], "error": None}

    monkeypatch.setattr(security_client, "analyze", no_findings)
    monkeypatch.setattr(bug_resource_client, "analyze", no_findings)
    monkeypatch.setattr(quality_client, "analyze", quality)
    monkeypatch.setattr(repair_client, "repair", repair)
    monkeypatch.setattr(sandbox_client, "execute", sandbox)
    monkeypatch.setattr(validation_client, "validate", validation)

    result = asyncio.run(build_workflow().ainvoke({
        "project_id": "candidate-order",
        "run_id": "run-candidate-order",
        "project_files": {"main.py": original},
        "project_context": {"analysis_checks": {
            "code_quality": True,
            "security": False,
            "bug_resource": False,
            "complexity": False,
            "automated_repair": True,
            "final_validation": True,
            "ai_report_recommendations": False,
        }},
        "tools": ["ruff"],
    }))

    assert [name for name, _ in events] == ["analyze", "repair", "analyze", "sandbox", "validation"]
    assert events[2][1] == candidate
    assert events[3][1] == candidate
    assert events[4][1] == candidate
    stages = [item["stage"] for item in result["stage_history"]]
    assert stages.index("reanalyze") < stages.index("validate") < stages.index("compare")
    assert result["validation_status"] == "passed", {
        "sandbox": result.get("sandbox_result"),
        "validation": result.get("validation_results"),
        "errors": result.get("agent_errors"),
    }
    assert result["final_report"]["comparison"]["resolved_count"] == 1


def test_workflow_retries_repair_after_passed_sandbox_when_findings_remain(monkeypatch):
    from app.services import bug_resource_client, quality_client, repair_client, sandbox_client, security_client, validation_client

    original = "int main(void) { return 0; }\n"
    first_candidate = "int main(void) { return 1; }\n"
    final_candidate = "int main(void) { return 2; }\n"
    repair_inputs = []
    current_finding = {
        "finding_id": "missing-return",
        "file": "main.c",
        "line_number": 1,
        "issue": "LANGUAGE_ANALYZER",
        "category": "code_quality",
        "tool": "Cppcheck",
        "description": "A return value is missing.",
        "recommendation": "Add the required return value.",
    }

    async def no_findings(*_args, **_kwargs):
        return {"findings": []}

    async def quality(_project_id, files, _context, _tools):
        findings = [] if files["main.c"] == final_candidate else [current_finding]
        return {
            "findings": findings,
            "quality_summary": {
                "overall_score": 90,
                "quality_gate_status": "PASS",
                "selected_tools": ["Cppcheck"],
                "tool_runs": [{"tool": "Cppcheck", "status": "completed"}],
            },
        }

    async def repair(_project_id, files, findings, *_args, **_kwargs):
        repair_inputs.append((files["main.c"], findings))
        candidate = first_candidate if len(repair_inputs) == 1 else final_candidate
        return {
            "status": "partial" if len(repair_inputs) == 1 else "completed",
            "outcome": "partially_applied" if len(repair_inputs) == 1 else "applied",
            "changed_files": ["main.c"],
            "repaired_files": [{"path": "main.c", "content": candidate}],
            "unresolved_findings": findings if len(repair_inputs) == 1 else [],
            "repair_details": [{"path": "main.c", "changed": True, "resolved_finding_ids": []}],
            "error": None,
        }

    async def sandbox(*_args, **_kwargs):
        return {"status": "completed", "executed": True, "passed": True, "exit_code": 0}

    async def validation(_project_id, files, *_args, **_kwargs):
        result = {"passed": True, "status": "passed", "findings": [], "error": None}
        if not next(iter(files)).endswith(".py"):
            result["sandbox_result"] = {
                "status": "completed",
                "executed": True,
                "passed": True,
                "exit_code": 0,
            }
        return result

    monkeypatch.setattr(security_client, "analyze", no_findings)
    monkeypatch.setattr(bug_resource_client, "analyze", no_findings)
    monkeypatch.setattr(quality_client, "analyze", quality)
    monkeypatch.setattr(repair_client, "repair", repair)
    monkeypatch.setattr(sandbox_client, "execute", sandbox)
    monkeypatch.setattr(validation_client, "validate", validation)

    result = asyncio.run(build_workflow().ainvoke({
        "project_id": "passed-sandbox-with-remaining-findings",
        "project_files": {"main.c": original},
        "project_context": {"analysis_checks": {
            "code_quality": True,
            "security": False,
            "bug_resource": False,
            "complexity": False,
            "automated_repair": True,
            "final_validation": True,
            "ai_report_recommendations": False,
        }},
        "tools": ["Cppcheck"],
    }))

    assert len(repair_inputs) == 2
    assert repair_inputs[0][0] == original
    assert repair_inputs[1] == (first_candidate, [current_finding])
    assert result["repaired_code"]["main.c"] == final_candidate
    assert result["retry_count"] == 1
    assert result["validation_status"] == "passed"
    assert result["comparison"]["status"] == "complete"
    assert result["comparison"]["resolved_count"] == 1
    assert result["comparison"]["remaining_original_count"] == 0
    assert [stage["stage"] for stage in result["stage_history"]].count("repair") == 2
    assert [stage["stage"] for stage in result["stage_history"]].count("reanalyze") == 2


def test_validation_keeps_repaired_source_and_sandbox_failure_diagnostics(monkeypatch):
    from app.graph import nodes
    from app.services import sandbox_client, validation_client

    repaired = {"main.py": "print('candidate')\n"}
    seen = {}

    async def sandbox(_project_id, files, *_args, **_kwargs):
        seen["sandbox_files"] = files
        return {
            "status": "failed",
            "executed": True,
            "passed": False,
            "exit_code": 23,
            "stdout": "",
            "stderr": "compiler diagnostic: missing return",
        }

    async def validation(_project_id, files, _findings):
        seen["validation_files"] = files
        return {"passed": False, "status": "failed", "findings": [], "error": None}

    monkeypatch.setattr(sandbox_client, "execute", sandbox)
    monkeypatch.setattr(validation_client, "validate", validation)

    result = asyncio.run(nodes.validate({
        "project_id": "sandbox-failure",
        "run_id": "run-sandbox-failure",
        "project_files": {"main.py": "print('original')\n"},
        "repaired_code": repaired,
        "project_context": {},
        "findings": [],
        "repair_attempts": [{"outcome": "candidate"}],
        "agent_errors": [],
        "workflow_trace": [],
        "stage_history": [],
    }))

    assert seen["sandbox_files"] == repaired
    assert seen["validation_files"] == repaired
    assert result["sandbox_result"]["exit_code"] == 23
    assert result["sandbox_result"]["stderr"] == "compiler diagnostic: missing return"
    assert result["validation_status"] == "failed"


def test_analysis_checks_skip_unselected_agents_repair_and_validation(monkeypatch):
    from app.services import bug_resource_client, quality_client, repair_client, sandbox_client, security_client, validation_client

    calls = []

    async def quality_only(project_id, files, context, tools):
        calls.append(("quality", tools))
        return {"findings": [], "quality_summary": {"tool_runs": [{"tool": "radon", "status": "completed", "finding_count": 0}]}}

    async def unexpected(*args, **kwargs):
        raise AssertionError("a disabled agent or stage must not run")

    monkeypatch.setattr(quality_client, "analyze", quality_only)
    monkeypatch.setattr(security_client, "analyze", unexpected)
    monkeypatch.setattr(bug_resource_client, "analyze", unexpected)
    monkeypatch.setattr(repair_client, "repair", unexpected)
    monkeypatch.setattr(sandbox_client, "execute", unexpected)
    monkeypatch.setattr(validation_client, "validate", unexpected)

    checks = {
        "code_quality": False,
        "security": False,
        "bug_resource": False,
        "complexity": True,
        "automated_repair": False,
        "final_validation": False,
    }
    result = asyncio.run(build_workflow().ainvoke({
        "project_id": "proj-custom-checks",
        "project_files": {"app.py": "print('hi')\n"},
        "project_context": {"analysis_checks": checks},
        "tools": ["radon"],
    }))

    assert calls == [("quality", ["radon"])]
    stage_statuses = {stage["stage"]: stage["status"] for stage in result["stage_history"]}
    assert stage_statuses["repair"] == "skipped"
    assert stage_statuses["validate"] == "skipped"
    assert stage_statuses["reanalyze"] == "skipped"
    assert stage_statuses["compare"] == "skipped"
    assert result["final_report"]["analysis_checks"] == checks
    assert result["validation_status"] == "not_run"
    assert result["final_report"]["validation_status"] == "not_run"


def test_final_validation_can_run_without_automated_repair(monkeypatch):
    from app.services import bug_resource_client, quality_client, repair_client, sandbox_client, security_client, validation_client

    async def unexpected_analysis(*args, **kwargs):
        raise AssertionError("analysis agents were disabled")

    async def unexpected_repair(*args, **kwargs):
        raise AssertionError("automated repair was disabled")

    async def sandbox_pass(project_id, files, stdin=""):
        assert files == {"app.py": "print('hi')\n"}
        assert stdin == ""
        return {"executed": True, "exit_code": 0}

    async def validation_pass(project_id, files, findings):
        assert files == {"app.py": "print('hi')\n"}
        return {"passed": True, "tests": {"passed": 1}}

    monkeypatch.setattr(quality_client, "analyze", unexpected_analysis)
    monkeypatch.setattr(security_client, "analyze", unexpected_analysis)
    monkeypatch.setattr(bug_resource_client, "analyze", unexpected_analysis)
    monkeypatch.setattr(repair_client, "repair", unexpected_repair)
    monkeypatch.setattr(sandbox_client, "execute", sandbox_pass)
    monkeypatch.setattr(validation_client, "validate", validation_pass)

    result = asyncio.run(build_workflow().ainvoke({
        "project_id": "proj-final-validation-only",
        "project_files": {"app.py": "print('hi')\n"},
        "project_context": {"analysis_checks": {
            "code_quality": False,
            "security": False,
            "bug_resource": False,
            "complexity": False,
            "automated_repair": False,
            "final_validation": True,
        }},
        "tools": [],
    }))

    stage_statuses = {stage["stage"]: stage["status"] for stage in result["stage_history"]}
    assert stage_statuses["repair"] == "skipped"
    assert stage_statuses["validate"] == "passed"
    assert stage_statuses["reanalyze"] == "skipped"
    assert result["validation_status"] == "passed"


def test_validation_runs_against_repaired_source(monkeypatch):
    from app.graph import nodes
    from app.services import sandbox_client, validation_client

    repaired = {"main.py": "if True:\n    print('fixed')\n"}
    received = {}

    async def sandbox_pass(project_id, files, stdin=""):
        received["sandbox_files"] = files
        received["sandbox_stdin"] = stdin
        return {"executed": True, "passed": True, "exit_code": 0, "status": "completed"}

    async def validation_pass(project_id, files, findings):
        received["validation_files"] = files
        return {"passed": True, "status": "passed", "findings": []}

    monkeypatch.setattr(sandbox_client, "execute", sandbox_pass)
    monkeypatch.setattr(validation_client, "validate", validation_pass)
    result = asyncio.run(nodes.validate({
        "project_id": "repaired-validation",
        "project_files": {"main.py": "if True\n    print('broken')\n"},
        "repaired_code": repaired,
        "findings": [],
        "stage_history": [],
        "agent_errors": [],
        "repair_attempts": [],
        "workflow_trace": [],
    }))

    assert received == {"sandbox_files": repaired, "sandbox_stdin": "", "validation_files": repaired}
    assert result["validation_status"] == "passed"


def test_non_python_workflow_without_expected_output_does_not_require_review(monkeypatch):
    from app.graph import nodes
    from app.services import sandbox_client, validation_client

    files = {"main.js": 'console.log("actual output");\n'}
    test_cases = [{"input": "", "expected": None}]
    observed = {}

    async def sandbox_execute(_project_id, candidate_files, _stdin="", *, test_cases=None):
        observed["files"] = candidate_files
        observed["test_cases"] = test_cases
        return {
            "status": "completed",
            "executed": True,
            "passed": True,
            "exit_code": 0,
            "execution_status": "passed",
            "comparison_status": "not_provided",
            "results": [{
                "test_case": 1,
                "actual": "actual output",
                "expected": None,
                "comparison_status": "not_provided",
            }],
        }

    async def validation_compile(_project_id, _files, _findings):
        return {
            "passed": True,
            "status": "passed",
            "findings": [],
            "sandbox_result": {
                "status": "completed",
                "executed": True,
                "passed": True,
                "exit_code": 0,
                "stdout": "",
                "check_type": "static_compilation",
            },
        }

    monkeypatch.setattr(sandbox_client, "execute", sandbox_execute)
    monkeypatch.setattr(validation_client, "validate", validation_compile)

    result = asyncio.run(nodes.validate({
        "project_id": "javascript-output-comparison",
        "project_files": files,
        "project_context": {"test_cases": test_cases},
        "findings": [],
        "repair_attempts": [],
        "agent_errors": [],
        "workflow_trace": [],
        "stage_history": [],
    }))

    assert observed == {"files": files, "test_cases": test_cases}
    assert result["sandbox_result"]["results"][0]["actual"] == "actual output"
    assert "check_type" not in result["sandbox_result"]
    assert result["validation_status"] == "passed"
    assert result["stage_history"][-1]["status"] == "passed"


def test_failed_candidate_validation_rolls_back_to_original_files(monkeypatch):
    from app.graph import nodes
    from app.services import sandbox_client, validation_client

    original = {"main.py": "print('original')\n"}
    candidate = {"main.py": "print('broken candidate')\n"}

    async def sandbox_failure(_project_id, files, *_args, **_kwargs):
        assert files == candidate
        return {
            "status": "failed",
            "executed": True,
            "passed": False,
            "exit_code": 1,
            "stderr": "candidate test failed",
        }

    async def validation_failure(_project_id, files, *_args, **_kwargs):
        assert files == candidate
        return {"passed": False, "status": "failed", "findings": [], "error": None}

    monkeypatch.setattr(sandbox_client, "execute", sandbox_failure)
    monkeypatch.setattr(validation_client, "validate", validation_failure)
    result = asyncio.run(nodes.validate({
        "project_id": "rollback-invalid-candidate",
        "project_files": original,
        "repaired_code": candidate,
        "findings": [{"file": "main.py", "issue": "F401"}],
        "reanalysis_findings": [],
        "reanalysis_summary": {},
        "stage_history": [],
        "agent_errors": [],
        "repair_attempts": [{
            "outcome": "partial",
            "changed_files": ["main.py"],
            "candidate_files": candidate,
        }],
        "workflow_trace": [],
    }))

    assert result["repaired_code"] == original
    assert result["reanalysis_findings"] == [{"file": "main.py", "issue": "F401"}]
    assert result["repair_attempts"][-1]["candidate_rollback"] is True
    assert "candidate test failed" in result["repair_attempts"][-1]["candidate_rejection_reason"]


def test_needs_review_candidate_is_kept_with_its_validation_results(monkeypatch):
    from app.graph import nodes
    from app.services import sandbox_client, validation_client

    original = {"main.py": "if True\n    print('broken')\n"}
    candidate = {"main.py": "if True:\n    print('fixed')\n"}
    style_finding = {
        "file": "main.py",
        "issue": "E302",
        "description": "Expected two blank lines before the function.",
    }

    async def sandbox_pass(_project_id, files, *_args, **_kwargs):
        assert files == candidate
        return {"status": "completed", "executed": True, "passed": True, "exit_code": 0}

    async def validation_needs_review(_project_id, files, *_args, **_kwargs):
        assert files == candidate
        return {
            "status": "needs_review",
            "passed": True,
            "findings": [style_finding],
            "error": None,
        }

    monkeypatch.setattr(sandbox_client, "execute", sandbox_pass)
    monkeypatch.setattr(validation_client, "validate", validation_needs_review)
    result = asyncio.run(nodes.validate({
        "project_id": "keep-reviewable-candidate",
        "project_files": original,
        "repaired_code": candidate,
        "findings": [],
        "reanalysis_findings": [style_finding],
        "reanalysis_summary": {},
        "stage_history": [],
        "agent_errors": [],
        "repair_attempts": [{
            "outcome": "partial",
            "changed_files": ["main.py"],
            "candidate_files": candidate,
        }],
        "workflow_trace": [],
    }))

    assert result["validation_status"] == "needs_review"
    assert result["repaired_code"] == candidate
    assert result["sandbox_result"]["exit_code"] == 0
    assert result["validation_results"]["findings"] == [style_finding]
    assert result["repair_attempts"][-1]["reviewed_files"] == candidate
    assert result["repair_attempts"][-1].get("candidate_rollback") is not True


def test_failed_retry_restores_last_reviewed_candidate_and_keeps_failure_evidence(monkeypatch):
    from app.graph import nodes
    from app.services import sandbox_client, validation_client

    original = {"main.py": "print('original')\n"}
    reviewed = {"main.py": "print('reviewed')\n"}
    rejected = {"main.py": "print('rejected')\n"}
    review_sandbox = {"status": "completed", "executed": True, "passed": True, "exit_code": 0}
    review_validation = {"status": "needs_review", "passed": False, "findings": [], "error": None}

    async def sandbox_failure(_project_id, files, *_args, **_kwargs):
        assert files == rejected
        return {
            "status": "failed",
            "executed": True,
            "passed": False,
            "exit_code": 1,
            "stderr": "candidate execution failed",
        }

    async def validation_failure(_project_id, files, *_args, **_kwargs):
        assert files == rejected
        return {"passed": False, "status": "failed", "findings": [], "error": None}

    monkeypatch.setattr(sandbox_client, "execute", sandbox_failure)
    monkeypatch.setattr(validation_client, "validate", validation_failure)
    result = asyncio.run(nodes.validate({
        "project_id": "restore-reviewed-candidate",
        "project_files": original,
        "repaired_code": rejected,
        "findings": [],
        "reanalysis_findings": [],
        "reanalysis_summary": {},
        "stage_history": [],
        "agent_errors": [],
        "repair_attempts": [
            {
                "outcome": "needs_review",
                "changed_files": ["main.py"],
                "validation_status": "needs_review",
                "reviewed_files": reviewed,
                "reviewed_findings": [],
                "reviewed_quality_summary": {},
                "reviewed_sandbox_result": review_sandbox,
                "reviewed_validation_results": review_validation,
            },
            {
                "outcome": "partial",
                "changed_files": ["main.py"],
                "candidate_files": rejected,
            },
        ],
        "workflow_trace": [],
    }))

    assert result["validation_status"] == "needs_review"
    assert result["repaired_code"] == reviewed
    assert result["sandbox_result"] == review_sandbox
    assert result["validation_results"] == review_validation
    rejected_attempt = result["repair_attempts"][-1]
    assert rejected_attempt["candidate_rollback"] is True
    assert rejected_attempt["candidate_validation_status"] == "failed"
    assert rejected_attempt["candidate_sandbox_result"]["stderr"] == "candidate execution failed"
    assert rejected_attempt["candidate_validation_results"]["status"] == "failed"


def test_validation_service_error_is_not_reported_as_blocked_or_success(monkeypatch):
    from app.graph import nodes
    from app.services import sandbox_client, validation_client

    async def sandbox_pass(*_args, **_kwargs):
        return {"executed": True, "passed": True, "exit_code": 0, "status": "completed"}

    async def validation_error(*_args, **_kwargs):
        return {"passed": False, "status": "error", "error": "validation service timed out"}

    monkeypatch.setattr(sandbox_client, "execute", sandbox_pass)
    monkeypatch.setattr(validation_client, "validate", validation_error)
    result = asyncio.run(nodes.validate({
        "project_id": "validation-error",
        "project_files": {"main.py": "print('ok')\n"},
        "findings": [],
        "stage_history": [],
        "agent_errors": [],
        "repair_attempts": [],
        "workflow_trace": [],
    }))

    assert result["validation_status"] == "error"
    assert result["stage_history"][-1]["status"] == "error"
    assert result["validation_results"]["passed"] is False


def test_repair_records_only_changed_python_files_with_dependency_manifests(monkeypatch):
    from app.graph import nodes
    from app.services import repair_client

    project_files = {
        "main.py": "print('before')\n",
        "requirements.txt": "requests==2.32.0\n",
    }

    async def repair_with_changed_source(project_id, files, findings, repair_feedback=""):
        assert files == {"main.py": project_files["main.py"]}
        return {
            "status": "completed",
            "repaired_files": [{"path": "main.py", "content": "print('after')\n"}],
        }

    monkeypatch.setattr(repair_client, "repair", repair_with_changed_source)

    result = asyncio.run(nodes.repair({
        "project_id": "project-with-manifest",
        "project_files": project_files,
        "findings": [{"finding_id": "finding-1", "file": "main.py"}],
        "repair_attempts": [],
        "agent_errors": [],
        "workflow_trace": [],
        "stage_history": [],
    }))

    attempt = result["repair_attempts"][0]
    assert attempt["changed_files"] == ["main.py"]
    assert attempt["input_files"] == {"main.py": project_files["main.py"]}
    assert attempt["candidate_files"] == {"main.py": "print('after')\n"}
    assert result["repaired_code"]["requirements.txt"] == project_files["requirements.txt"]


def test_reanalysis_runs_all_selected_analysis_agents(monkeypatch):
    from app.graph import nodes
    from app.services import bug_resource_client, quality_client, security_client

    called = []
    monkeypatch.setattr(nodes, "_analysis_agent_flags", lambda checks: (True, True, True))

    async def security(project_id, files, context):
        called.append("security")
        return {"findings": [{"file": "main.py", "issue": "B101", "category": "security", "tool": "bandit"}]}

    async def quality(project_id, files, context, tools):
        called.append("quality")
        return {"findings": [{"file": "main.py", "issue": "F401", "category": "code_quality", "tool": "ruff"}], "quality_summary": {"overall_score": 90}}

    async def bug_resource(project_id, files, context):
        called.append("bug_resource")
        return {"findings": [{"file": "main.py", "issue": "Bugs", "category": "bug", "tool": "bug-resource"}]}

    monkeypatch.setattr(security_client, "analyze", security)
    monkeypatch.setattr(quality_client, "analyze", quality)
    monkeypatch.setattr(bug_resource_client, "analyze", bug_resource)

    result = asyncio.run(nodes.reanalyze({
        "project_id": "project-reanalysis",
        "project_files": {"main.py": "print('before')\n"},
        "repaired_code": {"main.py": "print('after')\n"},
        "project_context": {},
        "tools": ["ruff"],
        "stage_history": [],
        "workflow_trace": [],
        "agent_errors": [],
    }))

    assert called == ["security", "quality", "bug_resource"]
    assert {finding["issue"] for finding in result["reanalysis_findings"]} == {"B101", "F401", "Bugs"}
    assert result["reanalysis_summary"] == {"overall_score": 90}
    assert result["stage_history"][-1]["status"] == "completed"
    assert set(result["stage_history"][-1]["agents"].values()) == {"completed"}


def test_reanalysis_rejects_new_critical_security_finding_without_total_count_increase(monkeypatch):
    from app.graph import nodes
    from app.services import bug_resource_client, quality_client, security_client

    async def security(*_args, **_kwargs):
        return {"findings": [{
            "file": "main.js",
            "issue": "SEC-001",
            "category": "security",
            "severity": "critical",
            "tool": "semgrep",
        }]}

    async def no_quality(*_args, **_kwargs):
        return {"findings": [], "quality_summary": {"quality_score": 90}}

    async def no_bugs(*_args, **_kwargs):
        return {"findings": []}

    monkeypatch.setattr(nodes, "_analysis_agent_flags", lambda _checks: (True, True, True))
    monkeypatch.setattr(security_client, "analyze", security)
    monkeypatch.setattr(quality_client, "analyze", no_quality)
    monkeypatch.setattr(bug_resource_client, "analyze", no_bugs)
    safe_files = {"main.js": "const value = 1;\n"}
    result = asyncio.run(nodes.reanalyze({
        "project_id": "critical-security-regression",
        "project_files": safe_files,
        "repaired_code": {"main.js": "const value = 2;\n"},
        "findings": [{
            "file": "main.js",
            "issue": "QUALITY-1",
            "category": "code_quality",
            "severity": "low",
            "tool": "eslint",
        }],
        "quality_summary": {"quality_score": 80},
        "best_repair_candidate": {
            "files": safe_files,
            "findings": [{
                "file": "main.js",
                "issue": "QUALITY-1",
                "category": "code_quality",
                "severity": "low",
                "tool": "eslint",
            }],
            "quality_summary": {"quality_score": 80},
            "finding_count": 1,
            "quality_score": 80,
            "comparison_available": True,
        },
        "project_context": {},
        "tools": ["eslint"],
        "stage_history": [],
        "workflow_trace": [],
        "agent_errors": [],
    }))

    assert result["repaired_code"] == safe_files
    assert result["stage_history"][-1]["regression_rollback"] is True
    assert any("critical or high-severity security" in reason for reason in result["stage_history"][-1]["regression_reasons"])


def test_comparison_preserves_same_rule_findings_and_marks_failed_agents_unverified():
    from app.graph import nodes

    security_finding = {"file": "a.py", "line_number": 4, "issue": "B101", "category": "security", "tool": "bandit"}
    quality_finding = {"file": "a.py", "line_number": 4, "issue": "F401", "category": "code_quality", "tool": "ruff"}
    complete = nodes.compare_results({
        "findings": [security_finding, quality_finding],
        "reanalysis_findings": [quality_finding],
        "stage_history": [
            {"stage": "analyze", "status": "completed", "agents": {
                "security": "completed", "quality": "completed", "bug_resource": "skipped",
            }},
            {"stage": "reanalyze", "status": "completed", "agents": {
                "security": "completed", "quality": "completed", "bug_resource": "skipped",
            }},
        ],
        "project_files": {},
        "repaired_code": {},
        "repair_attempts": [],
        "workflow_trace": [],
    })["comparison"]
    assert complete["findings_before"] == 2
    assert complete["findings_after"] == 1
    assert complete["resolved_count"] == 1
    assert complete["unchanged_count"] == 1
    assert complete["remaining_original_count"] == 1
    assert complete["unverified_before_count"] == 0
    assert complete["unverified_after_count"] == 0
    assert complete["findings_before"] == complete["resolved_count"] + complete["remaining_original_count"]
    assert complete["improved"] is True

    partial = nodes.compare_results({
        "findings": [security_finding, quality_finding],
        "reanalysis_findings": [quality_finding],
        "stage_history": [
            {"stage": "analyze", "status": "completed", "agents": {
                "security": "completed", "quality": "completed", "bug_resource": "skipped",
            }},
            {"stage": "reanalyze", "status": "partial", "agents": {
                "security": "failed", "quality": "completed", "bug_resource": "skipped",
            }},
        ],
        "project_files": {},
        "repaired_code": {},
        "repair_attempts": [],
        "workflow_trace": [],
    })["comparison"]
    assert partial["status"] == "partial"
    assert partial["findings_after"] is None
    assert partial["resolved_count"] == 0
    assert partial["unverified_count"] == 1
    assert partial["remaining_original_count"] == 2
    assert partial["unverified_before_count"] == 1
    assert partial["unverified_after_count"] == 0
    assert partial["findings_before"] == partial["resolved_count"] + partial["remaining_original_count"]
    assert partial["improved"] is False


def test_comparison_separates_new_findings_from_remaining_original_findings():
    from app.graph import nodes

    original = {"file": "main.py", "issue": "F401", "category": "code_quality", "tool": "ruff"}
    introduced = {"file": "main.py", "issue": "E501", "category": "code_quality", "tool": "ruff"}
    comparison = nodes.compare_results({
        "findings": [original],
        "reanalysis_findings": [original, introduced],
        "stage_history": [
            {"stage": "analyze", "status": "completed", "agents": {"quality": "completed"}},
            {"stage": "reanalyze", "status": "completed", "agents": {"quality": "completed"}},
        ],
        "project_files": {},
        "repaired_code": {},
        "repair_attempts": [],
        "workflow_trace": [],
    })["comparison"]

    assert comparison["findings_before"] == 1
    assert comparison["resolved_count"] == 0
    assert comparison["remaining_original_count"] == 1
    assert comparison["new_count"] == 1
    assert comparison["unverified_before_count"] == 0
    assert comparison["unverified_after_count"] == 0
    assert comparison["remaining_original_count"] == comparison["unchanged_count"] + comparison["unverified_before_count"]
    assert [finding["issue"] for finding in comparison["new_findings"]] == ["E501"]


def test_comparison_treats_rule_location_and_description_changes_as_distinct_findings():
    from app.graph import nodes

    before = {"file": "src/main.py", "line_number": 4, "issue": "C0114", "description": "Missing module docstring", "category": "code_quality", "tool": "pylint"}
    after = {"file": "src/main.py", "line_number": 1, "issue": "C0114", "description": "Module docstring is missing", "category": "code_quality", "tool": "pylint"}
    comparison = nodes.compare_results({
        "findings": [before],
        "reanalysis_findings": [after],
        "quality_summary": {"quality_score": 78},
        "reanalysis_summary": {"quality_score": 98},
        "stage_history": [
            {"stage": "analyze", "status": "completed", "agents": {"quality": "completed"}},
            {"stage": "reanalyze", "status": "completed", "agents": {"quality": "completed"}},
        ],
        "project_files": {},
        "repaired_code": {},
        "repair_attempts": [],
        "workflow_trace": [],
    })["comparison"]

    assert comparison["resolved_count"] == 1
    assert comparison["unchanged_count"] == 0
    assert comparison["new_count"] == 1
    assert comparison["quality_score_before"] == 78
    assert comparison["quality_score_after"] == 98


def test_comparison_distinguishes_generic_analyzer_findings_by_diagnostic():
    from app.graph import nodes

    before = [
        {
            "file": "src/main.js",
            "issue": "LANGUAGE_ANALYZER",
            "description": "'unusedValue' is assigned a value but never used.",
            "category": "code_quality",
            "tool": "ESLint",
        },
        {
            "file": "src/main.js",
            "issue": "LANGUAGE_ANALYZER",
            "description": "'window' is not defined.",
            "category": "code_quality",
            "tool": "ESLint",
        },
    ]
    after = [
        before[0],
        {
            "file": "src/main.js",
            "issue": "LANGUAGE_ANALYZER",
            "description": "'document' is not defined.",
            "category": "code_quality",
            "tool": "ESLint",
        },
    ]
    comparison = nodes.compare_results({
        "findings": before,
        "reanalysis_findings": after,
        "stage_history": [
            {"stage": "analyze", "status": "completed", "agents": {"quality": "completed"}},
            {"stage": "reanalyze", "status": "completed", "agents": {"quality": "completed"}},
        ],
        "project_files": {},
        "repaired_code": {},
        "repair_attempts": [],
        "workflow_trace": [],
    })["comparison"]

    assert comparison["resolved_count"] == 1
    assert comparison["unchanged_count"] == 1
    assert comparison["new_count"] == 1
    assert comparison["resolved_findings"][0]["description"] == "'window' is not defined."
    assert comparison["new_findings"][0]["description"] == "'document' is not defined."


def test_comparison_matches_generic_diagnostic_by_symbol_after_line_and_message_change():
    from app.graph import nodes

    before = {
        "file": "src/main.js",
        "line_number": 18,
        "issue": "LANGUAGE_ANALYZER",
        "description": "'API_TOKEN' is assigned a value but never used.",
        "category": "code_quality",
        "tool": "ESLint",
    }
    after = {
        **before,
        "line_number": 3,
        "description": "'API_TOKEN' is declared but its value is never read.",
    }
    comparison = nodes.compare_results({
        "findings": [before],
        "reanalysis_findings": [after],
        "stage_history": [
            {"stage": "analyze", "status": "completed", "agents": {"quality": "completed"}},
            {"stage": "reanalyze", "status": "completed", "agents": {"quality": "completed"}},
        ],
        "project_files": {},
        "repaired_code": {},
        "repair_attempts": [],
        "workflow_trace": [],
    })["comparison"]

    assert comparison["resolved_count"] == 0
    assert comparison["unchanged_count"] == 1
    assert comparison["new_count"] == 0


def test_comparison_deduplicates_identical_analyzer_findings_without_losing_distinct_lines():
    from app.graph import nodes

    finding = {
        "finding_id": "first-id",
        "file": "main.js",
        "line_number": 4,
        "issue": "LANGUAGE_ANALYZER",
        "description": "'API_TOKEN' is unused.",
        "category": "code_quality",
        "tool": "ESLint",
    }
    duplicate = {**finding, "finding_id": "regenerated-id"}
    comparison = nodes.compare_results({
        "findings": [finding, duplicate],
        "reanalysis_findings": [finding, duplicate],
        "stage_history": [
            {"stage": "analyze", "status": "completed", "agents": {"quality": "completed"}},
            {"stage": "reanalyze", "status": "completed", "agents": {"quality": "completed"}},
        ],
        "project_files": {},
        "repaired_code": {},
        "repair_attempts": [],
        "workflow_trace": [],
    })["comparison"]

    assert comparison["findings_before"] == 1
    assert comparison["findings_after"] == 1
    assert comparison["unchanged_count"] == 1


def test_comparison_reports_unavailable_checks_as_skipped_and_needs_review():
    from app.graph import nodes

    finding = {"file": "main.go", "issue": "GO001", "category": "code_quality", "tool": "golangci-lint"}
    result = nodes.compare_results({
        "findings": [finding],
        "reanalysis_findings": [finding],
        "quality_summary": {"tool_runs": [{"tool": "golangci-lint", "status": "unavailable"}]},
        "reanalysis_summary": {"tool_runs": [{"tool": "golangci-lint", "status": "unavailable"}]},
        "stage_history": [
            {"stage": "analyze", "status": "completed", "agents": {"quality": "completed"}},
            {"stage": "reanalyze", "status": "completed", "agents": {"quality": "completed"}},
        ],
        "project_files": {},
        "repaired_code": {},
        "repair_attempts": [{"changed_files": ["main.go"]}],
        "workflow_trace": [],
    })

    assert result["comparison"]["status"] == "partial"
    assert result["comparison"]["skipped_checks"] == ["golangci-lint"]
    assert result["repair_attempts"][-1]["repair_status"] == "needs_review"
    assert result["repair_attempts"][-1]["verification_status"] == "needs_review"


def test_comparison_does_not_merge_distinct_diagnostics_with_the_same_rule():
    from app.graph import nodes

    before = [
        {
            "file": "main.py",
            "issue": "F821",
            "description": "Undefined name `first_name`.",
            "category": "code_quality",
            "tool": "ruff",
        },
        {
            "file": "main.py",
            "issue": "F821",
            "description": "Undefined name `old_value`.",
            "category": "code_quality",
            "tool": "ruff",
        },
    ]
    after = [
        before[0],
        {
            **before[1],
            "description": "Undefined name `new_value`.",
        },
    ]

    comparison = nodes.compare_results({
        "findings": before,
        "reanalysis_findings": after,
        "stage_history": [
            {"stage": "analyze", "status": "completed", "agents": {"quality": "completed"}},
            {"stage": "reanalyze", "status": "completed", "agents": {"quality": "completed"}},
        ],
        "project_files": {},
        "repaired_code": {},
        "repair_attempts": [],
        "workflow_trace": [],
    })["comparison"]

    assert comparison["unchanged_count"] == 1
    assert comparison["resolved_count"] == 1
    assert comparison["new_count"] == 1


@pytest.mark.parametrize(
    (
        "baseline_findings",
        "baseline_summary",
        "best_candidate",
        "candidate_findings",
        "candidate_summary",
        "reason",
    ),
    [
        (
            [{"file": "Main.java", "issue": "Q1", "category": "code_quality", "tool": "test"}],
            {"overall_score": 80},
            None,
            [
                {"file": "Main.java", "issue": "Q2", "category": "code_quality", "tool": "test"},
                {"file": "Main.java", "issue": "Q3", "category": "code_quality", "tool": "test"},
            ],
            {"overall_score": 90},
            "increased findings",
        ),
        (
            [
                {"file": "Main.java", "issue": "Q1", "category": "code_quality", "tool": "test"},
                {"file": "Main.java", "issue": "Q2", "category": "code_quality", "tool": "test"},
            ],
            {"overall_score": 70},
            {
                "files": {"Main.java": "best candidate\n"},
                "findings": [{"file": "Main.java", "issue": "Q1", "category": "code_quality", "tool": "test"}],
                "quality_summary": {"overall_score": 85},
                "finding_count": 1,
                "quality_score": 85,
            },
            [],
            {"overall_score": 40},
            "lowered quality score",
        ),
    ],
)
def test_reanalyze_rolls_back_candidate_regressions(
    monkeypatch,
    baseline_findings,
    baseline_summary,
    best_candidate,
    candidate_findings,
    candidate_summary,
    reason,
):
    from app.graph import nodes

    async def analyzed_candidate(*_args):
        return {
            "code_quality": {
                "findings": candidate_findings,
                "quality_summary": candidate_summary,
            }
        }

    monkeypatch.setattr(nodes, "_collect_analysis_responses", analyzed_candidate)
    original_files = {"Main.java": "original source\n"}
    rejected_files = {"Main.java": "regressive candidate\n"}
    state = {
        "project_id": "java-regression-guard",
        "project_files": original_files,
        "repaired_code": rejected_files,
        "findings": baseline_findings,
        "quality_summary": baseline_summary,
        "project_context": {"analysis_checks": {
            "security": False,
            "code_quality": True,
            "bug_resource": False,
            "complexity": False,
        }},
        "repair_attempts": [{"changed_files": ["Main.java"]}],
        "stage_history": [{"stage": "analyze", "status": "completed", "agents": {"quality": "completed"}}],
        "agent_errors": [],
        "workflow_trace": [],
    }
    if best_candidate is not None:
        state["best_repair_candidate"] = best_candidate

    result = asyncio.run(nodes.reanalyze(state))

    expected_best = best_candidate or {
        "files": original_files,
        "findings": baseline_findings,
        "quality_summary": baseline_summary,
        "finding_count": len(baseline_findings),
        "quality_score": baseline_summary["overall_score"],
    }
    assert result["repaired_code"] == expected_best["files"]
    assert result["reanalysis_findings"] == expected_best["findings"]
    assert result["reanalysis_summary"] == expected_best["quality_summary"]
    assert result["stage_history"][-1]["regression_rollback"] is True
    assert reason in " ".join(result["stage_history"][-1]["regression_reasons"]).lower()
    assert result["repair_attempts"][-1]["regression_rollback"] is True


def test_start_request_rejects_invalid_project_files():
    with pytest.raises(ValueError):
        StartAnalysisRequest(project_id="", files=[])


def test_start_request_accepts_dependency_manifest_with_python_source():
    request = StartAnalysisRequest(project_id="django", files=[
        {"path": "manage.py", "content": "print('entry')"},
        {"path": "requirements.txt", "content": "Django==5.2.4"},
        {"path": "site/pyproject.toml", "content": "[project]\ndependencies = []"},
    ])

    assert len(request.files) == 3


def test_start_request_rejects_manifest_only_project():
    with pytest.raises(ValueError, match="at least one supported source file"):
        StartAnalysisRequest(project_id="manifest-only", files=[
            {"path": "requirements.txt", "content": "Django==5.2.4"},
        ])


def test_start_request_rejects_duplicate_paths():
    with pytest.raises(ValueError):
        StartAnalysisRequest(
            project_id="p",
            files=[
                {"path": "main.py", "content": ""},
                {"path": "main.py", "content": ""},
            ],
        )


@pytest.mark.parametrize("path", ["C:/private/app.py", "../app.py", "/tmp/app.py", "notes.txt"])
def test_start_request_rejects_unsafe_or_non_python_paths(path):
    with pytest.raises(ValueError):
        StartAnalysisRequest(project_id="p", files=[{"path": path, "content": "print('x')"}])


def test_workflow_summary_records_quality_gate_and_stop_status(monkeypatch):
    from app.services import (
        bug_resource_client,
        quality_client,
        repair_client,
        sandbox_client,
        security_client,
        validation_client,
    )

    async def quality_pass(*args, **kwargs):
        return {
            "findings": [{
                "file": "app.py",
                "line_number": 1,
                "issue": "F841",
                "severity": "medium",
                "description": "unused variable",
                "recommendation": "remove variable",
                "tool": "ruff",
            }],
            "quality_summary": {
                "overall_score": 88,
                "quality_gate_status": "PASS",
                "quality_gate": {"status": "PASS", "overall_score": 88, "thresholds": {"error_limit": 0, "warning_limit": 5}},
                "errors": 0,
                "warnings": 1,
                "complexity_issues": 0,
            },
        }

    async def no_findings(*args, **kwargs):
        return {"findings": []}

    async def unchanged_repair(*args, **kwargs):
        files = args[1]
        return {"repaired_files": [{"path": path, "content": content} for path, content in files.items()]}

    async def sandbox_pass(*args, **kwargs):
        return {"executed": True, "exit_code": 0}

    async def validation_pass(*args, **kwargs):
        return {"passed": True, "tests": {"passed": 1}}

    monkeypatch.setattr(security_client, "analyze", no_findings)
    monkeypatch.setattr(quality_client, "analyze", quality_pass)
    monkeypatch.setattr(bug_resource_client, "analyze", no_findings)
    monkeypatch.setattr(repair_client, "repair", unchanged_repair)
    monkeypatch.setattr(sandbox_client, "execute", sandbox_pass)
    monkeypatch.setattr(validation_client, "validate", validation_pass)

    wf = build_workflow()
    result = asyncio.run(wf.ainvoke({
        "project_id": "proj-summary",
        "project_files": {"app.py": "value = 1\nprint(value)\n"},
        "project_context": {},
    }))

    summary = result.get("final_report", {}).get("workflow_summary", {})
    assert summary.get("workflow_status") in {"SUCCESS", "PASSED", "PARTIAL_RESULT", "REVIEW_REQUIRED", "FAILED"}
    assert summary.get("quality_gate") in {"PASS", "FAIL"}
    assert "quality_score" in summary


def test_workflow_retries_rejected_repair_with_feedback_and_can_pass(monkeypatch):
    from app.services import (
        bug_resource_client,
        quality_client,
        repair_client,
        sandbox_client,
        security_client,
        validation_client,
    )

    source = "import os\nprint('ok')\n"
    repaired = "print('ok')\n"
    finding = {
        "finding_id": "QUAL-001",
        "category": "code_quality",
        "severity": "medium",
        "file": "main.py",
        "line_number": 1,
        "issue": "F401",
        "description": "`os` imported but unused",
        "recommendation": "Remove the unused import.",
        "tool": "ruff",
    }
    repair_calls = []
    events = []

    async def quality(project_id, files, context, tools):
        events.append(("quality", files["main.py"]))
        findings = [finding] if "import os" in files["main.py"] else []
        return {
            "findings": findings,
            "quality_summary": {
                "quality_gate_status": "PASS",
                "selected_tools": ["ruff"],
                "tool_runs": [{"tool": "ruff", "status": "completed"}],
            },
        }

    async def no_findings(*_args, **_kwargs):
        return {"findings": []}

    async def repair(project_id, files, findings, repair_feedback=""):
        events.append(("repair", files["main.py"]))
        repair_calls.append(repair_feedback)
        if len(repair_calls) == 1:
            return {
                "status": "failed",
                "repaired_files": [{"path": path, "content": content} for path, content in files.items()],
                "repair_details": [{
                    "path": "main.py",
                    "changed": False,
                    "explanation": "Repair rejected: main.py: Repair introduced Ruff findings: W292 at line 2, column 1",
                    "candidate_code": "import os\nprint('ok')",
                }],
                "unresolved_findings": findings,
                "error": "Repair rejected: main.py: Repair introduced Ruff findings: W292 at line 2, column 1",
            }
        return {
            "status": "completed",
            "repaired_files": [{"path": "main.py", "content": repaired}],
            "repair_details": [{"path": "main.py", "changed": True, "explanation": "Removed unused import."}],
            "unresolved_findings": [],
        }

    async def sandbox(_project_id, files, _stdin=""):
        events.append(("sandbox", files["main.py"]))
        return {"status": "completed", "executed": True, "passed": True, "exit_code": 0}

    async def validation(_project_id, files, _findings):
        events.append(("validation", files["main.py"]))
        if "import os" in files["main.py"]:
            return {"passed": True, "status": "needs_review", "findings": [finding], "error": None}
        return {"passed": True, "status": "passed", "findings": [], "error": None}

    monkeypatch.setattr(security_client, "analyze", no_findings)
    monkeypatch.setattr(quality_client, "analyze", quality)
    monkeypatch.setattr(bug_resource_client, "analyze", no_findings)
    monkeypatch.setattr(repair_client, "repair", repair)
    monkeypatch.setattr(sandbox_client, "execute", sandbox)
    monkeypatch.setattr(validation_client, "validate", validation)

    result = asyncio.run(build_workflow().ainvoke({
        "project_id": "repair-retry-e2e",
        "project_files": {"main.py": source},
        "project_context": {"analysis_checks": {
            "code_quality": True,
            "security": False,
            "bug_resource": False,
            "complexity": False,
            "automated_repair": True,
            "final_validation": True,
            "ai_report_recommendations": False,
        }},
        "tools": ["ruff"],
    }))

    assert len(repair_calls) == 2
    assert [name for name, _ in events] == [
        "quality", "repair", "quality", "sandbox", "validation",
        "repair", "quality", "sandbox", "validation",
    ]
    assert events[3][1] == source
    assert events[4][1] == source
    assert events[7][1] == repaired
    assert events[8][1] == repaired
    assert repair_calls[0] == ""
    assert "W292 at line 2" in repair_calls[1]
    assert "F401" in repair_calls[1]
    assert result["retry_count"] == 1
    assert result["validation_status"] == "passed"
    assert result["final_report"]["workflow_status"] == "SUCCESS"
    assert result["final_report"]["repair_attempts"][0]["rejected_candidates"][0]["code"].endswith("print('ok')")


def test_workflow_stops_retrying_when_same_finding_persists_after_validated_repair(monkeypatch):
    from app.services import bug_resource_client, quality_client, repair_client, sandbox_client, security_client, validation_client

    original = "value = 0\n"
    candidates = ["value = 1\n", '"""Module documentation."""\nvalue = 1\n']
    repair_inputs = []
    initial_finding = {
        "finding_id": "initial-issue",
        "file": "main.py",
        "issue": "F001",
        "category": "code_quality",
        "tool": "ruff",
        "message": "Initial issue",
        "severity": "medium",
    }
    residual_finding = {
        "finding_id": "docstring-warning",
        "file": "main.py",
        "issue": "C0114",
        "category": "code_quality",
        "tool": "pylint",
        "message": "Missing module docstring",
        "severity": "low",
    }

    async def no_findings(*_args, **_kwargs):
        return {"findings": []}

    async def quality(_project_id, files, _context, _tools):
        findings = [initial_finding] if files["main.py"] == original else [residual_finding]
        return {
            "findings": findings,
            "quality_summary": {
                "quality_gate_status": "PASS",
                "selected_tools": ["ruff"],
                "tool_runs": [{"tool": "ruff", "status": "completed"}],
            },
        }

    async def repair(_project_id, files, _findings, *_args, **_kwargs):
        repair_inputs.append(files["main.py"])
        candidate = candidates[len(repair_inputs) - 1]
        return {
            "status": "completed",
            "outcome": "applied",
            "changed_files": ["main.py"],
            "repaired_files": [{"path": "main.py", "content": candidate}],
            "unresolved_findings": [],
            "error": None,
        }

    async def sandbox(*_args, **_kwargs):
        return {"status": "completed", "executed": True, "passed": True, "exit_code": 0}

    async def validation(*_args, **_kwargs):
        return {"passed": True, "status": "passed", "findings": [], "error": None}

    monkeypatch.setattr(security_client, "analyze", no_findings)
    monkeypatch.setattr(bug_resource_client, "analyze", no_findings)
    monkeypatch.setattr(quality_client, "analyze", quality)
    monkeypatch.setattr(repair_client, "repair", repair)
    monkeypatch.setattr(sandbox_client, "execute", sandbox)
    monkeypatch.setattr(validation_client, "validate", validation)

    result = asyncio.run(build_workflow().ainvoke({
        "project_id": "repair-no-progress",
        "project_files": {"main.py": original},
        "project_context": {"analysis_checks": {
            "code_quality": True,
            "security": False,
            "bug_resource": False,
            "complexity": False,
            "automated_repair": True,
            "final_validation": True,
            "ai_report_recommendations": False,
        }},
        "tools": ["ruff"],
    }))

    assert len(repair_inputs) == 2
    assert result["retry_count"] == 1
    assert result["validation_status"] == "needs_review"
    assert result["final_report"]["workflow_status"] == "REVIEW_REQUIRED"
    assert "Sandbox and validation passed" in result["final_report"]["workflow_status_reason"]
    assert result["final_report"]["repair_attempts"][-1]["outcome"] == "validated"
    assert result["final_report"]["comparison"]["remaining_issues"] == ["C0114"]


@pytest.mark.parametrize(
    "message",
    ["No Ollama endpoint is configured.", "Gemini API key is not configured."],
)
def test_report_recommendation_stage_skips_without_provider(monkeypatch, message):
    from app.graph import nodes
    from app.services import report_recommendation_client

    async def agent_skipped(files):
        return {"status": "skipped", "message": message}

    monkeypatch.setattr(report_recommendation_client, "analyze", agent_skipped)
    result = asyncio.run(nodes.report_and_recommend({
        "project_files": {"main.py": "print('hello')\n"},
        "project_context": {"analysis_checks": {"ai_report_recommendations": True}},
        "stage_history": [],
        "workflow_trace": [],
    }))

    assert result["ai_recommendations_status"]["status"] == "skipped"
    assert result["code_report"] == {}
    assert result["stage_history"][-1]["stage"] == "report_recommend"


def test_report_recommendation_stage_returns_agent_output(monkeypatch):
    from app.graph import nodes
    from app.services import report_recommendation_client

    async def fake_analyze(files):
        assert files == {"main.py": "print('hello')\n"}
        return {
            "status": "completed",
            "code_report": {"scanned_files": 1, "issues": []},
            "recommendation_report": {"fixes": [], "suggestions": []},
        }

    monkeypatch.setattr(report_recommendation_client, "analyze", fake_analyze)

    result = asyncio.run(nodes.report_and_recommend({
        "project_files": {"main.py": "print('hello')\n"},
        "project_context": {"analysis_checks": {"ai_report_recommendations": True}},
        "stage_history": [],
        "workflow_trace": [],
    }))

    assert result["ai_recommendations_status"]["status"] == "completed"
    assert result["code_report"]["scanned_files"] == 1
    assert result["recommendation_report"]["suggestions"] == []
