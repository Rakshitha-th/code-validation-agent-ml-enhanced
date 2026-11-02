"""
Common Finding contract.

Every analysis agent (Security, Code Quality, Bug/Resource) MUST return
findings in this shape so the Orchestrator's Finding Aggregator can merge
them without per-agent translation code.
"""
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class FindingCategory(str, Enum):
    SECURITY = "security"
    CODE_QUALITY = "code_quality"
    RESOURCE = "resource"
    BUG = "bug"
    STYLE = "style"
    MAINTAINABILITY = "maintainability"
    COMPLEXITY = "complexity"
    CONVENTION = "convention"


class FindingStatus(str, Enum):
    OPEN = "open"
    REPAIRED = "repaired"
    IGNORED = "ignored"


class Finding(BaseModel):
    finding_id: str = Field(..., description="e.g. QUAL-001, SEC-014")
    category: FindingCategory
    severity: Severity
    file: str
    line_number: Optional[int] = None
    issue: str
    description: str
    evidence: Optional[str] = None
    recommendation: Optional[str] = None
    status: FindingStatus = FindingStatus.OPEN
    tool: Optional[str] = None
    detected_by_tools: Optional[list[str]] = None
    subcategory: Optional[str] = None

    class Config:
        use_enum_values = True


class FindingsResponse(BaseModel):
    """Standard response envelope every agent service returns."""
    project_id: str
    agent: str
    findings: list[Finding] = Field(default_factory=list)
    findings_count: int = 0
    error: Optional[str] = None
    quality_summary: Optional[dict] = None
