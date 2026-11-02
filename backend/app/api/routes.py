from fastapi import APIRouter, UploadFile, File, Form

from app.models.schemas import (
    FixRequest,
    FixResponse,
    ApplyResponse,
    ValidationResponse
)

from app.services.fix_service import generate_fix, apply_fix
from app.services.validation_service import validate_fix


router = APIRouter()


@router.post("/analyze-fix", response_model=FixResponse)
async def analyze_fix(request: FixRequest):

    result = await generate_fix(
        code=request.code,
        finding=request.finding,
        severity=request.severity
    )

    return result


@router.post("/analyze-file", response_model=FixResponse)
async def analyze_file(
    file: UploadFile = File(...),
    finding: str = Form(...),
    severity: str = Form("medium")
):
    code_bytes = await file.read()
    code = code_bytes.decode("utf-8")

    result = await generate_fix(
        code=code,
        finding=finding,
        severity=severity
    )

    return result


@router.post("/apply-fix", response_model=ApplyResponse)
async def apply_approved_fix(
    file: UploadFile = File(...),
    fixed_code: str = Form(...),
    approved: bool = Form(...)
):

    # Explicit approval is required.
    if not approved:
        return {
            "status": "cancelled",
            "message": "Fix was not applied because user approval was not provided.",
            "file": file.filename or "unknown",
            "diff": ""
        }

    original_bytes = await file.read()
    original_code = original_bytes.decode("utf-8")

    output_path, diff = apply_fix(
        original_code=original_code,
        fixed_code=fixed_code,
        filename=file.filename or "unknown.py"
    )

    return {
        "status": "applied",
        "message": "Approved fix was applied to a new corrected file.",
        "file": output_path,
        "diff": diff
    }


@router.post("/revalidate-file", response_model=ValidationResponse)
async def revalidate_file(
    file: UploadFile = File(...),
    finding: str = Form(...),
    severity: str = Form("medium")
):
    code_bytes = await file.read()
    code = code_bytes.decode("utf-8")

    result = await validate_fix(
        code=code,
        finding=finding,
        severity=severity
    )

    return result