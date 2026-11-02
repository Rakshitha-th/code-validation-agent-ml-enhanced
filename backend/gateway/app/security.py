import hashlib
import secrets
from datetime import datetime, timezone
from typing import Callable

from fastapi import Cookie, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models import AuthSessionRecord, ProjectRecord, UserRecord

SESSION_COOKIE = "cva_session"


def _token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def get_current_user(
    token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    session: AsyncSession = Depends(get_session),
) -> UserRecord:
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    auth_session = await session.scalar(
        select(AuthSessionRecord).where(
            AuthSessionRecord.token_hash == _token_digest(token),
            AuthSessionRecord.expires_at > datetime.now(timezone.utc),
        )
    )
    user = await session.get(UserRecord, auth_session.user_id) if auth_session else None
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session is invalid or expired")
    return user


def require_roles(*roles: str) -> Callable:
    def check_role(user: UserRecord = Depends(get_current_user)) -> UserRecord:
        if user.role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
        return user

    return check_role


async def require_owned_project(
    session: AsyncSession,
    project_id: str,
    user: UserRecord,
) -> ProjectRecord:
    project = await session.scalar(
        select(ProjectRecord).where(
            ProjectRecord.project_id == project_id,
            ProjectRecord.owner_id == user.user_id,
        )
    )
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project


def create_session_token() -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    return token, _token_digest(token)