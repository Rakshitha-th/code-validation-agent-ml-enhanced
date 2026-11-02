from fastapi import APIRouter

from app.models import RepairRequest, RepairResponse
from app.services.repair_engine import repair_project


router = APIRouter()


@router.post("/repair")
async def repair_code(request: RepairRequest) -> RepairResponse:
    return await repair_project(request)