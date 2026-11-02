import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import main


CLIENT = TestClient(main.app)
LANGUAGE_PROFILES = [
    ("main.py", "p/python"),
    ("main.js", "p/javascript"),
    ("main.ts", "p/typescript"),
    ("Main.java", "p/java"),
    ("main.c", "p/c"),
    ("main.cpp", "p/cpp"),
    ("main.go", "p/golang"),
    ("Program.cs", "p/csharp"),
]


@pytest.mark.parametrize(("filename", "profile"), LANGUAGE_PROFILES)
def test_security_scan_accepts_supported_language_and_selects_semgrep_profile(
    monkeypatch, filename, profile
):
    calls = []

    monkeypatch.setattr(main, "run_bandit", lambda *_args: [])

    def fake_run(arguments, **_kwargs):
        calls.append(arguments)
        return SimpleNamespace(
            stdout=json.dumps({"results": []}),
            stderr="",
            returncode=0,
        )

    monkeypatch.setattr(main.subprocess, "run", fake_run)
    response = CLIENT.post("/scan", json={"filename": filename, "code": "source"})

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert calls[0][:3] == ["semgrep", "--config", profile]


@pytest.mark.parametrize("filename", [filename for filename, _ in LANGUAGE_PROFILES])
def test_security_api_returns_results_for_each_supported_language(monkeypatch, filename):
    monkeypatch.setattr(main, "run_bandit", lambda *_args: [])
    monkeypatch.setattr(main, "run_semgrep", lambda *_args: [])
    monkeypatch.setattr(main, "run_clang_static_analyzer", lambda *_args: [])
    monkeypatch.setattr(main, "run_gitleaks", lambda *_args: [])

    response = CLIENT.post("/scan", json={"filename": filename, "code": "source"})

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert response.json()["total_findings"] == 0


def test_project_security_scan_runs_manifest_scanners_once(monkeypatch):
    calls = []
    monkeypatch.setattr(main, "run_bandit", lambda *_args: [])
    monkeypatch.setattr(main, "run_semgrep", lambda *_args: [])
    monkeypatch.setattr(main, "run_clang_static_analyzer", lambda *_args: [])
    monkeypatch.setattr(main, "run_gitleaks", lambda *_args: [])
    monkeypatch.setattr(
        main,
        "run_npm_audit",
        lambda files: calls.append(("npm", set(files))) or [
            main.SecurityFinding(
                type="NpmAudit",
                severity="HIGH",
                file="package-lock.json",
                description="example: vulnerable dependency",
            )
        ],
    )
    monkeypatch.setattr(
        main,
        "run_govulncheck",
        lambda files: calls.append(("go", set(files))) or [],
    )

    response = CLIENT.post("/scan-project", json={
        "project_id": "web-app",
        "files": {
            "src/main.ts": "const value: number = 1;",
            "package.json": "{}",
            "package-lock.json": "{}",
        },
    })

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert response.json()["findings"][0]["file"] == "package-lock.json"
    assert calls == [
        ("npm", {"src/main.ts", "package.json", "package-lock.json"}),
        ("go", {"src/main.ts", "package.json", "package-lock.json"}),
    ]


def test_project_security_scan_rejects_projects_without_source():
    response = CLIENT.post("/scan-project", json={
        "project_id": "manifest-only",
        "files": {"package.json": "{}"},
    })

    assert response.status_code == 422


def test_npm_audit_maps_lockfile_advisories(monkeypatch):
    observed = {}

    def fake_run(command, **kwargs):
        observed["command"] = command
        observed["cwd"] = kwargs["cwd"]
        return SimpleNamespace(
            returncode=1,
            stdout=json.dumps({
                "vulnerabilities": {
                    "example-package": {
                        "severity": "high",
                        "via": [{"title": "Prototype pollution"}],
                    }
                }
            }),
            stderr="",
        )

    monkeypatch.setattr(main.subprocess, "run", fake_run)

    findings = main.run_npm_audit({
        "package.json": '{"name":"example"}',
        "package-lock.json": '{"lockfileVersion":3}',
    })

    assert observed["command"][:3] == ["npm", "audit", "--json"]
    assert "--ignore-scripts" in observed["command"]
    assert findings[0].file == "package-lock.json"
    assert findings[0].severity == "HIGH"
    assert "Prototype pollution" in findings[0].description


def test_govulncheck_maps_go_vulnerability_results(monkeypatch):
    monkeypatch.setattr(
        main.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=3,
            stdout="Vulnerability #1: GO-2025-1234: vulnerable module\n",
            stderr="",
        ),
    )

    findings = main.run_govulncheck({
        "go.mod": "module example.com/app\n",
        "main.go": "package main\nfunc main() {}\n",
    })

    assert len(findings) == 1
    assert findings[0].file == "go.mod"
    assert findings[0].type == "Govulncheck"
    assert "GO-2025-1234" in findings[0].description


def test_dotnet_security_analysis_uses_sandbox_roslyn_diagnostics(monkeypatch):
    calls = []

    class FakeResponse:
        def raise_for_status(self):
            pass

        @staticmethod
        def json():
            return {
                "executed": True,
                "status": "completed",
                "stdout": "Program.cs(7,3): warning CA3001: Possible SQL injection.\n",
                "stderr": "",
            }

    monkeypatch.setattr(main, "SECURITY_SANDBOX_SERVICE_URL", "http://sandbox")

    def fake_post(*args, **kwargs):
        calls.append((args, kwargs))
        return FakeResponse()

    monkeypatch.setattr(main.httpx, "post", fake_post)

    findings = main.run_dotnet_security_analyzers({
        "Program.cs": "class Program {}\n",
    })

    assert len(findings) == 1
    assert findings[0].type == "DotNetSecurityAnalyzer-CA3001"
    assert findings[0].file == "Program.cs"
    assert findings[0].line == 7
    assert calls[0][1]["json"]["timeout"] == 180
    assert calls[0][1]["timeout"] == 210
