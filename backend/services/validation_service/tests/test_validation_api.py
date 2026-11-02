import subprocess

import pytest
from fastapi.testclient import TestClient

from app import validator
from app import main
from app.main import app


client = TestClient(app)


def test_missing_flake8_is_not_reported_as_valid(monkeypatch):
    def missing_analyzer(*_args, **_kwargs):
        return subprocess.CompletedProcess(
            args=["python", "-m", "flake8"],
            returncode=2,
            stdout="",
            stderr="No module named flake8",
        )

    monkeypatch.setattr(validator.subprocess, "run", missing_analyzer)
    response = client.post("/validate", json={
        "code": "print('valid syntax')\n",
        "filename": "main.py",
    })

    assert response.status_code == 200
    assert response.json()["valid"] is False
    assert response.json()["findings"][0]["code"] == "VALIDATOR_ERROR"
    assert "No module named flake8" in response.json()["findings"][0]["message"]


def test_flake8_timeout_is_a_validation_failure(monkeypatch):
    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd="flake8", timeout=20)

    monkeypatch.setattr(validator.subprocess, "run", timeout)
    response = client.post("/validate", json={
        "code": "print('valid syntax')\n",
        "filename": "main.py",
    })

    assert response.status_code == 200
    assert response.json()["valid"] is False
    assert response.json()["findings"][0]["code"] == "VALIDATOR_TIMEOUT"


def test_style_findings_do_not_require_human_review(monkeypatch):
    monkeypatch.setattr(main, "validate_code", lambda *_args: ([{
        "type": "CodeQuality",
        "severity": "LOW",
        "file": "main.py",
        "line": 1,
        "message": "Unused import",
        "code": "F401",
        "recommendation": "Remove the unused import.",
    }], {"lines_of_code": 1, "functions": 0, "classes": 0, "imports": 1, "max_function_length": 0, "max_nesting_depth": 0}))

    response = client.post("/validate", json={"code": "import os", "filename": "main.py"})

    assert response.status_code == 200
    assert response.json()["valid"] is True
    assert response.json()["status"] == "passed"
    assert response.json()["needs_review"] is False


def test_valid_python_passes_syntax_check(monkeypatch):
    monkeypatch.setattr(validator, "run_flake8", lambda *_args: [])

    response = client.post("/validate", json={
        "code": "def greet(name):\n    return f'hello {name}'\n",
        "filename": "main.py",
    })

    assert response.status_code == 200
    assert response.json()["valid"] is True
    assert response.json()["status"] == "passed"


def test_e305_is_reported_without_review_requirement(monkeypatch):
    monkeypatch.setattr(validator, "run_flake8", lambda *_args: [{
        "type": "CodeQuality",
        "severity": "LOW",
        "file": "main.py",
        "line": 4,
        "column": 1,
        "message": "E305: expected 2 blank lines after class or function definition",
        "code": "E305",
        "recommendation": "Add the required blank lines after the definition.",
    }])

    response = client.post("/validate", json={
        "code": "def greet():\n    return 'hello'\nprint(greet())\n",
        "filename": "main.py",
    })

    assert response.status_code == 200
    assert response.json()["status"] == "passed"
    assert response.json()["findings"][0]["code"] == "E305"
    assert response.json()["findings"][0]["line"] == 4


def test_missing_colon_fails_syntax_with_diagnostic():
    response = client.post("/validate", json={
        "code": "if True\n    print('broken')\n",
        "filename": "main.py",
    })

    assert response.status_code == 200
    assert response.json()["valid"] is False
    assert response.json()["status"] == "failed"
    assert response.json()["findings"][0]["code"] == "SYNTAX"
    assert response.json()["findings"][0]["line"] == 1
    assert "expected ':'" in response.json()["findings"][0]["message"]


@pytest.mark.parametrize(
    ("filename", "source", "expected_language"),
    [
        ("Main.java", "class Main {}\n", "java"),
        ("main.js", 'console.log("ok");\n', "javascript"),
        ("main.ts", "const value: number = 1;\n", "typescript"),
        ("main.c", "int main(void) { return 0; }\n", "c"),
        ("main.cpp", "int main() { return 0; }\n", "cpp"),
        ("main.go", "package main\nfunc main() {}\n", "go"),
        ("Program.cs", "class Program {}\n", "csharp"),
    ],
)
def test_non_python_validation_runs_in_isolated_sandbox(
    monkeypatch, filename, source, expected_language
):
    class SandboxResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "status": "completed",
                "executed": True,
                "passed": True,
                "exit_code": 0,
                "stdout": "",
                "stderr": "",
                "validator": f"{expected_language} compiler",
            }

    requests = []

    def sandbox_post(url, *, json, timeout):
        requests.append((url, json, timeout))
        return SandboxResponse()

    monkeypatch.setattr(main, "SANDBOX_SERVICE_URL", "http://sandbox:8004")
    monkeypatch.setattr(main.httpx, "post", sandbox_post)

    response = client.post("/validate", json={"filename": filename, "code": source})

    assert response.status_code == 200
    result = response.json()
    assert result["valid"] is True
    assert result["status"] == "passed"
    assert result["language"] == expected_language
    assert result["validator"] == f"{expected_language} compiler"
    assert requests[0][0] == "http://sandbox:8004/execute"
    assert requests[0][1]["validation_only"] is True
    assert requests[0][1]["files"] == {filename: source}


def test_non_python_compilation_error_returns_file_diagnostic(monkeypatch):
    class SandboxResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "status": "failed",
                "executed": True,
                "passed": False,
                "exit_code": 1,
                "stdout": "",
                "stderr": "/sandbox/run/main.c:4:2: error: expected ';'",
                "validator": "GCC",
            }

    monkeypatch.setattr(main, "SANDBOX_SERVICE_URL", "http://sandbox:8004")
    monkeypatch.setattr(main.httpx, "post", lambda *_args, **_kwargs: SandboxResponse())

    response = client.post("/validate", json={
        "filename": "main.c",
        "code": "int main(void) {\n return 0\n}\n",
    })

    assert response.status_code == 200
    result = response.json()
    assert result["valid"] is False
    assert result["status"] == "failed"
    assert result["findings"][0]["code"] == "COMPILATION_FAILED"
    assert result["findings"][0]["line"] == 4
    assert "expected ';'" in result["findings"][0]["message"]


def test_non_python_compiler_warning_does_not_require_human_review(monkeypatch):
    class SandboxResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "status": "completed",
                "executed": True,
                "passed": True,
                "exit_code": 0,
                "stdout": "",
                "stderr": "/sandbox/main.c:4:2: warning: unused variable",
                "validator": "GCC",
            }

    monkeypatch.setattr(main, "SANDBOX_SERVICE_URL", "http://sandbox:8004")
    monkeypatch.setattr(main.httpx, "post", lambda *_args, **_kwargs: SandboxResponse())

    response = client.post("/validate", json={
        "filename": "main.c",
        "code": "int main(void) { int value; return 0; }\n",
    })

    assert response.status_code == 200
    result = response.json()
    assert result["valid"] is True
    assert result["status"] == "passed"
    assert result["needs_review"] is False
    assert result["findings"][0]["message"].endswith("unused variable")


def test_non_python_project_sends_all_files_and_maps_diagnostic_path(monkeypatch):
    class SandboxResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "status": "failed",
                "executed": True,
                "passed": False,
                "exit_code": 1,
                "stdout": "",
                "stderr": "/sandbox/run/src/Helper.java:8: error: cannot find symbol",
                "validator": "JDK compiler",
            }

    files = {
        "src/Main.java": "class Main {}\n",
        "src/Helper.java": "class Helper {}\n",
        "pom.xml": "<project />",
    }
    request_payload = {}

    def sandbox_post(_url, *, json, **_kwargs):
        request_payload.update(json)
        return SandboxResponse()

    monkeypatch.setattr(main, "SANDBOX_SERVICE_URL", "http://sandbox:8004")
    monkeypatch.setattr(main.httpx, "post", sandbox_post)

    response = client.post("/validate", json={"files": files})

    assert response.status_code == 200
    assert request_payload["files"] == files
    assert request_payload["filename"] == "src/Main.java"
    assert response.json()["findings"][0]["file"] == "src/Helper.java"
    assert response.json()["findings"][0]["line"] == 8


def test_missing_project_dependencies_block_validation(monkeypatch):
    class SandboxResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "status": "dependency_missing",
                "executed": False,
                "passed": False,
                "stderr": "",
                "error": "External dependencies are not available in the offline runtime.",
            }

    monkeypatch.setattr(main, "SANDBOX_SERVICE_URL", "http://sandbox:8004")
    monkeypatch.setattr(main.httpx, "post", lambda *_args, **_kwargs: SandboxResponse())

    response = client.post("/validate", json={
        "files": {
            "src/Main.java": "class Main {}\n",
            "pom.xml": "<project />",
        },
    })

    assert response.status_code == 200
    assert response.json()["status"] == "blocked"
    assert response.json()["valid"] is False
    assert response.json()["findings"][0]["code"] == "VALIDATION_BLOCKED"


def test_python_project_validates_every_source_file(monkeypatch):
    validated_paths = []

    def validate_file(code, filename):
        validated_paths.append(filename)
        return [], {
            "lines_of_code": len(code.splitlines()),
            "functions": 0,
            "classes": 0,
            "imports": 0,
            "max_function_length": 0,
            "max_nesting_depth": 0,
        }

    monkeypatch.setattr(main, "validate_code", validate_file)

    response = client.post("/validate", json={"files": {
        "src/main.py": "print('main')\n",
        "src/helper.py": "print('helper')\n",
        "requirements.txt": "flake8\n",
    }})

    assert response.status_code == 200
    assert validated_paths == ["src/main.py", "src/helper.py"]
    assert response.json()["metrics"]["lines_of_code"] == 2


def test_non_python_validation_fails_explicitly_when_sandbox_is_unavailable(monkeypatch):
    monkeypatch.setattr(main, "SANDBOX_SERVICE_URL", None)

    response = client.post("/validate", json={
        "filename": "main.js",
        "code": "console.log('ok');",
    })

    assert response.status_code == 200
    assert response.json()["valid"] is False
    assert response.json()["status"] == "error"
    assert response.json()["findings"][0]["code"] == "VALIDATOR_UNAVAILABLE"