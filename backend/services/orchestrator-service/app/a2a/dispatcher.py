from typing import Any

from app.schemas.requests import StartAnalysisRequest
from shared.schemas.a2a import A2ATaskRequest, A2ATaskResponse


class A2ADispatcher:
    """Maps A2A tasks to orchestrator operations without coupling agents."""

    def __init__(self, records: dict[str, dict]):
        self.records = records

    def submit_workflow(self, task: A2ATaskRequest) -> tuple[A2ATaskResponse, StartAnalysisRequest]:
        payload = task.payload
        request = StartAnalysisRequest.model_validate(payload)
        response = A2ATaskResponse(
            context_id=task.context_id,
            task_type=task.task_type,
            status="submitted",
            result={"project_id": request.project_id},
        )
        self.records[response.task_id] = {
            "task_id": response.task_id,
            "context_id": task.context_id,
            "task_type": task.task_type,
            "status": response.status,
            "project_id": request.project_id,
        }
        return response, request

    def status(self, task: A2ATaskRequest) -> A2ATaskResponse:
        task_id = task.payload.get("task_id", "")
        record = self.records.get(task_id) if task_id else None
        if record is None:
            record = self.records.get(task.context_id)
        if record is None:
            record = next(
                (item for item in self.records.values() if item.get("project_id") == task.context_id),
                None,
            )
        if record is None:
            return A2ATaskResponse(
                context_id=task.context_id,
                task_type=task.task_type,
                status="failed",
                error="A2A task not found",
            )
        task_record = record
        workflow_id = record.get("workflow_id")
        if workflow_id and workflow_id in self.records:
            record = {**record, **self.records[workflow_id], "task_id": task_record.get("task_id")}
        return A2ATaskResponse(
            task_id=record.get("task_id", task.context_id),
            context_id=record.get("context_id", task.context_id),
            task_type=task.task_type,
            status=record.get("status", "working"),
            result=record,
        )

    def agent_result(self, task: A2ATaskRequest) -> A2ATaskResponse:
        result: dict[str, Any] = {
            "agent_name": task.agent_name,
            "payload": task.payload,
        }
        self.records[task.context_id] = {
            "task_id": task.context_id,
            "context_id": task.context_id,
            "task_type": task.task_type,
            "status": "completed",
            "agent_name": task.agent_name,
            "result": result,
        }
        return A2ATaskResponse(
            context_id=task.context_id,
            task_type=task.task_type,
            status="completed",
            result=result,
        )