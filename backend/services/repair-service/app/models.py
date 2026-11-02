from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from shared.languages import detect_language, validate_project_path


class Finding(BaseModel):
    finding_id: str | None = None
    file: str
    line: int | None = None
    rule_id: str | None = None
    type: str = "bug"
    category: str | None = None
    severity: str = "medium"
    message: str
    recommendation: str | None = None
    evidence: str | None = None
    tool: str | None = None
    subcategory: str | None = None

    @field_validator("file")
    @classmethod
    def validate_file(cls, file: str) -> str:
        return validate_project_path(file)


class CodeFile(BaseModel):
    path: str
    content: str


class RepairRequest(BaseModel):
    project_id: str = "local-project"
    language: str = "python"
    files: list[CodeFile] = Field(min_length=1)
    findings: list[Finding] = Field(default_factory=list)
    repair_feedback: str = ""

    @field_validator("files")
    @classmethod
    def validate_files(cls, files: list[CodeFile]) -> list[CodeFile]:
        paths = [validate_project_path(file.path) for file in files]
        if len(paths) != len(set(paths)):
            raise ValueError("Project files must have unique paths")
        return [CodeFile(path=path, content=file.content) for path, file in zip(paths, files)]

    @model_validator(mode="after")
    def validate_language(self):
        detected = detect_language([file.path for file in self.files])
        if detected is None or detected != self.language.lower():
            raise ValueError("The requested repair language must match the submitted source files.")
        return self


class FileRepair(BaseModel):
    path: str
    original_code: str
    regenerated_code: str
    changed: bool
    explanation: str
    resolved_finding_ids: list[str] = Field(default_factory=list)
    candidate_code: str | None = None
    skipped_checks: list[str] = Field(default_factory=list)


class RepairResponse(BaseModel):
    status: Literal["completed", "partial", "failed"]
    repair_status: Literal["success", "partial", "failed", "needs_review", "not_run", "running"]
    outcome: Literal["applied", "partially_applied", "no_change"] = "no_change"
    changed_files: list[str] = Field(default_factory=list)
    repairs: list[FileRepair]
    unresolved_findings: list[Finding]
    validation_status: str
    skipped_checks: list[str] = Field(default_factory=list)