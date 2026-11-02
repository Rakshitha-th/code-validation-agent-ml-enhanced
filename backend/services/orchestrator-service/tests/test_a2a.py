import asyncio

from fastapi.testclient import TestClient
from uuid import uuid4

from app.main import app
from app import main
from app.schemas.requests import StartAnalysisRequest


client = TestClient(app)


def test_workflow_request_strips_java_markdown_code_fences():
    request = StartAnalysisRequest.model_validate({
        "project_id": "fenced-java",
        "files": [{"path": "Main.java", "content": "```java\npublic class Main {}\n```\n"}],
    })

    assert request.files[0].content == "public class Main {}\n"


def test_agent_card_describes_orchestrator_skills():
    response = client.get("/.well-known/agent-card.json")

    assert response.status_code == 200
    assert response.json()["name"] == "coding-validation-orchestrator"
    assert {skill["id"] for skill in response.json()["skills"]} == {
        "workflow.start", "workflow.status", "agent.result"
    }


def test_a2a_agent_result_is_acknowledged():
    response = client.post("/a2a/v1/tasks", json={
        "task_type": "agent.result",
        "context_id": "project-a2a",
        "agent_name": "code-quality",
        "payload": {"findings_count": 1},
    })

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert response.json()["result"]["agent_name"] == "code-quality"


def test_a2a_status_accepts_context_id():
    client.post("/a2a/v1/tasks", json={
        "task_type": "agent.result",
        "context_id": "project-status",
        "agent_name": "security",
        "payload": {"findings": []},
    })
    response = client.post("/a2a/v1/tasks", json={
        "task_type": "workflow.status",
        "context_id": "project-status",
    })

    assert response.status_code == 200
    assert response.json()["status"] == "completed"


def test_a2a_status_follows_workflow_record(monkeypatch):
    project_id = f"project-{uuid4().hex}"
    workflow_id = f"run-{uuid4().hex}"
    monkeypatch.setattr(main, "_schedule_workflow", lambda _request, _workflow_id: None)
    with TestClient(app) as test_client:
        started = test_client.post("/a2a/v1/tasks", json={
            "task_type": "workflow.start",
            "context_id": project_id,
            "payload": {
                "project_id": project_id,
                "workflow_id": workflow_id,
                "files": [{"path": "main.py", "content": "print(1)"}],
            },
        })
        task_id = started.json()["task_id"]
        main.records[workflow_id].update({"status": "completed", "validation_status": "failed"})
        main.run_store.upsert(workflow_id, main.records[workflow_id])
        status = test_client.post("/a2a/v1/tasks", json={
            "task_type": "workflow.status",
            "context_id": project_id,
            "payload": {"task_id": task_id},
        })

    assert started.status_code == 200
    assert status.status_code == 200
    assert status.json()["status"] == "completed"
    assert status.json()["result"]["validation_status"] == "failed"


def test_workflows_are_run_scoped_and_reject_overlap(monkeypatch):
    project_id = f"project-{uuid4().hex}"
    first_workflow_id = f"run-{uuid4().hex}"
    second_workflow_id = f"run-{uuid4().hex}"
    scheduled = []
    monkeypatch.setattr(main, "_schedule_workflow", lambda request, workflow_id: scheduled.append(workflow_id))

    with TestClient(app) as test_client:
        payload = {"project_id": project_id, "files": [{"path": "main.py", "content": "print(1)"}]}
        first = test_client.post("/workflow/start", json={**payload, "workflow_id": first_workflow_id})
        duplicate = test_client.post("/workflow/start", json={**payload, "workflow_id": second_workflow_id})

        assert first.status_code == 200
        assert duplicate.status_code == 409
        assert test_client.get(f"/workflow/status/{first_workflow_id}").json()["workflow_id"] == first_workflow_id

        main.records[first_workflow_id]["status"] = "completed"
        main.run_store.upsert(first_workflow_id, main.records[first_workflow_id])
        second = test_client.post("/workflow/start", json={**payload, "workflow_id": second_workflow_id})

        assert second.status_code == 200
        assert test_client.get(f"/workflow/status/{first_workflow_id}").json()["workflow_id"] == first_workflow_id
        assert test_client.get(f"/workflow/status/{second_workflow_id}").json()["workflow_id"] == second_workflow_id
    assert scheduled == [first_workflow_id, second_workflow_id]


def test_startup_marks_persisted_active_workflows_failed(monkeypatch):
    active = {"project_id": "p1", "workflow_id": "run-p1", "status": "working", "progress": "repair"}
    completed = {"project_id": "p2", "workflow_id": "run-p2", "status": "completed", "progress": "finalize"}

    class FakeRunStore:
        def __init__(self):
            self.records = {"run-p1": active.copy(), "run-p2": completed.copy()}

        def all(self):
            return list(self.records.values())

        def upsert(self, workflow_id, record):
            self.records[workflow_id] = record.copy()

    store = FakeRunStore()
    monkeypatch.setattr(main, "run_store", store)
    monkeypatch.setattr(main, "records", {})

    assert main.recover_interrupted_workflows() == 1
    assert store.records["run-p1"]["status"] == "failed"
    assert store.records["run-p1"]["failed_stage"] == "repair"
    assert "restarted" in store.records["run-p1"]["error"]
    assert store.records["run-p2"]["status"] == "completed"


def test_cancel_workflow_records_cancelled_without_passing_validation(monkeypatch):
    workflow_id = f"run-{uuid4().hex}"
    monkeypatch.setattr(main, "workflow_tasks", {})
    monkeypatch.setattr(main, "_schedule_workflow", lambda _request, _workflow_id: None)
    with TestClient(app) as test_client:
        started = test_client.post("/workflow/start", json={
            "project_id": f"project-{uuid4().hex}",
            "workflow_id": workflow_id,
            "files": [{"path": "main.py", "content": "print(1)"}],
        })
        assert started.status_code == 200
        cancelled = test_client.post(f"/workflow/cancel/{workflow_id}")

    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["validation_status"] == "inconclusive"


def test_workflow_stream_terminates_after_cancellation(monkeypatch):
    workflow_id = f"run-{uuid4().hex}"
    monkeypatch.setattr(main, "_record", lambda _workflow_id: {"status": "cancelled"})

    async def collect_events():
        response = await main.workflow_stream(workflow_id)
        return [event async for event in response.body_iterator]

    events = asyncio.run(asyncio.wait_for(collect_events(), timeout=1))

    assert len(events) == 1
    assert '"status": "cancelled"' in events[0]


def test_workflow_stream_sends_only_one_snapshot_when_state_is_unchanged(monkeypatch):
    workflow_id = f"run-{uuid4().hex}"
    reads = 0

    def current_record(_workflow_id):
        nonlocal reads
        reads += 1
        if reads > 2:
            main.records[workflow_id]["status"] = "completed"
        return main.records[workflow_id]

    monkeypatch.setattr(main, "records", {
        workflow_id: {
            "workflow_id": workflow_id,
            "status": "working",
            "progress": "report_recommend",
        }
    })
    monkeypatch.setattr(main, "_record", current_record)

    async def collect_events():
        response = await main.workflow_stream(workflow_id)
        return [event async for event in response.body_iterator]

    events = asyncio.run(asyncio.wait_for(collect_events(), timeout=3))

    assert len(events) == 2
    assert all(event.startswith("data: ") for event in events)