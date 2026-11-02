from fastapi.testclient import TestClient
from app import main
from app.main import app

client = TestClient(app)


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_analyze_returns_findings_response_shape():
    payload = {
        "project_id": "proj-1",
        "files": [{"path": "app.py", "content": "import os\n\ndef f():\n    pass\n"}],
        "tools": ["ruff"],
    }
    resp = client.post("/analyze", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["project_id"] == "proj-1"
    assert body["agent"] == "code_quality"
    assert "findings" in body
    assert "findings_count" in body


def test_python_default_tools_include_mypy(monkeypatch):
    selected = []
    for tool in ("ruff", "pylint", "radon", "mypy"):
        monkeypatch.setattr(
            main,
            f"run_{tool}",
            lambda _files, tool=tool: selected.append(tool) or [],
        )

    response = client.post("/analyze", json={
        "project_id": "python-default-tool-set",
        "files": [{"path": "main.py", "content": "value: int = 1\n"}],
    })

    assert response.status_code == 200
    assert selected == ["ruff", "pylint", "radon", "mypy"]
    assert response.json()["quality_summary"]["tool_results"]["mypy"]["status"] == "completed"


def test_partial_mypy_analysis_keeps_source_diagnostic_and_fails_quality_gate(monkeypatch):
    from app.analyzers.mypy_analyzer import PartialMypyAnalysisError

    issue = {
        "source": "mypy",
        "file": "src/main.py",
        "line_number": 3,
        "code": "MYPY_SYNTAX",
        "message": "expected an indented block",
        "severity_hint": "high",
    }

    def partial_mypy(_files):
        raise PartialMypyAnalysisError([issue])

    monkeypatch.setattr(main, "run_mypy", partial_mypy)
    response = client.post("/analyze", json={
        "project_id": "partial-mypy-diagnostic",
        "files": [{"path": "src/main.py", "content": "def main():\n\nreturn 0\n"}],
        "tools": ["mypy"],
    })

    assert response.status_code == 200
    body = response.json()
    assert body["findings"][0]["file"] == "src/main.py"
    assert body["findings"][0]["issue"] == "MYPY_SYNTAX"
    assert body["quality_summary"]["tool_results"]["mypy"]["status"] == "partial"
    assert body["quality_summary"]["quality_gate_status"] == "FAIL"
    assert body["error"] == "mypy: partial"


def test_non_python_analysis_uses_language_specific_analyzer(monkeypatch):
    monkeypatch.setattr(
        main,
        "_run_language_analyzer",
        lambda *_args: (
            [{
                "source": "ESLint",
                "file": "main.js",
                "line_number": 1,
                "code": "LANGUAGE_ANALYZER",
                "message": "'value' is assigned a value but never used.",
                "severity_hint": "medium",
            }],
            {"tool": "ESLint", "status": "completed", "finding_count": 1, "duration_ms": 1},
        ),
    )
    response = client.post("/analyze", json={
        "project_id": "javascript-quality",
        "files": [{
            "path": "main.js",
            "content": 'const value = "ok";\n',
        }],
        "tools": [],
    })

    assert response.status_code == 200
    body = response.json()
    assert body["error"] is None
    assert body["findings"][0]["issue"] == "LANGUAGE_ANALYZER"
    assert body["quality_summary"]["selected_tools"] == ["ESLint"]


def test_non_python_analysis_reports_unavailable_toolchain(monkeypatch):
    def unavailable(*_args):
        raise RuntimeError("Required language analyzer is unavailable: ESLint.")

    monkeypatch.setattr(main, "_run_language_analyzer", unavailable)
    response = client.post("/analyze", json={
        "project_id": "javascript-quality",
        "files": [{"path": "main.js", "content": 'console.log("ok");\n'}],
    })

    assert response.status_code == 200
    assert "unavailable" in response.json()["error"]
    assert response.json()["quality_summary"]["quality_gate_status"] == "FAIL"


def test_language_analyzer_output_ignores_non_diagnostic_tool_banners():
    lines = main._language_diagnostic_lines(
        "Starting audit...\n"
        "Audit done.\n"
        "Picked up JAVA_TOOL_OPTIONS: -Xmx192m\n"
        "[warning] /usr/bin/checkstyle: Unable to locate commons-cli\n"
        "[WARN] Progressbar rendering conflicts with reporting to STDOUT. No progressbar will be shown.\n"
        "Compilation succeeded - 1 warning(s)\n"
        "Build succeeded.\n"
        "1 Warning(s)\n"
        "0 Error(s)\n"
        "Time Elapsed 00:00:02.30\n"
        "Main.java:4:1: Avoid star imports.\n"
    )

    assert lines == ["Main.java:4:1: Avoid star imports."]


def test_language_analyzer_maps_sandbox_diagnostic_path_to_unique_source_file(monkeypatch):
    calls = []
    ticks = iter((1.0, 1.25))
    monkeypatch.setattr(main, "perf_counter", lambda: next(ticks))

    class FakeResponse:
        def raise_for_status(self):
            pass

        @staticmethod
        def json():
            return {
                "executed": True,
                "status": "completed",
                "validator": "javac",
                "stderr": "/tmp/cva-sandbox/src/main.java:7: error: cannot find symbol",
                "stdout": "",
            }

    monkeypatch.setenv("QUALITY_SANDBOX_SERVICE_URL", "http://sandbox")

    def fake_post(*args, **kwargs):
        calls.append((args, kwargs))
        return FakeResponse()

    monkeypatch.setattr(main.httpx, "post", fake_post)

    findings, tool_run = main._run_language_analyzer(
        "java-path-test",
        {"src/main.java": "class Main {}\n", "src/Helper.java": "class Helper {}\n"},
        "java",
    )

    assert len(findings) == 1
    assert findings[0]["file"] == "src/main.java"
    assert findings[0]["line_number"] == 7
    assert calls[0][1]["json"]["analysis_only"] is True
    assert calls[0][1]["json"]["timeout"] == 180
    assert calls[0][1]["timeout"] == 210
    assert tool_run["duration_ms"] == 250


def test_typescript_eslint_diagnostic_preserves_rule_and_repair_recommendation(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            pass

        @staticmethod
        def json():
            return {
                "executed": True,
                "status": "completed",
                "validator": "ESLint TypeScript + TypeScript compiler",
                "stdout": (
                    "/sandbox/run/main.ts:1:7:"
                    "[@typescript-eslint/no-unused-vars] "
                    "'unused' is assigned a value but never used.\n"
                ),
                "stderr": "",
            }

    monkeypatch.setenv("QUALITY_SANDBOX_SERVICE_URL", "http://sandbox")
    monkeypatch.setattr(main.httpx, "post", lambda *_args, **_kwargs: FakeResponse())

    raw_issues, _ = main._run_language_analyzer(
        "typescript-rule-id",
        {"main.ts": "const unused: number = 1;\n"},
        "typescript",
    )
    findings = main.normalize(raw_issues)

    assert raw_issues[0]["code"] == "@typescript-eslint/no-unused-vars"
    assert findings[0].issue == "@typescript-eslint/no-unused-vars"
    assert findings[0].recommendation == "Remove the unused variable or use it."
    assert findings[0].line_number == 1


def test_cppcheck_analysis_discards_source_excerpt_and_caret_lines(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            pass

        @staticmethod
        def json():
            return {
                "executed": True,
                "status": "completed",
                "validator": "Cppcheck",
                "stdout": (
                    "/sandbox/run/main.c:4:51: warning: Memory leak: p [memleak]\n"
                    "int main(void) { char *p = malloc(8); return 0; }\n"
                    "                                                  ^\n"
                    "/sandbox/run/main.c:4:26: warning: Variable 'p' is unused [unreadVariable]\n"
                    "int main(void) { char *p = malloc(8); return 0; }\n"
                    "                         ^\n"
                ),
                "stderr": "",
            }

    monkeypatch.setenv("QUALITY_SANDBOX_SERVICE_URL", "http://sandbox")
    monkeypatch.setattr(main.httpx, "post", lambda *_args, **_kwargs: FakeResponse())

    findings, tool_run = main._run_language_analyzer(
        "cppcheck-diagnostics",
        {"main.c": "int main(void) { char *p = malloc(8); return 0; }\n"},
        "c",
    )

    assert len(findings) == 2
    assert [finding["line_number"] for finding in findings] == [4, 4]
    assert "Memory leak" in findings[0]["message"]
    assert "unused" in findings[1]["message"]
    assert tool_run["finding_count"] == 2


def test_spotbugs_structured_findings_are_preserved_without_a_source_location(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            pass

        @staticmethod
        def json():
            return {
                "executed": True,
                "status": "completed",
                "validator": "Checkstyle + PMD + SpotBugs + javac",
                "stdout": (
                    "CODE_VALIDATION_SPOTBUGS:{\"tool\":\"spotbugs\",\"error\":null,"
                    "\"findings\":[{\"rule_id\":\"NP_NULL_ON_SOME_PATH\","
                    "\"type\":\"NP_NULL_ON_SOME_PATH\",\"message\":\"Possible null dereference\","
                    "\"priority\":1,\"severity\":\"high\",\"file\":\"unmapped/Other.java\","
                    "\"line\":null,\"class\":\"example.Other\",\"method\":\"run\"}]}\n"
                ),
                "stderr": "",
            }

    monkeypatch.setenv("QUALITY_SANDBOX_SERVICE_URL", "http://sandbox")
    monkeypatch.setattr(main.httpx, "post", lambda *_args, **_kwargs: FakeResponse())

    raw_issues, _ = main._run_language_analyzer(
        "spotbugs-result",
        {"src/Main.java": "class Main {}\n"},
        "java",
    )
    findings = main.normalize(raw_issues)

    assert len(findings) == 1
    assert findings[0].issue == "NP_NULL_ON_SOME_PATH"
    assert findings[0].tool == "spotbugs"
    assert findings[0].severity == "high"
    assert findings[0].file == "unmapped/Other.java"
    assert findings[0].line_number is None
    assert '"priority":1' in findings[0].evidence


def test_dotnet_analyzer_diagnostics_keep_rule_and_source_location(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            pass

        @staticmethod
        def json():
            return {
                "executed": True,
                "status": "completed",
                "validator": ".NET SDK Roslyn analyzers",
                "stdout": (
                    "An issue was encountered verifying workloads. For more information, run "
                    "\"dotnet workload update\".\n"
                    "Program.cs(3,5): warning CA3001: Possible SQL injection.\n"
                    "Program.cs(3,5): warning CA3001: Possible SQL injection.\n"
                    "Build succeeded.\n"
                    "1 Warning(s)\n"
                    "0 Error(s)\n"
                    "Time Elapsed 00:00:02.30\n"
                ),
                "stderr": "",
            }

    monkeypatch.setenv("QUALITY_SANDBOX_SERVICE_URL", "http://sandbox")
    monkeypatch.setattr(main.httpx, "post", lambda *_args, **_kwargs: FakeResponse())

    raw_issues, _ = main._run_language_analyzer(
        "csharp-roslyn-diagnostics",
        {"Program.cs": "class Program {}\n"},
        "csharp",
    )
    findings = main.normalize(raw_issues)

    assert len(findings) == 1
    assert findings[0].issue == "CA3001"
    assert findings[0].file == "Program.cs"
    assert findings[0].line_number == 3


def test_analyze_with_no_files_errors_gracefully():
    payload = {"project_id": "proj-2", "files": []}
    resp = client.post("/analyze", json=payload)
    assert resp.status_code == 200
    assert resp.json()["error"] == "No files supplied"


def test_analyze_rejects_path_traversal():
    payload = {
        "project_id": "proj-3",
        "files": [{"path": "../app.py", "content": "print('unsafe')"}],
        "tools": ["ruff"],
    }
    resp = client.post("/analyze", json=payload)
    assert resp.status_code == 422


def test_analyze_rejects_duplicate_normalized_file_paths():
    payload = {
        "project_id": "duplicate-files",
        "files": [
            {"path": "src/app.py", "content": "first = True\n"},
            {"path": "src\\app.py", "content": "second = True\n"},
        ],
        "tools": ["ruff"],
    }
    response = client.post("/analyze", json=payload)

    assert response.status_code == 422


def test_analyze_rejects_unknown_tool():
    payload = {
        "project_id": "proj-4",
        "files": [{"path": "app.py", "content": "print('ok')"}],
        "tools": ["unknown"],
    }
    resp = client.post("/analyze", json=payload)
    assert resp.status_code == 422


def test_analyze_keeps_partial_results_when_one_tool_fails(monkeypatch):
    from app import main

    monkeypatch.setattr(main, "run_ruff", lambda files: [{
        "source": "ruff",
        "file": "app.py",
        "line_number": 1,
        "code": "F401",
        "message": "unused import",
        "severity_hint": "medium",
    }])
    monkeypatch.setattr(main, "run_pylint", lambda files: (_ for _ in ()).throw(RuntimeError("boom")))

    payload = {
        "project_id": "proj-5",
        "files": [{"path": "app.py", "content": "import os\n"}],
        "tools": ["ruff", "pylint"],
    }
    resp = client.post("/analyze", json=payload)
    body = resp.json()
    assert resp.status_code == 200
    assert body["findings_count"] == 2
    assert body["error"] == "pylint: failed"
    assert body["quality_summary"]["tool_results"]["pylint"]["status"] == "failed"
    assert "boom" not in body["error"]


def test_analyze_marks_missing_analyzer_unavailable_without_losing_other_results(monkeypatch):
    from app import main

    monkeypatch.setattr(main, "run_ruff", lambda files: [{
        "source": "ruff", "file": "app.py", "line_number": 1,
        "code": "F401", "message": "unused import", "severity_hint": "medium",
    }])
    monkeypatch.setattr(main, "run_pylint", lambda files: (_ for _ in ()).throw(FileNotFoundError("C:/private/path/pylint.exe")))

    response = client.post("/analyze", json={
        "project_id": "missing-pylint",
        "files": [{"path": "app.py", "content": "import os\n"}],
        "tools": ["ruff", "pylint"],
    })

    body = response.json()
    assert body["quality_summary"]["tool_results"]["ruff"]["status"] == "completed"
    assert body["quality_summary"]["tool_results"]["pylint"]["status"] == "unavailable"
    assert "C:/private/path" not in body["error"]


def test_analyze_fails_quality_gate_when_all_selected_tools_are_unavailable(monkeypatch):
    from app import main

    for tool in ("ruff", "pylint", "radon"):
        monkeypatch.setattr(
            main,
            f"run_{tool}",
            lambda files: (_ for _ in ()).throw(FileNotFoundError("analyzer missing")),
        )

    response = client.post("/analyze", json={
        "project_id": "no-analyzers",
        "files": [{"path": "app.py", "content": "value = 1\n"}],
        "tools": ["ruff", "pylint", "radon"],
    })

    summary = response.json()["quality_summary"]
    assert summary["quality_gate_status"] == "FAIL"
    assert summary["quality_gate"]["status"] == "FAIL"
    assert {item["tool"] for item in summary["quality_gate"]["blocking_tools"]} == {"ruff", "pylint", "radon"}


def test_analyze_returns_quality_summary(monkeypatch):
    from app import main

    monkeypatch.setattr(main, "run_ruff", lambda files: [{
        "source": "ruff",
        "file": "app.py",
        "line_number": 1,
        "code": "F401",
        "message": "unused import",
        "severity_hint": "medium",
    }])
    response = client.post("/analyze", json={
        "project_id": "summary-test",
        "files": [{"path": "app.py", "content": "import os\n"}],
        "tools": ["ruff"],
    })

    summary = response.json()["quality_summary"]
    assert summary["files_analyzed"] == 1
    assert summary["total_findings"] == 1
    assert summary["by_tool"]["ruff"] == 1
    assert summary["by_severity"]["medium"] == 1
    assert summary["style_issues"] == 0
    assert summary["complexity_issues"] == 0
    assert summary["by_category"]["code_quality"] == 1


def test_analyze_summarizes_style_and_complexity_categories(monkeypatch):
    from app import main

    monkeypatch.setattr(main, "run_ruff", lambda files: [{
        "source": "ruff", "file": "app.py", "line_number": 1,
        "code": "C0116", "message": "Missing function or method docstring", "severity_hint": "low",
    }])
    monkeypatch.setattr(main, "run_pylint", lambda files: [])
    monkeypatch.setattr(main, "run_mypy", lambda files: [])
    monkeypatch.setattr(main, "run_radon", lambda files: [{
        "source": "radon", "file": "app.py", "line_number": 2,
        "code": "COMPLEXITY_D", "message": "'run' has cyclomatic complexity 22 (grade D)", "severity_hint": "medium",
    }])

    response = client.post("/analyze", json={
        "project_id": "category-summary",
        "files": [{"path": "app.py", "content": "def run():\n    return True\n"}],
    })

    summary = response.json()["quality_summary"]
    assert summary["style_issues"] == 1
    assert summary["complexity_issues"] == 1
    assert summary["by_category"]["code_quality"] == 2


def test_analyze_exposes_quality_gate_and_hotspots(monkeypatch):
    from app import main

    monkeypatch.setattr(main, "run_ruff", lambda files: [{
        "source": "ruff",
        "file": "app.py",
        "line_number": 8,
        "code": "F841",
        "message": "local variable 'value' is assigned to but never used",
        "severity_hint": "medium",
    }])
    monkeypatch.setattr(main, "run_pylint", lambda files: [{
        "source": "pylint",
        "file": "app.py",
        "line_number": 8,
        "code": "W0612",
        "message": "Unused variable 'value'",
        "severity_hint": "medium",
    }])
    monkeypatch.setattr(main, "run_radon", lambda files: [{
        "source": "radon",
        "file": "app.py",
        "line_number": 5,
        "code": "COMPLEXITY_E",
        "message": "'process_payment' has cyclomatic complexity 27 (grade E)",
        "severity_hint": "high",
    }])

    response = client.post("/analyze", json={
        "project_id": "gate-test",
        "files": [{"path": "app.py", "content": "def process_payment(amount):\n    value = amount * 2\n    return value\n"}],
        "tools": ["ruff", "pylint", "radon"],
    })

    summary = response.json()["quality_summary"]
    assert summary["overall_score"] >= 0
    assert summary["quality_gate_status"] in {"PASS", "FAIL"}
    assert summary["quality_gate"]["status"] == summary["quality_gate_status"]
    assert "hotspots" in summary
    assert summary["hotspots"][0]["file"] == "app.py"
    assert "per_file" in summary
    assert "tool_results" in summary
    assert summary["tool_results"]["ruff"]["status"] == "completed"


def test_analyze_deduplicates_cross_tool_unused_variable(monkeypatch):
    from app import main

    issue = {
        "file": "app.py",
        "line_number": 3,
        "message": "unused variable 'value'",
        "severity_hint": "medium",
    }
    monkeypatch.setattr(main, "run_ruff", lambda files: [{**issue, "source": "ruff", "code": "F841"}])
    monkeypatch.setattr(main, "run_pylint", lambda files: [{**issue, "source": "pylint", "code": "W0612"}])

    response = client.post("/analyze", json={
        "project_id": "dedup-test",
        "files": [{"path": "app.py", "content": "def run():\n    value = 1\n"}],
        "tools": ["ruff", "pylint"],
    })

    findings = response.json()["findings"]
    assert len(findings) == 1
    assert findings[0]["detected_by_tools"] == ["ruff", "pylint"]
