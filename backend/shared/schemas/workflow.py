"""
Shared workflow / project contracts used across the orchestrator and
downstream services (repair, sandbox, validation).
"""
from typing import Optional
from pydantic import BaseModel, Field


class ProjectFile(BaseModel):
    path: str
    content: str


class AnalysisRequest(BaseModel):
    """What the orchestrator sends to each analysis agent (Security,
    Code Quality, Bug/Resource)."""
    project_id: str
    files: list[ProjectFile]
    project_context: dict = Field(default_factory=dict)


class RepairRequest(BaseModel):
    project_id: str
    files: list[ProjectFile]
    findings: list[dict]
    repair_attempt: int = 1


class RepairResponse(BaseModel):
    project_id: str
    repaired_files: list[ProjectFile]
    changelog: list[str] = Field(default_factory=list)


class ValidationRequest(BaseModel):
    project_id: str
    files: list[ProjectFile]
    original_findings: list[dict] = Field(default_factory=list)


class ValidationResult(BaseModel):
    project_id: str
    passed: bool
    syntax_ok: bool = True
    tests_passed: Optional[int] = None
    tests_failed: Optional[int] = None
    remaining_findings: list[dict] = Field(default_factory=list)
    details: dict = Field(default_factory=dict)
