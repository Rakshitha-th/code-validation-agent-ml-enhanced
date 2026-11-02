from types import SimpleNamespace

import pytest

from app.analyzers import mypy_analyzer


def test_run_mypy_maps_diagnostics_to_submitted_project_paths(monkeypatch):
    observed = {}

    def fake_run(command, **_kwargs):
        observed["command"] = command
        path = command[-1]
        return SimpleNamespace(
            returncode=1,
            stdout=f"{path}:3: error: Incompatible return value type [return-value]\n",
            stderr="",
        )

    monkeypatch.setattr(mypy_analyzer.subprocess, "run", fake_run)

    issues = mypy_analyzer.run_mypy({"src/main.py": "def main() -> int:\n    return 'bad'\n"})

    assert observed["command"][0] == "mypy"
    assert issues == [{
        "source": "mypy",
        "file": "src/main.py",
        "line_number": 3,
        "code": "MYPY_RETURN_VALUE",
        "message": "Incompatible return value type",
        "severity_hint": "high",
    }]


def test_run_mypy_preserves_diagnostics_when_project_analysis_is_partial(monkeypatch):
    def fake_run(command, **_kwargs):
        path = command[-1]
        return SimpleNamespace(
            returncode=2,
            stdout=f"{path}:3: error: expected an indented block [syntax]\n",
            stderr="",
        )

    monkeypatch.setattr(mypy_analyzer.subprocess, "run", fake_run)

    with pytest.raises(mypy_analyzer.PartialMypyAnalysisError) as error:
        mypy_analyzer.run_mypy({
            "src/main.py": "def main():\n\nreturn 0\n",
        })

    assert error.value.issues == [{
        "source": "mypy",
        "file": "src/main.py",
        "line_number": 3,
        "code": "MYPY_SYNTAX",
        "message": "expected an indented block",
        "severity_hint": "high",
    }]
