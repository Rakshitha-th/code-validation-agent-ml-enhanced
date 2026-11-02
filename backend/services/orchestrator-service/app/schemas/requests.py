from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator
from shared.languages import (
    SOURCE_LANGUAGES,
    detect_language,
    is_project_manifest,
    strip_markdown_code_fence,
    validate_project_path,
)


class ProjectFileIn(BaseModel):
    path: str
    content: str

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return validate_project_path(value)

    @field_validator("content")
    @classmethod
    def strip_wrapping_code_fence(cls, value: str, info: ValidationInfo) -> str:
        path = info.data.get("path")
        return strip_markdown_code_fence(path, value) if path else value


class StartAnalysisRequest(BaseModel):
    project_id: str
    workflow_id: str | None = None
    files: list[ProjectFileIn]
    language: str | None = None
    project_type: str | None = None
    project_context: dict = Field(default_factory=dict)
    tools: list[str] | None = None

    @field_validator("project_id")
    @classmethod
    def validate_project_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("project_id must not be empty")
        return value

    @field_validator("files")
    @classmethod
    def validate_files(cls, value: list[ProjectFileIn]) -> list[ProjectFileIn]:
        if not value:
            raise ValueError("at least one project file is required")
        paths = [item.path for item in value]
        if len(paths) != len(set(paths)):
            raise ValueError("project files must have unique paths")
        try:
            language = detect_language([path for path in paths if not is_project_manifest(path)])
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        if language is None:
            raise ValueError("at least one supported source file is required for analysis")
        return value

    @model_validator(mode="after")
    def validate_explicit_language(self):
        paths = [item.path for item in self.files if not is_project_manifest(item.path)]
        detected = detect_language(paths)
        explicit = self.language or self.project_context.get("language")
        if explicit:
            explicit = explicit.strip().lower()
            if explicit not in SOURCE_LANGUAGES:
                raise ValueError(f"Unsupported language {explicit!r}.")
            if detected != explicit:
                raise ValueError(
                    f"Selected language {explicit!r} does not match detected source language {detected!r}."
                )
        project_type = self.project_type or self.project_context.get("project_type")
        if project_type not in {None, "single_file", "dotnet"}:
            raise ValueError(f"Unsupported project type {project_type!r}.")
        if project_type == "dotnet" and (
            detected != "csharp"
            or not any(path.lower().endswith(".csproj") for path in self.files)
        ):
            raise ValueError("A .NET project requires C# source files and a .csproj manifest.")
        return self


class WorkflowRunResponse(BaseModel):
    project_id: str
    validation_status: str
    retry_count: int
    findings: list[dict]
    final_report: dict
