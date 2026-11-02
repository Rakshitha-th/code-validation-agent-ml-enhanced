from pydantic import BaseModel


class FixRequest(BaseModel):
    code: str
    finding: str
    severity: str = "medium"


class FixResponse(BaseModel):
    problem: str
    explanation: str
    recommendation: str
    fixed_code: str


class ApplyResponse(BaseModel):
    status: str
    message: str
    file: str
    diff: str


class ValidationResponse(BaseModel):
    status: str
    explanation: str
    remaining_issue: str