from typing import Literal
from uuid import uuid4

from argon2 import PasswordHasher
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models import UserRecord
from app.security import get_current_user, require_roles

router = APIRouter(
    prefix="/api/admin/users",
    tags=["administration"],
    dependencies=[Depends(require_roles("Administrator"))],
)
ROLES = ("Developer", "Reviewer", "Technical Lead", "Administrator")
password_hasher = PasswordHasher()


def _is_valid_email(value: str) -> bool:
    if value.count("@") != 1 or any(character.isspace() for character in value):
        return False
    local, domain = value.split("@")
    domain_name, separator, top_level_domain = domain.rpartition(".")
    return bool(local and domain_name and separator and top_level_domain)


class CreateUserRequest(BaseModel):
    full_name: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=3, max_length=320)
    role: Literal["Developer", "Reviewer", "Technical Lead", "Administrator"]
    password: str = Field(min_length=12, max_length=128)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not _is_valid_email(normalized):
            raise ValueError("Enter a valid email address")
        return normalized

    @field_validator("full_name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Full name is required")
        return normalized


class UpdateUserRequest(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=120)
    email: str | None = Field(default=None, min_length=3, max_length=320)
    role: Literal["Developer", "Reviewer", "Technical Lead", "Administrator"] | None = None
    active: bool | None = None

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        if not _is_valid_email(normalized):
            raise ValueError("Enter a valid email address")
        return normalized

    @field_validator("full_name")
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("Full name is required")
        return normalized


def _user_payload(user: UserRecord) -> dict:
    return {
        "id": user.user_id,
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role,
        "active": user.is_active,
    }


@router.get("")
async def list_users(session: AsyncSession = Depends(get_session)) -> dict:
    users = list((await session.scalars(select(UserRecord).order_by(UserRecord.email))).all())
    return {"items": [_user_payload(user) for user in users]}


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    responses={409: {"description": "An account with that email already exists"}},
)
async def create_user(request: CreateUserRequest, session: AsyncSession = Depends(get_session)) -> dict:
    user = UserRecord(
        user_id=str(uuid4()),
        email=request.email,
        full_name=request.full_name,
        password_hash=password_hasher.hash(request.password),
        role=request.role,
        is_active=True,
    )
    session.add(user)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="An account with that email already exists") from exc
    return _user_payload(user)


@router.patch(
    "/{user_id}",
    responses={
        404: {"description": "User not found"},
        409: {"description": "The account update conflicts with an administration rule or email already exists"},
        422: {"description": "At least one user field must be changed"},
    },
)
async def update_user(
    user_id: str,
    changes: UpdateUserRequest,
    session: AsyncSession = Depends(get_session),
    current_user: UserRecord = Depends(get_current_user),
) -> dict:
    user = await session.get(UserRecord, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    patch = changes.model_dump(exclude_unset=True)
    if not patch:
        raise HTTPException(status_code=422, detail="At least one user field must be changed")

    removes_admin = user.role == "Administrator" and (
        patch.get("role", user.role) != "Administrator" or patch.get("active", user.is_active) is False
    )
    if removes_admin:
        active_admins = await session.scalar(
            select(func.count()).select_from(UserRecord).where(
                UserRecord.role == "Administrator", UserRecord.is_active.is_(True)
            )
        ) or 0
        if active_admins <= 1:
            raise HTTPException(status_code=409, detail="The last active administrator cannot be demoted or disabled")
    if user.user_id == current_user.user_id and patch.get("active") is False:
        raise HTTPException(status_code=409, detail="You cannot deactivate your own account")

    for key, value in patch.items():
        setattr(user, "is_active" if key == "active" else key, value)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="An account with that email already exists") from exc
    return _user_payload(user)