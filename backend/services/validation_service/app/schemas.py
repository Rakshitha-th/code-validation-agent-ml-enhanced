from pydantic import BaseModel, Field
from typing import List, Optional


class ValidationRequest(BaseModel):
    code: str = ""
    filename: str = "main.py"
    files: dict[str, str] = Field(default_factory=dict)


class ValidationFinding(BaseModel):
    type: str
    severity: str
    file: str
    line: Optional[int] = None
    column: Optional[int] = None
    message: str
    code: Optional[str] = None
    recommendation: str


class ValidationMetrics(BaseModel):
    lines_of_code: int
    functions: int
    classes: int
    imports: int
    max_function_length: int
    max_nesting_depth: int


class ValidationResponse(BaseModel):
    status: str
    filename: str
    valid: bool
    passed: bool
    needs_review: bool = False
    total_findings: int
    findings: List[ValidationFinding]
    metrics: Optional[ValidationMetrics] = None
    language: Optional[str] = None
    validator: Optional[str] = None
    sandbox_result: Optional[dict] = None