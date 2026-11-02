import asyncio
from datetime import datetime, timezone
import pytest
from fastapi.testclient import TestClient
from uuid import uuid4

from app.main import app
from app.routes import analysis, system
from app.database import SessionFactory
from app.models import AnalysisRunRecord, FindingRecord, ReportRecord, ValidationRecord


@pytest.fixture
def client():
    with TestClient(app, base_url="https://testserver") as test_client:
        registered = test_client.post("/api/auth/register", json={
            "email": f"api-test-{uuid4().hex}@example.test",
            "full_name": "API Test User",
            "password": "test-password-long-123",
        })
        assert registered.status_code == 201
        yield test_client


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200
        self.text = ""

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload

    async def aread(self):
        return b""


class FakeAsyncClient:
    posts = []
    gets = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def post(self, url, json):
        self.posts.append((url, json))
        if url.endswith("/analyze"):
            return FakeResponse({"agent": "code_quality", "findings": []})
        if "/workflow/cancel/" in url:
            return FakeResponse({
                "project_id": json.get("project_id", "project"),
                "status": "cancelled",
                "progress": "repair",
                "stage_status": "cancelled",
                "validation_status": "inconclusive",
                "error": "Workflow cancelled before final validation.",
            })
        return FakeResponse({"status": "submitted", "project_id": json["project_id"]})

    async def get(self, url):
        self.gets.append(url)
        return FakeResponse({
            "project_id": "project",
            "status": "completed",
            "result": {
                "findings": [],
                "validation_status": "passed",
                "final_report": {"project_id": "project", "validation_status": "passed"},
            },
        })


def test_analysis_file_strips_matching_markdown_code_fence():
    file = analysis.ProjectFileIn(
        path="Main.java",
        content="```java\npublic class Main {}\n```\n",
    )

    assert file.content == "public class Main {}\n"


def test_project_and_analysis_contract(monkeypatch, client):
    monkeypatch.setattr(analysis.httpx, "AsyncClient", FakeAsyncClient)

    project = client.post("/api/projects", json={
        "name": "demo",
        "source_code": "print('ok')\n",
    })
    assert project.status_code == 200
    project_id = project.json()["project_id"]

    started = client.post("/api/analysis", json={"project_id": project_id})
    assert started.status_code == 200
    assert started.json()["status"] == "started"

    run_id = started.json()["run_id"]
    status = client.get(f"/api/analysis/{run_id}/status")
    assert status.status_code == 200
    assert status.json()["status"] == "completed"


def test_code_quality_adapter_forwards_to_service(monkeypatch, client):
    monkeypatch.setattr(analysis.httpx, "AsyncClient", FakeAsyncClient)

    response = client.post("/api/agents/code-quality/analyze", json={
        "project_id": "quality-test",
        "files": [{"path": "main.py", "content": "print('ok')"}],
        "tools": ["ruff"],
    })

    assert response.status_code == 200
    assert response.json()["agent"] == "code_quality"


def test_project_files_and_management_routes_persist(client):
    created = client.post("/api/projects", json={
        "name": "persisted-demo",
        "files": [{"path": "src/main.py", "content": "print('before')\n"}],
    })
    assert created.status_code == 200
    project_id = created.json()["project_id"]

    listed = client.get("/api/projects", params={"search": "persisted-demo"})
    assert listed.status_code == 200
    assert listed.json()["items"][0]["project_id"] == project_id

    files = client.get("/api/files", params={"project_id": project_id})
    assert files.json()["files"] == [{"path": "src/main.py", "content": "print('before')\n"}]

    saved = client.post("/api/files", json={
        "project_id": project_id,
        "files": [{"path": "src/main.py", "content": "print('after')\n"}],
    })
    assert saved.status_code == 200
    assert client.get("/api/files", params={"project_id": project_id}).json()["files"][0]["content"] == "print('after')\n"

    updated = client.patch(f"/api/projects/{project_id}", json={"name": "renamed-demo"})
    assert updated.status_code == 200
    assert updated.json()["name"] == "renamed-demo"

    assert client.delete(f"/api/projects/{project_id}").status_code == 204
    assert client.get(f"/api/projects/{project_id}").status_code == 404


def test_analysis_run_is_persisted_with_restart_safe_run_id(monkeypatch, client):
    monkeypatch.setattr(analysis.httpx, "AsyncClient", FakeAsyncClient)
    project = client.post("/api/projects", json={
        "name": "persisted-analysis",
        "files": [{"path": "main.py", "content": "print('ok')\n"}],
    }).json()

    started = client.post("/analysis/run", json={
        "project_id": project["project_id"],
        "files": [{"path": "main.py", "content": "print('ok')\n"}],
        "tools": ["ruff"],
    })
    assert started.status_code == 200
    run_id = started.json()["run_id"]

    status = client.get(f"/api/analysis/{run_id}/status")
    assert status.status_code == 200
    assert status.json()["run_id"] == run_id
    assert status.json()["project_id"] == project["project_id"]

    report = client.get(f"/results/{project['project_id']}")
    assert report.status_code == 200
    assert report.json()["report"]["validation_status"] == "passed"
    report_by_run = client.get(f"/results/{run_id}")
    assert report_by_run.status_code == 200
    assert report_by_run.json()["project_id"] == project["project_id"]
    history = client.get("/api/history")
    assert history.status_code == 200
    assert any(item["run_id"] == run_id for item in history.json()["items"])


def test_analysis_run_forwards_test_cases_to_the_orchestrator(monkeypatch, client):
    FakeAsyncClient.posts.clear()
    monkeypatch.setattr(analysis.httpx, "AsyncClient", FakeAsyncClient)
    project = client.post("/api/projects", json={
        "name": "expected-output-analysis",
        "files": [{"path": "main.go", "content": 'package main\nimport "fmt"\nfunc main(){fmt.Println("ok")}\n'}],
    }).json()
    test_cases = [{"input": "", "expected": "ok"}]

    response = client.post("/analysis/run", json={
        "project_id": project["project_id"],
        "files": [{"path": "main.go", "content": 'package main\nimport "fmt"\nfunc main(){fmt.Println("ok")}\n'}],
        "test_cases": test_cases,
    })

    assert response.status_code == 200
    orchestrator_payload = next(
        payload for url, payload in FakeAsyncClient.posts if url.endswith("/workflow/start")
    )
    assert orchestrator_payload["project_context"]["test_cases"] == test_cases


def test_analysis_run_rejects_malformed_test_cases(client):
    project = client.post("/api/projects", json={
        "name": "invalid-test-case-analysis",
        "files": [{"path": "main.py", "content": "print('ok')\n"}],
    }).json()

    response = client.post("/analysis/run", json={
        "project_id": project["project_id"],
        "files": [{"path": "main.py", "content": "print('ok')\n"}],
        "test_cases": [{"input": "", "expected": 4}],
    })

    assert response.status_code == 422


def test_completed_run_with_validation_disabled_is_not_run(monkeypatch, client):
    class PendingValidationClient(FakeAsyncClient):
        async def get(self, url):
            self.gets.append(url)
            return FakeResponse({
                "project_id": "project",
                "status": "completed",
                "result": {
                    "validation_status": "pending",
                    "validation_results": {},
                    "final_report": {
                        "project_id": "project",
                        "validation_status": "pending",
                        "analysis_checks": {"final_validation": False},
                    },
                },
            })

    monkeypatch.setattr(analysis.httpx, "AsyncClient", PendingValidationClient)
    project = client.post("/api/projects", json={
        "name": "validation-disabled",
        "files": [{"path": "main.py", "content": "print('ok')\n"}],
    }).json()
    started = client.post("/analysis/run", json={
        "project_id": project["project_id"],
        "files": [{"path": "main.py", "content": "print('ok')\n"}],
        "project_context": {"analysis_checks": {"final_validation": False}},
    }).json()

    status = client.get(f"/api/analysis/{started['run_id']}/status")
    results = client.get(f"/results/{started['run_id']}")
    history = client.get("/api/history")

    assert status.status_code == 200
    assert status.json()["status"] == "completed"
    assert results.json()["result"]["validation_status"] == "not_run"
    history_item = next(item for item in history.json()["items"] if item["run_id"] == started["run_id"])
    assert history_item["validation_status"] == "not_run"


def test_cancel_analysis_persists_cancelled_without_passing(monkeypatch, client):
    monkeypatch.setattr(analysis.httpx, "AsyncClient", FakeAsyncClient)
    project = client.post("/api/projects", json={
        "name": "cancel-analysis",
        "files": [{"path": "main.py", "content": "print('ok')\n"}],
    }).json()
    started = client.post("/analysis/run", json={
        "project_id": project["project_id"],
        "files": [{"path": "main.py", "content": "print('ok')\n"}],
    }).json()

    cancelled = client.post(f"/api/analysis/{started['run_id']}/cancel", json={})
    history = client.get("/api/history")

    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    item = next(entry for entry in history.json()["items"] if entry["run_id"] == started["run_id"])
    assert item["status"] == "cancelled"
    assert item["validation_status"] == "inconclusive"


def test_runs_for_one_project_use_independent_workflow_ids(monkeypatch, client):
    monkeypatch.setattr(analysis.httpx, "AsyncClient", FakeAsyncClient)
    FakeAsyncClient.posts.clear()
    FakeAsyncClient.gets.clear()
    project = client.post("/api/projects", json={
        "name": "repeat-analysis",
        "files": [{"path": "main.py", "content": "print('ok')\n"}],
    }).json()
    request = {
        "project_id": project["project_id"],
        "files": [{"path": "main.py", "content": "print('ok')\n"}],
    }

    first = client.post("/analysis/run", json=request).json()
    second = client.post("/analysis/run", json=request).json()

    assert first["run_id"] != second["run_id"]
    assert [body["workflow_id"] for _url, body in FakeAsyncClient.posts[-2:]] == [first["run_id"], second["run_id"]]

    status = client.get(f"/api/analysis/{first['run_id']}/status")
    assert status.status_code == 200
    assert status.json()["run_id"] == first["run_id"]
    assert FakeAsyncClient.gets[-1].endswith(f"/workflow/status/{first['run_id']}")


def test_public_agent_health_is_available_without_authentication(monkeypatch):
    async def healthy_service(_client, name, _url):
        return name, {"status": "healthy", "healthy": True, "details": {"service": name}}

    monkeypatch.setattr(system, "_check_service", healthy_service)
    with TestClient(app, base_url="https://testserver") as anonymous_client:
        public_response = anonymous_client.get("/api/system/public-agents")
        protected_response = anonymous_client.get("/api/system/agents")

    assert public_response.status_code == 200
    assert public_response.json()["services"]["code_quality"] is True
    assert protected_response.status_code == 401


def test_overview_returns_authenticated_backend_metrics(client):
    project = client.post("/api/projects", json={
        "name": "overview-project",
        "files": [{"path": "main.py", "content": "print('overview')\n"}],
    }).json()
    run_id = f"overview-run-{uuid4().hex}"

    async def persist_run_data():
        async with SessionFactory() as session:
            session.add(AnalysisRunRecord(
                run_id=run_id,
                project_id=project["project_id"],
                workflow_id=run_id,
                status="completed",
                validation_status="passed",
                project_context={"language": "python"},
                result={"retry_count": 2},
                started_at=datetime.now(timezone.utc),
            ))
            session.add_all([
                FindingRecord(
                    run_id=run_id,
                    finding_id=f"{run_id}-security",
                    category="security",
                    severity="high",
                    file_path="main.py",
                    status="open",
                    payload={"issue": "Example security finding"},
                ),
                FindingRecord(
                    run_id=run_id,
                    finding_id=f"{run_id}-quality",
                    category="code_quality",
                    severity="low",
                    file_path="main.py",
                    status="open",
                    payload={"issue": "Example quality finding"},
                ),
                ValidationRecord(
                    run_id=run_id,
                    passed=True,
                    payload={"passed": True},
                ),
                ReportRecord(
                    run_id=run_id,
                    payload={"repair_status": "success"},
                ),
            ])
            await session.commit()

    asyncio.run(persist_run_data())

    response = client.get("/api/overview")

    assert response.status_code == 200
    payload = response.json()
    assert payload["metrics"]["project_count"] == 1
    assert payload["metrics"]["run_count"] == 1
    assert payload["metrics"]["runs_today"] == 1
    assert payload["metrics"]["latest_security_findings"] == 1
    assert payload["metrics"]["latest_high_security_findings"] == 1
    assert payload["metrics"]["repair_successes"] == 1
    assert payload["metrics"]["repair_attempted"] == 1
    assert payload["metrics"]["validation_passed"] == 1
    assert payload["metrics"]["validation_counted"] == 1
    assert payload["latest_findings_by_severity"]["high"] == 1
    assert payload["latest_findings_by_severity"]["low"] == 1
    assert payload["latest_findings_by_language"]["python"] == 2
    assert payload["languages"][0] == {
        "key": "python",
        "label": "Python",
        "support": "full",
        "project_count": 1,
    }
    assert payload["recent_runs"][0]["run_id"] == run_id
    assert payload["recent_runs"][0]["finding_count"] == 2


def test_overview_requires_authentication():
    with TestClient(app, base_url="https://testserver") as anonymous_client:
        response = anonymous_client.get("/api/overview")

    assert response.status_code == 401
