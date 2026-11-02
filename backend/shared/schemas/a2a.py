"""A small A2A task contract shared by the orchestrator and agent adapters."""
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


class A2AMessage(BaseModel):
    role: Literal["user", "agent", "orchestrator", "system"]
    parts: list[dict[str, Any]] = Field(default_factory=list)


class A2ATaskRequest(BaseModel):
    task_type: Literal["workflow.start", "workflow.status", "agent.result"]
    context_id: str
    agent_name: str = "frontend"
    payload: dict[str, Any] = Field(default_factory=dict)
    message: A2AMessage | None = None


class A2ATaskResponse(BaseModel):
    task_id: str = Field(default_factory=lambda: f"task-{uuid4().hex}")
    context_id: str
    task_type: str
    status: Literal["submitted", "working", "completed", "failed", "cancelled"]
    result: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())