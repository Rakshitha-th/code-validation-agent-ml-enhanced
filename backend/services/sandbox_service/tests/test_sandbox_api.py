import sys
from types import ModuleType

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

docker_stub = ModuleType("docker")
docker_stub.from_env = lambda: object()
sys.modules.setdefault("docker", docker_stub)

from app import main


client = TestClient(main.app)


def test_execute_forwards_all_project_files_to_sandbox(monkeypatch):
    calls = []

    def fake_run_code(files, stdin, timeout, execution_id, filename):
        calls.append((files, stdin, timeout, filename))
        return {"status": "completed", "exit_code": 0, "stdout": "ok\n", "stderr": ""}

    monkeypatch.setattr(main, "run_code", fake_run_code)
    project_files = {
        "src/main.py": "from helper import value\nprint(value)\n",
        "src/helper.py": "value = 'ok'\n",
    }

    response = client.post("/execute", json={
        "filename": "src/main.py",
        "code": project_files["src/main.py"],
        "files": project_files,
        "stdin": "test input\n",
    })

    assert response.status_code == 200
    assert calls[0][0] == project_files
    assert calls[0][1] == "test input\n"
    assert calls[0][3] == "src/main.py"


def test_execute_preserves_single_file_api_contract(monkeypatch):
    calls = []

    def fake_run_code(files, stdin, timeout, execution_id, filename):
        calls.append((files, filename))
        return {"status": "completed", "exit_code": 0, "stdout": "ok\n", "stderr": ""}

    monkeypatch.setattr(main, "run_code", fake_run_code)
    response = client.post("/execute", json={"filename": "main.py", "code": "print('ok')"})

    assert response.status_code == 200
    assert calls == [({"main.py": "print('ok')"}, "main.py")]


def test_execute_rejects_traversal_in_project_file_paths():
    response = client.post("/execute", json={
        "filename": "main.py",
        "code": "print('ok')",
        "files": {"../outside.py": "print('unsafe')"},
    })

    assert response.status_code == 422


def test_sandbox_accepts_java_analysis_timeout_limit():
    request = main.CodeRequest(
        filename="Main.java",
        code="class Main {}\n",
        files={"Main.java": "class Main {}\n"},
        analysis_only=True,
        timeout=180,
    )

    assert request.timeout == 180


def test_sandbox_rejects_timeout_above_limit():
    with pytest.raises(ValidationError, match="Timeout must be between 1 and 180 seconds"):
        main.CodeRequest(filename="main.py", code="print('ok')", timeout=181)


def test_standalone_script_passes_only_after_zero_exit(monkeypatch):
    monkeypatch.setattr(main, "run_code", lambda *_args, **_kwargs: {
        "status": "completed", "executed": True, "passed": True, "exit_code": 0,
        "stdout": "ok\n", "stderr": "", "error_type": None,
    })

    response = client.post("/execute", json={"filename": "main.py", "code": "print('ok')"})

    assert response.json()["status"] == "completed"
    assert response.json()["passed"] is True
    assert response.json()["check_type"] == "script_execution"


def test_standalone_script_failure_is_not_passed(monkeypatch):
    monkeypatch.setattr(main, "run_code", lambda *_args, **_kwargs: {
        "status": "failed", "executed": True, "passed": False, "exit_code": 1,
        "stdout": "", "stderr": "RuntimeError", "error_type": "RuntimeError",
    })

    result = main.execute_code(main.CodeRequest(filename="main.py", code="raise RuntimeError()"))

    assert result["status"] == "failed"
    assert result["passed"] is False
    assert result["exit_code"] == 1


def test_browser_javascript_executes_in_isolated_chromium_runtime(monkeypatch):
    calls = []

    def fake_run_code(files, stdin, timeout, execution_id, filename, **options):
        calls.append((files, timeout, filename, options))
        return {
            "status": "completed",
            "executed": True,
            "passed": True,
            "exit_code": 0,
            "stdout": "browser-ok\n",
            "stderr": "",
        }

    monkeypatch.setattr(main, "run_code", fake_run_code)

    result = main.execute_code(main.CodeRequest(
        filename="main.js",
        code='document.body.textContent = "browser-ok";\n',
    ))

    assert result["status"] == "completed"
    assert result["executed"] is True
    assert result["passed"] is True
    assert result["check_type"] == "browser_execution"
    assert result["validator"] == "Chromium headless browser"
    assert result["execution_context"] == "generated_dom_harness"
    assert "browser_runner.cjs" in " ".join(calls[0][3]["command"])
    assert calls[0][3]["pids_limit"] == 128


def test_standalone_script_timeout_is_not_passed(monkeypatch):
    monkeypatch.setattr(main, "run_code", lambda *_args, **_kwargs: {
        "status": "timeout", "executed": True, "passed": False, "exit_code": -1,
        "stdout": "", "stderr": "Execution timed out.", "error_type": "Timeout",
    })

    result = main.execute_code(main.CodeRequest(filename="main.py", code="while True: pass"))

    assert result["status"] == "timeout"
    assert result["passed"] is False


def test_dotnet_project_reports_unavailable_when_sdk_mutex_cannot_initialize(monkeypatch):
    monkeypatch.setattr(main, "run_code", lambda *_args, **_kwargs: {
        "status": "failed",
        "executed": True,
        "passed": False,
        "exit_code": 1,
        "stdout": "",
        "stderr": (
            "System.IO.IOException: NuGet-Migrations\n"
            "at Microsoft.DotNet.Configurer.DotnetFirstTimeUseConfigurer.Configure()"
        ),
        "error_type": "ExecutionError",
    })
    project = {
        "Program.cs": "public class Program {}\n",
        "App.csproj": "<Project Sdk=\"Microsoft.NET.Sdk\"><PropertyGroup><TargetFramework>net8.0</TargetFramework></PropertyGroup></Project>",
    }

    result = main.execute_code(main.CodeRequest(
        filename="Program.cs",
        code=project["Program.cs"],
        files=project,
        validation_only=True,
    ))

    assert result["status"] == "unavailable"
    assert result["passed"] is False
    assert result["executed"] is False
    assert result["error_type"] == "ToolchainUnavailable"
    assert "hardened non-root sandbox" in result["error"]


def test_execution_container_allows_compiler_outputs_to_execute(monkeypatch):
    calls = []

    class ContainerManager:
        def run(self, **kwargs):
            calls.append(kwargs)

    class DockerClient:
        containers = ContainerManager()

    monkeypatch.setattr(main, "docker_client", DockerClient())

    main._start_execution_container(
        "execution-id",
        "main.c",
        image="runtime",
        command=["sh", "-c", "gcc main.c -o /tmp/program"],
    )

    assert calls[0]["tmpfs"]["/tmp"].startswith("rw,exec,")
    assert calls[0]["environment"]["PYTHONPATH"] == "/sandbox/execution-id:/app"
    assert calls[0]["pids_limit"] == 64


def test_complete_django_project_uses_prebuilt_runtime_and_manage_check(monkeypatch):
    calls = []

    def fake_run_code(*args, **kwargs):
        calls.append((args, kwargs))
        return {"status": "completed", "executed": True, "passed": True, "exit_code": 0, "stdout": "System check identified no issues.\n", "stderr": ""}

    monkeypatch.setattr(main, "run_code", fake_run_code)
    project = {
        "manage.py": "import os\nos.environ.setdefault('DJANGO_SETTINGS_MODULE', 'site.settings')\n",
        "site/settings.py": "SECRET_KEY = 'test'\nINSTALLED_APPS = []\nROOT_URLCONF = 'site.urls'\n",
        "site/urls.py": "urlpatterns = []\n",
        "requirements.txt": "Django==5.2.4\n",
    }

    result = main.execute_code(main.CodeRequest(filename="manage.py", files=project))

    assert result["status"] == "completed"
    assert result["check_type"] == "django_check"
    assert calls[0][0][4] == "manage.py"
    assert calls[0][0][2] == main.DJANGO_CHECK_TIMEOUT
    assert calls[0][1]["image"] == main.DJANGO_RUNTIME_IMAGE
    assert calls[0][1]["working_directory"] == "/sandbox"
    assert calls[0][1]["command"][-1] == "check"
    assert calls[0][1]["command"][2] == 'cd "$1" && exec python "$2" "$3"'


def test_complete_django_project_accepts_only_dependencies_in_prebuilt_runtime():
    project = {
        "manage.py": "os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'site.settings')\n",
        "site/settings.py": "SECRET_KEY = 'test'\n",
        "requirements.txt": "Django>=5.2,<5.3\n",
    }

    plan = main._execution_plan(main.CodeRequest(filename="manage.py", files=project))

    assert plan["status"] is None
    assert plan["check_type"] == "django_check"
    assert plan["image"] == main.DJANGO_RUNTIME_IMAGE


def test_django_dependency_outside_prebuilt_allowlist_is_dependency_missing(monkeypatch):
    monkeypatch.setattr(main, "run_code", lambda *_args, **_kwargs: pytest.fail("must not execute"))
    project = {
        "manage.py": "os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'site.settings')\n",
        "site/settings.py": "SECRET_KEY = 'test'\n",
        "requirements.txt": "Django==5.2.4\npsycopg==3.2.1\n",
    }

    result = main.execute_code(main.CodeRequest(filename="manage.py", files=project))

    assert result["status"] == "dependency_missing"
    assert result["passed"] is False
    assert "psycopg" in result["error"]


def test_lone_django_admin_module_is_project_incomplete(monkeypatch):
    monkeypatch.setattr(main, "run_code", lambda *_args, **_kwargs: pytest.fail("must not execute"))

    result = main.execute_code(main.CodeRequest(filename="app/admin.py", files={"app/admin.py": "from django.contrib import admin\n"}))

    assert result["status"] == "project_incomplete"
    assert result["passed"] is False


@pytest.mark.parametrize("path", ["../requirements.txt", "/tmp/requirements.txt", "C:/requirements.txt", "setup.cfg"])
def test_sandbox_rejects_unsafe_or_unsupported_project_files(path):
    with pytest.raises(ValidationError):
        main.CodeRequest(filename="main.py", files={"main.py": "print(1)\n", path: ""})


def test_test_case_failure_is_not_wrapped_as_a_completed_pass(monkeypatch):
    monkeypatch.setattr(main, "run_code", lambda *_args, **_kwargs: {
        "status": "failed", "executed": True, "passed": False, "exit_code": 1,
        "stdout": "", "stderr": "RuntimeError", "error_type": "RuntimeError",
    })

    result = main.execute_code(main.CodeRequest(
        filename="main.py",
        code="raise RuntimeError()",
        test_cases=[main.TestCase(input="", expected="ok")],
    ))

    assert result["status"] == "failed"
    assert result["passed"] == 0
    assert result["failed"] == 1


@pytest.mark.parametrize(
    ("expected", "status", "comparison_status", "passed", "failed", "unverified"),
    [
        ("ok", "completed", "passed", 1, 0, 0),
        ("wrong", "failed", "failed", 0, 1, 0),
        (None, "completed", "not_provided", 0, 0, 1),
    ],
)
def test_test_case_output_comparison_is_reported_separately_from_execution(
    monkeypatch, expected, status, comparison_status, passed, failed, unverified
):
    monkeypatch.setattr(main, "run_code", lambda *_args, **_kwargs: {
        "status": "completed", "executed": True, "passed": True, "exit_code": 0,
        "stdout": "ok\n", "stderr": "", "error_type": None,
    })

    result = main.execute_code(main.CodeRequest(
        filename="main.py",
        code='print("ok")\n',
        test_cases=[main.TestCase(input="", expected=expected)],
    ))

    assert result["status"] == status
    assert result["execution_status"] == "passed"
    assert result["passed_execution"] is True
    assert result["comparison_status"] == comparison_status
    assert (result["passed"], result["failed"], result["unverified"]) == (
        passed, failed, unverified
    )
    assert result["results"][0]["actual"] == "ok"
    assert result["results"][0]["expected"] == expected


def test_non_python_test_case_command_uses_the_same_workspace_as_its_files(monkeypatch):
    calls = []

    def fake_run_code(files, stdin, timeout, execution_id, filename, **options):
        calls.append((files, stdin, timeout, execution_id, filename, options))
        return {
            "status": "completed", "executed": True, "passed": True, "exit_code": 0,
            "stdout": "OK\n", "stderr": "", "error_type": None,
        }

    monkeypatch.setattr(main, "run_code", fake_run_code)
    project = {
        "src/main.c": '#include <stdio.h>\nint main(void) { puts("OK"); return 0; }\n',
        "src/helper.c": "int helper(void) { return 1; }\n",
    }

    result = main.execute_code(main.CodeRequest(
        filename="src/main.c",
        code=project["src/main.c"],
        files=project,
        test_cases=[main.TestCase(input="", expected="OK")],
    ))

    execution_id = calls[0][3]
    command = " ".join(calls[0][5]["command"])
    assert execution_id.endswith("-1")
    assert f"/sandbox/{execution_id}/src/main.c" in command
    assert calls[0][0] == project
    assert result["comparison_status"] == "passed"
    assert result["passed_execution"] is True