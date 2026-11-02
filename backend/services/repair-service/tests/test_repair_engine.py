import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import httpx
from pydantic import ValidationError

from app.models import CodeFile, Finding, RepairRequest
from app.services import repair_engine
from app.services import validation_service
from app.main import app
from fastapi.testclient import TestClient


def test_repair_file_returns_unchanged_code_without_findings():
    repair, unresolved = asyncio.run(repair_engine.repair_file("main.py", "print(1)\n", []))

    assert repair.changed is False
    assert repair.regenerated_code == "print(1)\n"
    assert unresolved == []


def test_repair_endpoint_returns_project_level_repair_response():
    response = TestClient(app).post("/repair", json={
        "project_id": "repair-contract-check",
        "files": [{"path": "main.py", "content": "print(1)\n"}],
        "findings": [],
    })

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert response.json()["repair_status"] == "not_run"
    assert response.json()["repairs"][0]["path"] == "main.py"
    assert response.json()["repairs"][0]["changed"] is False


@pytest.mark.parametrize(
    ("language", "files"),
    [
        ("python", [CodeFile(path="main.py", content="print('main')\n")]),
        ("java", [CodeFile(path="Main.java", content="class Main {}\n")]),
    ],
)
def test_repair_project_skips_ollama_when_there_are_no_findings(monkeypatch, language, files):
    async def unexpected_generation(_prompt: str) -> str:
        pytest.fail("Repair must not call Ollama when the project has no findings")

    monkeypatch.setattr(repair_engine, "generate_code", unexpected_generation)
    request = RepairRequest(
        language=language,
        files=files,
        findings=[],
    )

    result = asyncio.run(repair_engine.repair_project(request))

    assert result.status == "completed"
    assert result.repair_status == "not_run"
    assert result.outcome == "no_change"
    assert result.validation_status == "not_run_no_findings"
    assert [repair.regenerated_code for repair in result.repairs] == [
        file.content for file in files
    ]


def test_repair_project_skips_files_without_findings(monkeypatch):
    generated_paths = []

    async def generated_response(prompt: str) -> str:
        generated_paths.append(prompt.split("File: ", 1)[1].splitlines()[0])
        return json.dumps({"code": "print('repaired')\n"})

    monkeypatch.setattr(repair_engine, "generate_code", generated_response)
    monkeypatch.setattr(repair_engine, "validate_repair", lambda *_args, **_kwargs: (True, "checked"))
    request = RepairRequest(
        language="python",
        files=[
            CodeFile(path="main.py", content="print('original')\n"),
            CodeFile(path="helper.py", content="def helper():\n    return 1\n"),
        ],
        findings=[Finding(finding_id="f-1", file="main.py", message="Change output")],
    )

    result = asyncio.run(repair_engine.repair_project(request))

    assert generated_paths == ["main.py"]
    assert result.repairs[1].changed is False
    assert result.repairs[1].regenerated_code == "def helper():\n    return 1\n"


def test_repair_endpoint_accepts_a_language_matched_multi_file_project():
    response = TestClient(app).post("/repair", json={
        "project_id": "typescript-project-contract",
        "language": "typescript",
        "files": [
            {"path": "src/main.ts", "content": "import { value } from './helper';\nconsole.log(value);\n"},
            {"path": "src/helper.ts", "content": "export const value = 1;\n"},
        ],
        "findings": [],
        "repair_feedback": "",
    })

    assert response.status_code == 200
    assert response.json()["repair_status"] == "not_run"
    assert {repair["path"] for repair in response.json()["repairs"]} == {
        "src/main.ts",
        "src/helper.ts",
    }


@pytest.mark.parametrize("invalid_field", ["language", "finding_path", "finding_line"])
def test_repair_endpoint_rejects_invalid_request_contract(invalid_field):
    payload = {
        "project_id": "invalid-repair-contract",
        "language": "python",
        "files": [{"path": "main.py", "content": "print(1)\n"}],
        "findings": [{
            "finding_id": "f-1",
            "file": "main.py",
            "line": 1,
            "message": "Test finding",
        }],
        "repair_feedback": "",
    }
    if invalid_field == "language":
        payload["language"] = "typescript"
    elif invalid_field == "finding_path":
        payload["findings"][0]["file"] = "unknown"
    else:
        payload["findings"][0]["line"] = "not-a-line"

    response = TestClient(app).post("/repair", json=payload)

    assert response.status_code == 422


def test_repair_health_reports_missing_model_as_provider_unavailable(monkeypatch):
    async def missing_model():
        return False, "The configured Ollama model is not installed."

    monkeypatch.setattr("app.main.model_status", missing_model)

    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["provider_ready"] is False
    assert "not installed" in response.json()["provider_error"]


def test_repair_file_accepts_validated_candidate(monkeypatch):
    async def generated_response(prompt: str) -> str:
        assert "resolved_finding_ids" in prompt
        return json.dumps({
            "code": "def add(a, b):\n    return a + b\n",
            "explanation": "Use addition instead of subtraction.",
            "resolved_finding_indexes": [0],
        })

    monkeypatch.setattr(repair_engine, "generate_code", generated_response)
    monkeypatch.setattr(repair_engine, "validate_repair", lambda *_args, **_kwargs: (True, "validated"))
    finding = Finding(file="main.py", line=2, type="logic", message="Wrong operator")

    repair, unresolved = asyncio.run(repair_engine.repair_file(
        "main.py", "def add(a, b):\n    return a - b\n", [finding]
    ))

    assert repair.changed is True
    assert repair.regenerated_code == "def add(a, b):\n    return a + b\n"
    assert repair.resolved_finding_ids == []
    assert unresolved == [finding]


@pytest.mark.parametrize(
    "language,filename",
    [
        ("python", "main.py"),
        ("java", "Main.java"),
        ("javascript", "main.js"),
        ("typescript", "main.ts"),
        ("c", "main.c"),
        ("cpp", "main.cpp"),
        ("csharp", "main.cs"),
        ("go", "main.go"),
    ],
)
def test_repair_prompt_uses_the_submitted_language(monkeypatch, language, filename):
    async def generated_response(prompt: str) -> str:
        assert f"Language: {language}" in prompt
        return json.dumps({
            "code": "updated source\n",
            "explanation": "Applied the requested correction.",
            "resolved_finding_ids": ["finding-1"],
        })

    monkeypatch.setattr(repair_engine, "generate_code", generated_response)
    monkeypatch.setattr(repair_engine, "validate_repair", lambda *_args, **_kwargs: (True, "validated"))
    finding = Finding(finding_id="finding-1", file=filename, message="Correct this issue")

    repair, unresolved = asyncio.run(repair_engine.repair_file(
        filename, "original source\n", [finding], language=language
    ))

    assert repair.changed is True
    assert repair.resolved_finding_ids == []
    assert unresolved == [finding]


def test_java_repair_prompt_includes_filename_and_compiler_constraints(monkeypatch):
    async def generated_response(prompt: str) -> str:
        assert "Language: java" in prompt
        assert "without Markdown code fences" in prompt
        assert "Since the submitted file path cannot be renamed" in prompt
        assert "remove the public modifier only when that is safe" in prompt
        assert "never rename the type to an invalid Java identifier" in prompt
        assert "every referenced Java identifier is declared, imported from the correct package, and in scope" in prompt
        return json.dumps({"code": "```java\nclass java_test { }\n```\n"})

    monkeypatch.setattr(repair_engine, "generate_code", generated_response)
    monkeypatch.setattr(repair_engine, "validate_repair", lambda *_args, **_kwargs: (True, "validated"))
    finding = Finding(file="java_test.java", message="Fix the compilation errors")

    repair, _ = asyncio.run(repair_engine.repair_file(
        "java_test.java",
        "public class JavaTest { }\n",
        [finding],
        language="java",
    ))

    assert repair.changed is True
    assert repair.regenerated_code == "class java_test { }\n"


def test_java_standalone_public_type_filename_mismatch_is_corrected_after_compiler_feedback(monkeypatch):
    async def generated_response(_prompt: str) -> str:
        return json.dumps({
            "code": "public class JavaTest { public static void main(String[] args) {} }\n",
            "explanation": "Applied the requested repair.",
        })

    validations = []

    def validate(_original, candidate, _language, _path, **_kwargs):
        validations.append(candidate)
        if "public class JavaTest" in candidate:
            return False, (
                "java_test.java: error: class JavaTest is public, should be declared "
                "in a file named JavaTest.java"
            )
        return True, "javac validation passed."

    monkeypatch.setattr(repair_engine, "generate_code", generated_response)
    monkeypatch.setattr(repair_engine, "validate_repair", validate)
    finding = Finding(file="java_test.java", message="Fix the Java compiler error")

    repair, _ = asyncio.run(repair_engine.repair_file(
        "java_test.java",
        "public class JavaTest { public static void main(String[] args) { int value = ; } }\n",
        [finding],
        language="java",
    ))

    assert repair.changed is True
    assert repair.regenerated_code.startswith("class JavaTest")
    assert len(validations) == 2
    assert "Made the default-package Java type package-private" in repair.explanation


def test_java_filename_mismatch_fix_is_not_applied_to_packaged_projects():
    candidate = "package demo;\npublic class JavaTest {}\n"

    corrected = repair_engine._fix_default_package_java_filename_mismatch(
        "java_test.java",
        candidate,
        {"java_test.java": candidate},
        "error: class JavaTest is public, should be declared in a file named JavaTest.java",
    )

    assert corrected is None


def test_java_filename_mismatch_fix_preserves_visibility_for_packaged_projects():
    candidate = "package demo;\npublic class JavaTest {}\n"

    corrected = repair_engine._fix_default_package_java_filename_mismatch(
        "java_test.java",
        candidate,
        {
            "java_test.java": candidate,
            "demo/Helper.java": "package demo;\nclass Helper {}\n",
        },
        "error: class JavaTest is public, should be declared in a file named JavaTest.java",
    )

    assert corrected is None


def test_java_filename_mismatch_fix_supports_default_package_projects():
    candidate = "public class JavaTest {}\n"

    corrected = repair_engine._fix_default_package_java_filename_mismatch(
        "java_test.java",
        candidate,
        {
            "java_test.java": candidate,
            "Helper.java": "class Helper {}\n",
        },
        "error: class JavaTest is public, should be declared in a file named JavaTest.java",
    )

    assert corrected == "class JavaTest {}\n"


@pytest.mark.parametrize(
    ("language", "filename", "guidance"),
    [
        ("python", "main.py", "For F841"),
        ("c", "main.c", "remove the complete declaration"),
        ("cpp", "main.cpp", "prefer RAII"),
        ("csharp", "main.cs", "For CS0219"),
    ],
)
def test_repair_prompt_includes_unused_local_guidance(monkeypatch, language, filename, guidance):
    async def generated_response(prompt: str) -> str:
        assert guidance in prompt
        return json.dumps({"code": "updated source\n"})

    monkeypatch.setattr(repair_engine, "generate_code", generated_response)
    monkeypatch.setattr(repair_engine, "validate_repair", lambda *_args, **_kwargs: (True, "validated"))
    finding = Finding(file=filename, message="Unused local variable")

    repair, _ = asyncio.run(repair_engine.repair_file(
        filename,
        "original source\n",
        [finding],
        language=language,
    ))

    assert repair.changed is True


def test_python_candidate_with_new_f841_is_rejected(monkeypatch):
    async def generated_response(_prompt: str) -> str:
        return json.dumps({
            "code": "def calculate():\n    unused = 42\n    return 1\n",
            "explanation": "Applied repair.",
        })

    monkeypatch.setattr(repair_engine, "generate_code", generated_response)
    monkeypatch.setattr(repair_engine, "validate_repair", validation_service.validate_repair)
    original = "def calculate():\n    return 0\n"
    finding = Finding(
        finding_id="fix-result",
        file="main.py",
        rule_id="CUSTOM",
        message="Return the correct result.",
    )

    repair, unresolved = asyncio.run(repair_engine.repair_file("main.py", original, [finding]))

    assert repair.changed is False
    assert repair.regenerated_code == original
    assert "F841" in repair.explanation
    assert repair.candidate_code == "def calculate():\n    unused = 42\n    return 1\n"
    assert unresolved == [finding]


@pytest.mark.parametrize(
    "language,filename,original,candidate",
    [
        ("python", "main.py", "print('old')\n", "print('new')\n"),
        ("java", "Main.java", "class Main { }\n", "class Main { int value = 1; }\n"),
        ("javascript", "main.js", "console.log('old');\n", "console.log('new');\n"),
        ("typescript", "main.ts", "console.log('old');\n", "console.log('new');\n"),
        ("c", "main.c", "int main(void) { return 0; }\n", "int main(void) { return 1; }\n"),
        ("cpp", "main.cpp", "int main() { return 0; }\n", "int main() { return 1; }\n"),
        ("csharp", "main.cs", "class Main { }\n", "class Main { int value = 1; }\n"),
        ("go", "main.go", "package main\nfunc main() {}\n", "package main\nfunc main() { println(1) }\n"),
    ],
)
def test_repair_keeps_changed_candidate_when_model_omits_finding_ids(
    monkeypatch, language, filename, original, candidate
):
    async def generated_response(_prompt: str) -> str:
        return json.dumps({"code": candidate, "explanation": "Applied a targeted change."})

    monkeypatch.setattr(repair_engine, "generate_code", generated_response)
    monkeypatch.setattr(repair_engine, "validate_repair", lambda *_args, **_kwargs: (True, "validated"))
    finding = Finding(finding_id="finding-1", file=filename, message="Fix this issue")

    repair, unresolved = asyncio.run(repair_engine.repair_file(
        filename, original, [finding], language=language
    ))

    assert repair.changed is True
    assert repair.regenerated_code == candidate
    assert repair.resolved_finding_ids == []
    assert unresolved == [finding]


def test_repair_project_returns_partial_candidate_when_model_omits_finding_ids(monkeypatch):
    async def generated_response(_prompt: str) -> str:
        return json.dumps({
            "code": "int main(void) { return 1; }\n",
            "explanation": "Changed the return value.",
        })

    monkeypatch.setattr(repair_engine, "generate_code", generated_response)
    monkeypatch.setattr(repair_engine, "validate_repair", lambda *_args, **_kwargs: (True, "validated"))
    finding = Finding(
        finding_id="finding-1",
        file="main.c",
        rule_id="LANGUAGE_ANALYZER",
        message="Correct the return value.",
    )
    request = RepairRequest(
        project_id="missing-repair-ids",
        language="c",
        files=[CodeFile(path="main.c", content="int main(void) { return 0; }\n")],
        findings=[finding],
    )

    result = asyncio.run(repair_engine.repair_project(request))

    assert result.status == "partial"
    assert result.outcome == "partially_applied"
    assert result.changed_files == ["main.c"]
    assert result.repairs[0].resolved_finding_ids == []
    assert result.unresolved_findings == [finding]
    assert result.repair_status == "needs_review"


def test_repair_project_revalidates_all_candidates_and_rolls_back_project_regression(monkeypatch):
    async def generated_response(prompt: str) -> str:
        path = prompt.split("File: ", 1)[1].splitlines()[0]
        if path == "main.py":
            return json.dumps({"code": "from helper import value\nprint(value)\n"})
        return json.dumps({"code": "value = 1\n"})

    validations = []

    def validate(_original, _candidate, _language, path, *, candidate_files, **_kwargs):
        validations.append((path, dict(candidate_files)))
        if len(validations) == 3 and candidate_files["helper.py"] == "value = 1\n":
            return False, "project regression in main.py"
        return True, "project validation passed"

    monkeypatch.setattr(repair_engine, "generate_code", generated_response)
    monkeypatch.setattr(repair_engine, "validate_repair", validate)
    request_findings = [
        Finding(finding_id="main-finding", file="main.py", message="Fix main.py"),
        Finding(finding_id="helper-finding", file="helper.py", message="Fix helper.py"),
    ]
    request = RepairRequest(
        language="python",
        files=[
            CodeFile(path="main.py", content="print(0)\n"),
            CodeFile(path="helper.py", content="value = 0\n"),
        ],
        findings=request_findings,
    )

    result = asyncio.run(repair_engine.repair_project(request))

    assert len(validations) == 3
    assert validations[2][1] == {
        "main.py": "from helper import value\nprint(value)\n",
        "helper.py": "value = 1\n",
    }
    assert result.repairs[0].changed is False
    assert result.repairs[0].regenerated_code == "print(0)\n"
    assert "final project validation failed" in result.repairs[0].explanation
    assert result.repairs[1].changed is False
    assert result.repairs[1].regenerated_code == "value = 0\n"
    assert result.changed_files == []
    assert result.unresolved_findings == request_findings


def test_repair_file_preserves_original_final_newline(monkeypatch):
    validated_candidates = []

    async def generated_response(_prompt: str) -> str:
        return json.dumps({"code": "print('fixed')", "resolved_finding_ids": ["finding-1"]})

    def validate_candidate(_original: str, candidate: str, _language: str, _path: str, **_kwargs) -> tuple[bool, str]:
        validated_candidates.append(candidate)
        return True, "validated"

    monkeypatch.setattr(repair_engine, "generate_code", generated_response)
    monkeypatch.setattr(repair_engine, "validate_repair", validate_candidate)
    monkeypatch.setattr(validation_service, "_ruff_diagnostics", lambda _code: [])
    finding = Finding(finding_id="finding-1", file="main.py", rule_id="LOGIC", message="Fix this issue")

    repair, unresolved = asyncio.run(repair_engine.repair_file(
        "main.py", "print('original')\n", [finding]
    ))

    assert validated_candidates == ["print('fixed')\n"]
    assert repair.regenerated_code == "print('fixed')\n"
    assert repair.resolved_finding_ids == []
    assert unresolved == [finding]


def test_repair_file_adds_final_newline_to_candidate(monkeypatch):
    validated_candidates = []

    async def generated_response(_prompt: str) -> str:
        return json.dumps({"code": "print('fixed')", "resolved_finding_ids": ["finding-1"]})

    def validate_candidate(_original: str, candidate: str, _language: str, _path: str, **_kwargs) -> tuple[bool, str]:
        validated_candidates.append(candidate)
        return True, "validated"

    monkeypatch.setattr(repair_engine, "generate_code", generated_response)
    monkeypatch.setattr(repair_engine, "validate_repair", validate_candidate)
    monkeypatch.setattr(validation_service, "_ruff_diagnostics", lambda _code: [])
    finding = Finding(finding_id="finding-1", file="main.py", rule_id="LOGIC", message="Fix this issue")

    repair, unresolved = asyncio.run(repair_engine.repair_file(
        "main.py", "print('original')", [finding]
    ))

    assert validated_candidates == ["print('fixed')\n"]
    assert repair.regenerated_code == "print('fixed')\n"
    assert repair.resolved_finding_ids == []
    assert unresolved == [finding]


@pytest.mark.parametrize("reference", [
    {"resolved_finding_indexes": [4]},
    {"resolved_finding_ids": ["f-1"], "resolved_finding_indexes": [1]},
])
def test_repair_rejects_invalid_finding_references_without_changing_source(monkeypatch, reference):
    async def generated_response(_prompt: str) -> str:
        return json.dumps({"code": "print('candidate')\n", **reference})

    monkeypatch.setattr(repair_engine, "generate_code", generated_response)
    monkeypatch.setattr(repair_engine, "validate_repair", lambda *_args, **_kwargs: (True, "checked"))
    original = "print('original')\n"
    finding = Finding(finding_id="f-1", file="main.py", message="Change output")

    repair, unresolved = asyncio.run(repair_engine.repair_file("main.py", original, [finding]))

    assert repair.changed is False
    assert repair.regenerated_code == original
    assert unresolved == [finding]
    assert "finding" in repair.explanation.lower()


def test_repair_validates_candidate_when_model_claims_unknown_finding_id(monkeypatch):
    async def generated_response(_prompt: str) -> str:
        return json.dumps({
            "code": "print('candidate')\n",
            "resolved_finding_ids": ["not-a-real-finding"],
        })

    monkeypatch.setattr(repair_engine, "generate_code", generated_response)
    monkeypatch.setattr(repair_engine, "validate_repair", lambda *_args, **_kwargs: (True, "checked"))
    original = "print('original')\n"
    finding = Finding(finding_id="f-1", file="main.py", message="Change output")

    repair, unresolved = asyncio.run(repair_engine.repair_file("main.py", original, [finding]))

    assert repair.changed is True
    assert repair.regenerated_code == "print('candidate')\n"
    assert repair.resolved_finding_ids == []
    assert unresolved == [finding]


def test_repair_no_change_keeps_finding_unresolved_and_reports_no_change(monkeypatch):
    async def generated_response(_prompt: str) -> str:
        return json.dumps({"code": "print('original')\n", "resolved_finding_ids": ["f-1"]})

    monkeypatch.setattr(repair_engine, "generate_code", generated_response)
    monkeypatch.setattr(repair_engine, "validate_repair", lambda *_args, **_kwargs: (True, "checked"))
    request = RepairRequest(
        project_id="no-change",
        files=[CodeFile(path="main.py", content="print('original')\n")],
        findings=[Finding(finding_id="f-1", file="main.py", message="Change output")],
    )

    result = asyncio.run(repair_engine.repair_project(request))

    assert result.status == "failed"
    assert result.outcome == "no_change"
    assert result.changed_files == []
    assert result.repairs[0].changed is False
    assert result.unresolved_findings == request.findings
    assert result.repair_status == "needs_review"


@pytest.mark.parametrize("source", [
    'const unused: number = 1;\nconsole.log("ok");\n',
    'const unused: number = 1;\nconsole.log("ok");',
])
def test_repair_stops_when_model_returns_unchanged_source(monkeypatch, source):
    prompts = []

    async def generated_response(prompt: str) -> str:
        prompts.append(prompt)
        return json.dumps({
            "code": source,
            "resolved_finding_ids": ["unused-variable"],
        })

    def unexpected_validation(*_args, **_kwargs):
        pytest.fail("Unchanged source should stop before candidate validation")

    monkeypatch.setattr(repair_engine, "generate_code", generated_response)
    monkeypatch.setattr(repair_engine, "validate_repair", unexpected_validation)
    finding = Finding(
        finding_id="unused-variable",
        file="main.ts",
        rule_id="LANGUAGE_ANALYZER",
        message="'unused' is declared but its value is never read.",
        tool="ESLint TypeScript",
    )

    repair, unresolved = asyncio.run(repair_engine.repair_file(
        "main.ts",
        source,
        [finding],
        language="typescript",
    ))

    assert len(prompts) == 1
    assert repair.changed is False
    assert repair.regenerated_code == source
    assert repair.explanation == "Repair candidate did not change the source; stopped with findings unresolved."
    assert repair.candidate_code == source
    assert unresolved == [finding]


def test_python_no_change_candidate_with_nonbreaking_space_stops_before_validation(monkeypatch):
    source = "import os\u00a0 # Intentionally unused: should trigger an F401 finding.\n"

    async def unchanged_response(_prompt: str) -> str:
        return json.dumps({"code": source, "resolved_finding_ids": ["f-1"]})

    def unexpected_validation(*_args, **_kwargs):
        pytest.fail("Unchanged source should stop before candidate validation")

    monkeypatch.setattr(repair_engine, "generate_code", unchanged_response)
    monkeypatch.setattr(repair_engine, "validate_repair", unexpected_validation)
    monkeypatch.setattr(
        repair_engine.shutil,
        "which",
        lambda _tool: pytest.fail("No-op candidates should stop before invoking Ruff"),
    )
    finding = Finding(
        finding_id="f-1",
        file="main.py",
        rule_id="F401",
        message="`os` imported but unused",
    )

    repair, unresolved = asyncio.run(repair_engine.repair_file("main.py", source, [finding]))

    assert repair.changed is False
    assert repair.regenerated_code == source
    assert repair.explanation == "Repair candidate did not change the source; stopped with findings unresolved."
    assert unresolved == [finding]


def test_repair_does_not_retry_candidate_rejected_for_new_ruff_finding(monkeypatch):
    prompts = []

    async def generated_response(prompt: str) -> str:
        prompts.append(prompt)
        return json.dumps({
            "code": 'import os\nprint("fixed")\n',
            "resolved_finding_ids": [],
        })

    monkeypatch.setattr(repair_engine, "generate_code", generated_response)
    monkeypatch.setattr(
        repair_engine,
        "validate_repair",
        lambda *_args, **_kwargs: (
            False,
            "Repair introduced Ruff findings: F401 at line 1, column 8",
        ),
    )
    finding = Finding(
        finding_id="fix-output",
        file="main.py",
        rule_id="CUSTOM",
        message="Correct the output.",
    )

    repair, unresolved = asyncio.run(repair_engine.repair_file(
        "main.py",
        'print("original")\n',
        [finding],
    ))

    assert len(prompts) == 1
    assert repair.changed is False
    assert repair.regenerated_code == 'print("original")\n'
    assert "rejected" in repair.explanation.lower()
    assert repair.candidate_code == 'import os\nprint("fixed")\n'
    assert unresolved == [finding]


def test_repair_file_rejects_candidate_that_fails_validation(monkeypatch):
    async def generated_response(_prompt: str) -> str:
        return json.dumps({"code": "def add(:\n", "resolved_finding_indexes": [0]})

    monkeypatch.setattr(repair_engine, "generate_code", generated_response)
    monkeypatch.setattr(repair_engine, "validate_repair", lambda *_args, **_kwargs: (False, "invalid syntax"))
    finding = Finding(file="main.py", line=1, message="Syntax issue")

    repair, unresolved = asyncio.run(repair_engine.repair_file("main.py", "def add(): pass\n", [finding]))

    assert repair.changed is False
    assert repair.regenerated_code == "def add(): pass\n"
    assert "rejected" in repair.explanation
    assert repair.candidate_code == "def add(:\n"
    assert unresolved == [finding]


def test_repair_file_uses_ruff_when_model_candidate_leaves_unused_import(monkeypatch):
    original = 'import os\n\nprint("ready")\n'

    async def incomplete_response(_prompt: str) -> str:
        return json.dumps({
            "code": 'import os\nprint("ready")\n',
            "explanation": "Removed the unused import.",
            "resolved_finding_ids": [],
        })

    def autofix(command, **_kwargs):
        candidate = Path(command[-1])
        candidate.write_text(candidate.read_text(encoding="utf-8").replace("import os\n", ""), encoding="utf-8")
        return SimpleNamespace(returncode=1, stdout="", stderr="")

    def diagnostics(code: str):
        if "import os" in code:
            return [{"code": "F401", "message": "`os` imported but unused"}]
        return []

    monkeypatch.setattr(repair_engine, "generate_code", incomplete_response)
    monkeypatch.setattr(repair_engine.shutil, "which", lambda _tool: "ruff")
    monkeypatch.setattr(repair_engine.subprocess, "run", autofix)
    monkeypatch.setattr(validation_service, "_ruff_diagnostics", diagnostics)
    finding = Finding(
        finding_id="unused-os-import",
        file="main.py",
        rule_id="F401",
        message="`os` imported but unused",
    )

    repair, unresolved = asyncio.run(repair_engine.repair_file("main.py", original, [finding]))

    assert repair.changed is True
    assert repair.regenerated_code == 'print("ready")\n'
    assert repair.resolved_finding_ids == ["unused-os-import"]
    assert unresolved == []


def test_repair_file_does_not_accept_model_claim_when_ruff_finding_remains(monkeypatch):
    async def generated_response(_prompt: str) -> str:
        return json.dumps({
            "code": 'import os\n\nprint("changed")\n',
            "resolved_finding_ids": ["unused-os-import"],
        })

    monkeypatch.setattr(repair_engine, "generate_code", generated_response)
    monkeypatch.setattr(
        validation_service,
        "_ruff_diagnostics",
        lambda _code: [{"code": "F401", "message": "`os` imported but unused"}],
    )
    finding = Finding(
        finding_id="unused-os-import",
        file="main.py",
        rule_id="F401",
        message="`os` imported but unused",
    )

    repair, unresolved = asyncio.run(
        repair_engine.repair_file("main.py", 'import os\n\nprint("ready")\n', [finding])
    )

    assert repair.changed is True
    assert repair.resolved_finding_ids == []
    assert unresolved == [finding]


def test_repair_keeps_f401_unresolved_when_candidate_has_ambiguous_f401(monkeypatch):
    async def generated_response(_prompt: str) -> str:
        return json.dumps({"code": "import sys\nprint('changed')\n", "resolved_finding_ids": ["unused-os"]})

    monkeypatch.setattr(repair_engine, "generate_code", generated_response)
    monkeypatch.setattr(repair_engine, "validate_repair", lambda *_args, **_kwargs: (True, "checked"))
    monkeypatch.setattr(
        validation_service,
        "_ruff_diagnostics",
        lambda _code: [{"code": "F401", "message": "`sys` imported but unused"}],
    )
    finding = Finding(
        finding_id="unused-os",
        file="main.py",
        rule_id="F401",
        message="`os` imported but unused",
    )

    repair, unresolved = asyncio.run(
        repair_engine.repair_file("main.py", "import os\nprint('original')\n", [finding])
    )

    assert repair.resolved_finding_ids == []
    assert unresolved == [finding]


def test_repair_independently_verifies_resolved_python_f841(monkeypatch):
    original = "def calculate():\n    unused = 42\n    return 0\n"
    candidate = "def calculate():\n    return 1\n"

    async def generated_response(_prompt: str) -> str:
        return json.dumps({"code": candidate, "resolved_finding_ids": ["unused-local"]})

    def diagnostics(code: str):
        if "unused = 42" in code:
            return [{
                "code": "F841",
                "message": "Local variable `unused` is assigned to but never used",
            }]
        return []

    monkeypatch.setattr(repair_engine, "generate_code", generated_response)
    monkeypatch.setattr(repair_engine, "validate_repair", lambda *_args, **_kwargs: (True, "checked"))
    monkeypatch.setattr(validation_service, "_ruff_diagnostics", diagnostics)
    finding = Finding(
        finding_id="unused-local",
        file="main.py",
        rule_id="F841",
        message="Local variable `unused` is assigned to but never used",
    )

    repair, unresolved = asyncio.run(
        repair_engine.repair_file("main.py", original, [finding])
    )

    assert repair.resolved_finding_ids == ["unused-local"]
    assert unresolved == []


def test_repair_keeps_python_f841_unresolved_when_candidate_retains_it(monkeypatch):
    original = "def calculate():\n    unused = 42\n    return 0\n"
    candidate = "def calculate():\n    unused = 42\n    return 1\n"
    diagnostic = {
        "code": "F841",
        "message": "Local variable `unused` is assigned to but never used",
    }

    async def generated_response(_prompt: str) -> str:
        return json.dumps({"code": candidate, "resolved_finding_ids": ["unused-local"]})

    monkeypatch.setattr(repair_engine, "generate_code", generated_response)
    monkeypatch.setattr(repair_engine, "validate_repair", lambda *_args, **_kwargs: (True, "checked"))
    monkeypatch.setattr(validation_service, "_ruff_diagnostics", lambda _code: [diagnostic])
    finding = Finding(
        finding_id="unused-local",
        file="main.py",
        rule_id="F841",
        message=diagnostic["message"],
    )

    repair, unresolved = asyncio.run(
        repair_engine.repair_file("main.py", original, [finding])
    )

    assert repair.resolved_finding_ids == []
    assert unresolved == [finding]


def test_python_module_docstring_autofix_preserves_script_header():
    finding = Finding(
        finding_id="module-docstring",
        file="main.py",
        rule_id="C0114",
        message="Missing module docstring",
    )
    source = "#!/usr/bin/env python\n# -*- coding: utf-8 -*-\ndef main():\n    return 0\n"

    repaired = repair_engine._safe_autofix("main.py", source, [finding], "python")

    assert repaired == (
        "#!/usr/bin/env python\n"
        "# -*- coding: utf-8 -*-\n"
        '"""Module documentation."""\n\n\n'
        "def main():\n"
        "    return 0\n"
    )


def test_js_repair_does_not_trust_claims_for_api_token_and_load_data(monkeypatch):
    async def generated_response(_prompt: str) -> str:
        return json.dumps({
            "code": "const API_TOKEN = 'token';\nasync function loadData() { return 2; }\n",
            "resolved_finding_ids": ["unused-token", "unused-loader"],
        })

    monkeypatch.setattr(repair_engine, "generate_code", generated_response)
    monkeypatch.setattr(repair_engine, "validate_repair", lambda *_args, **_kwargs: (True, "node syntax passed"))
    findings = [
        Finding(
            finding_id="unused-token",
            file="main.js",
            rule_id="LANGUAGE_ANALYZER",
            type="code_quality",
            message="'API_TOKEN' is assigned a value but never used.",
            tool="ESLint",
            evidence="no-unused-vars",
        ),
        Finding(
            finding_id="unused-loader",
            file="main.js",
            rule_id="LANGUAGE_ANALYZER",
            type="code_quality",
            message="'loadData' is defined but never used.",
            tool="ESLint",
            evidence="no-unused-vars",
        ),
    ]

    repair, unresolved = asyncio.run(repair_engine.repair_file(
        "main.js",
        "const API_TOKEN = 'token';\nasync function loadData() { return 1; }\n",
        findings,
        language="javascript",
    ))

    assert repair.changed is True
    assert repair.resolved_finding_ids == []
    assert unresolved == findings


@pytest.mark.parametrize(
    ("language", "filename"),
    [
        ("python", "main.py"),
        ("java", "Main.java"),
        ("javascript", "main.js"),
        ("typescript", "main.ts"),
        ("c", "main.c"),
        ("cpp", "main.cpp"),
        ("csharp", "Program.cs"),
        ("go", "main.go"),
    ],
)
def test_repair_project_keeps_unverified_findings_and_multi_finding_context(
    monkeypatch, language, filename
):
    seen_prompts = []

    async def generated_response(prompt: str) -> str:
        seen_prompts.append(prompt)
        return json.dumps({
            "code": "candidate source\n",
            "explanation": "Applied focused changes.",
            "resolved_finding_ids": ["finding-a", "finding-b"],
        })

    monkeypatch.setattr(repair_engine, "generate_code", generated_response)
    monkeypatch.setattr(repair_engine, "validate_repair", lambda *_args, **_kwargs: (True, "syntax passed"))
    findings = [
        Finding(finding_id="finding-a", file=filename, message="First diagnostic", evidence="RULE_A"),
        Finding(finding_id="finding-b", file=filename, message="Second diagnostic", evidence="RULE_B"),
    ]
    request = RepairRequest(
        language=language,
        files=[
            CodeFile(path=filename, content="original source\n"),
            CodeFile(path=f"helper.{filename.rsplit('.', 1)[1]}", content="related source\n"),
        ],
        findings=findings,
    )

    result = asyncio.run(repair_engine.repair_project(request))

    assert "First diagnostic" in seen_prompts[0]
    assert "Second diagnostic" in seen_prompts[0]
    assert "related source" in seen_prompts[0]
    assert result.unresolved_findings == findings
    assert result.repair_status == "needs_review"


@pytest.mark.parametrize(
    ("language", "filename"),
    [
        ("python", "main.py"),
        ("java", "Main.java"),
        ("javascript", "main.js"),
        ("typescript", "main.ts"),
        ("c", "main.c"),
        ("cpp", "main.cpp"),
        ("csharp", "Program.cs"),
        ("go", "main.go"),
    ],
)
def test_repair_rejects_syntax_or_compilation_failure_for_every_language(
    monkeypatch, language, filename
):
    async def generated_response(_prompt: str) -> str:
        return json.dumps({"code": "malformed candidate\n"})

    monkeypatch.setattr(repair_engine, "generate_code", generated_response)
    monkeypatch.setattr(repair_engine, "validate_repair", lambda *_args, **_kwargs: (False, "syntax or compiler error"))
    finding = Finding(file=filename, message="Fix the diagnostic")

    repair, unresolved = asyncio.run(repair_engine.repair_file(
        filename, "original source\n", [finding], language=language
    ))

    assert repair.changed is False
    assert repair.regenerated_code == "original source\n"
    assert repair.candidate_code == "malformed candidate\n"
    assert unresolved == [finding]


def test_repair_file_reports_ollama_connection_failure(monkeypatch):
    async def unavailable(_prompt: str) -> str:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(repair_engine, "generate_code", unavailable)
    finding = Finding(file="main.py", line=1, message="Fix this issue")

    repair, unresolved = asyncio.run(repair_engine.repair_file("main.py", "print(1)\n", [finding]))

    assert repair.changed is False
    assert repair.explanation == "Could not connect to the configured Ollama endpoint."
    assert unresolved == [finding]


@pytest.mark.parametrize(
    ("response", "expected_reason"),
    [
        ("", "Ollama returned a response that was not valid JSON."),
        ("not json", "Ollama returned a response that was not valid JSON."),
        (json.dumps({"explanation": "No code field"}), "Model response is missing corrected code"),
    ],
)
def test_repair_reports_empty_or_malformed_model_responses(
    monkeypatch, response, expected_reason
):
    async def malformed(_prompt: str) -> str:
        return response

    monkeypatch.setattr(repair_engine, "generate_code", malformed)
    finding = Finding(file="main.js", message="Fix this issue")

    repair, unresolved = asyncio.run(repair_engine.repair_file(
        "main.js", "const value = 1;\n", [finding], language="javascript"
    ))

    assert repair.changed is False
    assert expected_reason in repair.explanation
    assert unresolved == [finding]


def test_repair_does_not_verify_python_finding_when_ruff_is_unavailable(monkeypatch):
    async def generated_response(_prompt: str) -> str:
        return json.dumps({
            "code": 'print("ready")\n',
            "resolved_finding_ids": ["unused-os-import"],
        })

    monkeypatch.setattr(repair_engine, "generate_code", generated_response)
    monkeypatch.setattr(repair_engine.shutil, "which", lambda _tool: None)
    finding = Finding(
        finding_id="unused-os-import",
        file="main.py",
        rule_id="F401",
        message="`os` imported but unused",
    )

    repair, unresolved = asyncio.run(repair_engine.repair_file(
        "main.py", 'import os\nprint("ready")\n', [finding]
    ))

    assert repair.changed is True
    assert repair.resolved_finding_ids == []
    assert unresolved == [finding]
    assert repair.skipped_checks == ["ruff"]


def test_python_syntax_success_does_not_hide_unavailable_ruff_check(monkeypatch):
    monkeypatch.setattr(validation_service, "_ruff_diagnostics", lambda _code: None)

    valid, message = validation_service.validate_repair(
        'import os\nprint("ready")\n',
        'import os\nprint("changed")\n',
    )

    assert valid is False
    assert "Ruff is unavailable" in message
    assert "could not complete" in message


def test_ollama_repair_uses_extended_timeout(monkeypatch):
    from app.services import ollama_service

    captured = {}

    class FakeClient:
        def __init__(self, timeout):
            captured["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            captured["request"] = _kwargs["json"]

            class Response:
                def raise_for_status(self):
                    return None

                def json(self):
                    return {"response": "{}"}

            return Response()

    monkeypatch.setattr(ollama_service.httpx, "AsyncClient", FakeClient)
    asyncio.run(ollama_service.generate_code("repair prompt"))

    assert captured["timeout"] == 600
    assert captured["request"]["format"] == ollama_service.REPAIR_RESPONSE_SCHEMA
    assert captured["request"]["system"] == ollama_service.REPAIR_SYSTEM_PROMPT
    assert captured["request"]["options"] == {"temperature": 0}


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ([], "Ollama returned an invalid response object"),
        ({}, "Ollama returned an empty response"),
        ({"response": "  "}, "Ollama returned an empty response"),
    ],
)
def test_ollama_rejects_invalid_or_empty_response_envelopes(monkeypatch, payload, message):
    from app.services import ollama_service

    class FakeClient:
        def __init__(self, timeout):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            class Response:
                def raise_for_status(self):
                    return None

                def json(self):
                    return payload

            return Response()

    monkeypatch.setattr(ollama_service.httpx, "AsyncClient", FakeClient)
    with pytest.raises(ValueError, match=message):
        asyncio.run(ollama_service.generate_code("repair prompt"))


def test_repair_rejects_empty_code_inside_valid_json(monkeypatch):
    async def generated_response(_prompt: str) -> str:
        return json.dumps({
            "code": " \n ",
            "explanation": "",
            "resolved_finding_ids": [],
        })

    monkeypatch.setattr(repair_engine, "generate_code", generated_response)
    finding = Finding(file="main.js", message="Fix this issue")

    repair, unresolved = asyncio.run(repair_engine.repair_file(
        "main.js", "const value = 1;\n", [finding], language="javascript"
    ))

    assert repair.changed is False
    assert repair.explanation == "Model returned empty corrected code"
    assert unresolved == [finding]


def test_validate_repair_allows_existing_rule_after_line_shift(monkeypatch):
    diagnostics = iter([
        [{"code": "W292", "message": "No newline at end of file", "location": {"row": 1, "column": 1}}],
        [{"code": "W292", "message": "No newline at end of file", "location": {"row": 2, "column": 1}}],
    ])
    monkeypatch.setattr(validation_service, "_ruff_diagnostics", lambda _code: next(diagnostics))

    valid, _ = validation_service.validate_repair(
        "print('hello')",
        '"""Greeting module."""\nprint("hello")',
    )

    assert valid is True


def test_validate_repair_rejects_additional_diagnostic_with_same_rule(monkeypatch):
    diagnostics = iter([
        [{"code": "W292", "message": "No newline at end of file", "location": {"row": 1, "column": 1}}],
        [
            {"code": "W292", "message": "No newline at end of file", "location": {"row": 1, "column": 1}},
            {"code": "W292", "message": "No newline at end of file", "location": {"row": 2, "column": 1}},
        ],
    ])
    monkeypatch.setattr(validation_service, "_ruff_diagnostics", lambda _code: next(diagnostics))

    valid, message = validation_service.validate_repair(
        "print('hello')",
        '"""Greeting module."""\nprint("hello")',
    )

    assert valid is False
    assert "W292 at line 2" in message


def test_validate_repair_checks_all_files_in_a_java_project(monkeypatch):
    observed = {}

    def run(command, **kwargs):
        observed["command"] = command
        java_paths = [Path(argument) for argument in command if argument.endswith(".java")]
        assert {path.name for path in java_paths} == {"Main.java", "Helper.java"}
        assert all(path.exists() for path in java_paths)
        assert java_paths[0].parents[1].joinpath("pom.xml").exists()
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(validation_service.subprocess, "run", run)
    original_files = {
        "src/Main.java": "class Main { Helper helper; }\n",
        "src/Helper.java": "class Helper {}\n",
        "pom.xml": "<project />",
    }
    candidate_files = {
        **original_files,
        "src/Main.java": "class Main { Helper helper; int value = 1; }\n",
    }

    valid, message = validation_service.validate_repair(
        original_files["src/Main.java"],
        candidate_files["src/Main.java"],
        "java",
        "src/Main.java",
        original_files=original_files,
        candidate_files=candidate_files,
    )

    assert valid is True
    assert "javac validation passed" in message
    assert len([item for item in observed["command"] if item.endswith(".java")]) == 2


def test_validate_repair_compares_ruff_findings_across_all_project_files(monkeypatch):
    reports = iter([
        [{"filename": "src/helper.py", "code": "F401", "message": "unused import"}],
        [{"filename": "src/helper.py", "code": "F401", "message": "unused import"}],
    ])
    monkeypatch.setattr(validation_service, "_ruff_project_diagnostics", lambda _files: next(reports))
    original_files = {
        "src/main.py": "print('before')\n",
        "src/helper.py": "import os\n",
    }
    candidate_files = {
        "src/main.py": "print('after')\n",
        "src/helper.py": "import os\n",
    }

    valid, message = validation_service.validate_repair(
        original_files["src/main.py"],
        candidate_files["src/main.py"],
        original_files=original_files,
        candidate_files=candidate_files,
    )

    assert valid is True
    assert "no-new-Ruff-findings" in message


def test_validate_repair_matches_single_file_ruff_findings_across_temp_paths(monkeypatch):
    def run(command, **_kwargs):
        source_path = Path(command[-1])
        source_path.read_text(encoding="utf-8")
        return SimpleNamespace(
            returncode=1,
            stdout=json.dumps([{
                "filename": str(source_path),
                "code": "F401",
                "message": "`os` imported but unused",
                "location": {"row": 1, "column": 8},
            }]),
            stderr="",
        )

    monkeypatch.setattr(validation_service.shutil, "which", lambda _tool: "ruff")
    monkeypatch.setattr(validation_service.subprocess, "run", run)

    valid, message = validation_service.validate_repair(
        "import os\nprint('before')\n",
        "import os\nprint('after')\n",
    )

    assert valid is True
    assert "no-new-Ruff-findings" in message


def test_validate_repair_rejects_unchanged_source():
    valid, message = validation_service.validate_repair(
        "print('same')\n",
        "print('same')\n",
    )

    assert valid is False
    assert message == "Repair candidate is unchanged."


def test_validate_repair_rejects_python_nonbreaking_space_without_normalizing_it():
    candidate = "import os\u00a0 # keep source bytes unchanged\n"

    valid, message = validation_service.validate_repair(
        "print('original')\n",
        candidate,
    )

    assert valid is False
    assert "U+00A0" in message
    assert candidate == "import os\u00a0 # keep source bytes unchanged\n"


@pytest.mark.parametrize(
    ("language", "filename", "tool"),
    [
        ("javascript", "main.js", "node"),
        ("typescript", "main.ts", "tsc"),
        ("java", "Main.java", "javac"),
        ("c", "main.c", "gcc"),
        ("cpp", "main.cpp", "g++"),
        ("go", "main.go", "go"),
        ("csharp", "Program.cs", "mcs"),
    ],
)
def test_validate_repair_runs_language_compiler_for_non_python_candidates(
    monkeypatch, language, filename, tool
):
    observed = {}

    def run(command, **kwargs):
        observed["command"] = command
        observed["env"] = kwargs.get("env")
        observed["timeout"] = kwargs.get("timeout")
        source_path = Path(command[-1])
        assert source_path.name == filename
        assert source_path.read_text(encoding="utf-8") == "valid candidate\n"
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(validation_service.subprocess, "run", run)

    valid, message = validation_service.validate_repair(
        "original source\n",
        "valid candidate\n",
        language,
        filename,
    )

    assert valid is True
    assert tool in observed["command"][0]
    assert "validation passed" in message
    if language == "typescript":
        assert observed["timeout"] == 60
    if language == "go":
        assert observed["env"]["GO111MODULE"] == "off"
        assert observed["command"][1] == "build"


@pytest.mark.parametrize(
    ("language", "filename"),
    [("go", "main.go"), ("csharp", "Program.cs")],
)
def test_validate_repair_rejects_compiler_errors_for_go_and_csharp(
    monkeypatch, language, filename
):
    monkeypatch.setattr(
        validation_service.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="compiler diagnostic: invalid source",
        ),
    )

    valid, message = validation_service.validate_repair(
        "original source\n",
        "invalid candidate\n",
        language,
        filename,
    )

    assert valid is False
    assert "compiler diagnostic" in message


def test_validate_repair_reports_missing_non_python_tool(monkeypatch):
    def missing_tool(*_args, **_kwargs):
        raise FileNotFoundError("compiler missing")

    monkeypatch.setattr(validation_service.subprocess, "run", missing_tool)

    valid, message = validation_service.validate_repair(
        "original\n",
        "candidate\n",
        "go",
        "main.go",
    )

    assert valid is False
    assert "Required validation tool is not installed: go" == message


@pytest.mark.parametrize("path", ["../main.py", "/tmp/main.py", "C:/temp/main.py", "notes.txt"])
def test_repair_request_rejects_unsafe_or_non_python_paths(path):
    with pytest.raises(ValidationError):
        RepairRequest(files=[{"path": path, "content": "print(1)"}])