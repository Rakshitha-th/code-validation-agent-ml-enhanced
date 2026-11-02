from enum import Enum
from pydantic import BaseModel, Field


class ProjectStatus(str, Enum):
    UPLOADED = "uploaded"
    ANALYZING = "analyzing"
    REPAIRING = "repairing"
    VALIDATING = "validating"
    COMPLETED = "completed"
    FAILED = "failed"


class ProjectMeta(BaseModel):
    project_id: str
    name: str
    status: ProjectStatus = ProjectStatus.UPLOADED
    file_count: int = 0
    created_at: str = Field(default_factory=lambda: __import__("datetime").datetime.utcnow().isoformat())
