import asyncio
import hashlib
import logging
import re
import secrets
import smtplib
import socket
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from uuid import uuid4
from urllib.parse import quote

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import SessionFactory, get_session
from app.models import AuthSessionRecord, PasswordResetTokenRecord, UserRecord
from app.security import SESSION_COOKIE, _token_digest, create_session_token, get_current_user

router = APIRouter(prefix="/api/auth", tags=["authentication"])
password_hasher = PasswordHasher()
logger = logging.getLogger(__name__)
_rate_limit_lock = threading.Lock()
_rate_limit_attempts: dict[str, deque[float]] = defaultdict(deque)


def _rate_limit(request: Request, action: str, subject: str, limit: int, window_seconds: int) -> None:
    client_host = request.client.host if request.client else "unknown"
    subject_digest = hashlib.sha256(subject.strip().lower().encode("utf-8")).hexdigest()
    keys = (
        (f"{action}:ip:{client_host}", limit * 3),
        (f"{action}:subject:{subject_digest}", limit),
    )
    now = time.monotonic()
    with _rate_limit_lock:
        active_attempts = []
        for key, key_limit in keys:
            attempts = _rate_limit_attempts[key]
            while attempts and attempts[0] <= now - window_seconds:
                attempts.popleft()
            if len(attempts) >= key_limit:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Too many attempts. Please wait before trying again.",
                    headers={"Retry-After": str(max(1, int(window_seconds - (now - attempts[0]))))},
                )
            active_attempts.append(attempts)
        for attempts in active_attempts:
            attempts.append(now)
        if len(_rate_limit_attempts) > 10_000:
            expired_before = now - window_seconds
            for existing_key in list(_rate_limit_attempts):
                bucket = _rate_limit_attempts[existing_key]
                while bucket and bucket[0] <= expired_before:
                    bucket.popleft()
                if not bucket:
                    del _rate_limit_attempts[existing_key]


class RegisterRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    full_name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=12, max_length=128)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", normalized):
            raise ValueError("Enter a valid email address")
        return normalized

    @field_validator("full_name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Full name is required")
        return normalized


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=128)
    remember: bool = False

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", normalized):
            raise ValueError("Enter a valid email address")
        return normalized


class ForgotPasswordRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", normalized):
            raise ValueError("Enter a valid email address")
        return normalized


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=20, max_length=256)
    new_password: str = Field(min_length=12, max_length=128)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=12, max_length=128)


def _smtp_configured() -> bool:
    return bool(
        settings.smtp_host
        and settings.smtp_from_email
        and (bool(settings.smtp_username) == bool(settings.smtp_password))
        and not (settings.smtp_use_tls and settings.smtp_use_ssl)
    )


def _send_password_reset_email(email: str, reset_url: str) -> None:
    message = EmailMessage()
    message["Subject"] = "Reset your Coding Validation Agent password"
    message["From"] = settings.smtp_from_email
    message["To"] = email
    message.set_content(
        "We received a request to reset the password for your Coding Validation Agent account.\n\n"
        f"Use this one-time link within {settings.auth_reset_token_minutes} minutes:\n{reset_url}\n\n"
        "If you did not request this, you can ignore this message."
    )
    if settings.smtp_use_ssl:
        with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
            if settings.smtp_username:
                smtp.login(settings.smtp_username, settings.smtp_password or "")
            smtp.send_message(message)
        return
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
        if settings.smtp_use_tls:
            smtp.starttls()
        if settings.smtp_username:
            smtp.login(settings.smtp_username, settings.smtp_password or "")
        smtp.send_message(message)


def _user_payload(user: UserRecord) -> dict:
    return {
        "id": user.user_id,
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role,
        "active": user.is_active,
    }


async def _issue_session(
    user: UserRecord,
    response: Response,
    session: AsyncSession,
    remember: bool,
) -> dict:
    now = datetime.now(timezone.utc)
    await session.execute(delete(AuthSessionRecord).where(AuthSessionRecord.expires_at <= now))
    duration = timedelta(days=settings.auth_remember_days) if remember else timedelta(hours=settings.auth_session_hours)
    token, token_hash = create_session_token()
    session.add(AuthSessionRecord(
        session_id=str(uuid4()),
        user_id=user.user_id,
        token_hash=token_hash,
        expires_at=now + duration,
    ))
    await session.commit()
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(duration.total_seconds()) if remember else None,
        expires=now + duration if remember else None,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="lax",
        path="/",
    )
    return {"user": _user_payload(user)}


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(
    request: RegisterRequest,
    response: Response,
    http_request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    _rate_limit(http_request, "register", request.email, 10, 900)
    existing = await session.scalar(select(UserRecord.user_id).where(UserRecord.email == request.email))
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="An account with that email already exists")

    user = UserRecord(
        user_id=str(uuid4()),
        email=request.email,
        full_name=request.full_name,
        password_hash=password_hasher.hash(request.password),
        role="Developer",
        is_active=True,
    )
    session.add(user)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="An account with that email already exists") from exc
    return await _issue_session(user, response, session, remember=False)


@router.post("/login")
async def login(
    request: LoginRequest,
    response: Response,
    http_request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    _rate_limit(http_request, "login", request.email, 10, 900)
    user = await session.scalar(select(UserRecord).where(UserRecord.email == request.email))
    valid = False
    if user is not None and user.is_active:
        try:
            valid = password_hasher.verify(user.password_hash, request.password)
        except VerificationError:
            valid = False
    if not valid or user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    if password_hasher.check_needs_rehash(user.password_hash):
        user.password_hash = password_hasher.hash(request.password)
    return await _issue_session(user, response, session, request.remember)


@router.post("/demo")
async def local_demo(response: Response, session: AsyncSession = Depends(get_session)) -> dict:
    if not settings.local_demo_auth_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Local demo authentication is disabled")
    user = await session.scalar(select(UserRecord).where(UserRecord.email == "demo@local.invalid"))
    if user is None:
        user = UserRecord(
            user_id=str(uuid4()),
            email="demo@local.invalid",
            full_name="Local Demo",
            password_hash=password_hasher.hash(str(uuid4())),
            role="Developer",
            is_active=True,
        )
        session.add(user)
        await session.commit()
    return await _issue_session(user, response, session, remember=False)


async def ensure_bootstrap_admin() -> None:
    if not settings.bootstrap_admin_email and not settings.bootstrap_admin_password:
        return
    if not settings.bootstrap_admin_email or not settings.bootstrap_admin_password:
        raise RuntimeError("Both GATEWAY_BOOTSTRAP_ADMIN_EMAIL and GATEWAY_BOOTSTRAP_ADMIN_PASSWORD must be set")
    if len(settings.bootstrap_admin_password) < 12:
        raise RuntimeError("The bootstrap administrator password must contain at least 12 characters")

    email = settings.bootstrap_admin_email.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise RuntimeError("GATEWAY_BOOTSTRAP_ADMIN_EMAIL must be a valid email address")

    async with SessionFactory() as session:
        user = await session.scalar(select(UserRecord).where(UserRecord.email == email))
        if user is None:
            session.add(UserRecord(
                user_id=str(uuid4()),
                email=email,
                full_name=settings.bootstrap_admin_name.strip() or "System Administrator",
                password_hash=password_hasher.hash(settings.bootstrap_admin_password),
                role="Administrator",
                is_active=True,
            ))
            await session.commit()


@router.get("/me")
async def current_user(user: UserRecord = Depends(get_current_user)) -> dict:
    return {"user": _user_payload(user)}


@router.post("/forgot-password", status_code=status.HTTP_202_ACCEPTED)
async def forgot_password(
    request: ForgotPasswordRequest,
    http_request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    _rate_limit(http_request, "forgot-password", request.email, 5, 3600)
    if not _smtp_configured():
        logger.error("Password reset requested but SMTP host or sender address is not configured")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Password reset email is temporarily unavailable. Please contact your administrator.",
        )

    user = await session.scalar(select(UserRecord).where(UserRecord.email == request.email))
    if user is not None and user.is_active:
        now = datetime.now(timezone.utc)
        await session.execute(delete(PasswordResetTokenRecord).where(PasswordResetTokenRecord.user_id == user.user_id))
        token = secrets.token_urlsafe(32)
        reset_record = PasswordResetTokenRecord(
            reset_id=str(uuid4()),
            user_id=user.user_id,
            token_hash=_token_digest(token),
            expires_at=now + timedelta(minutes=settings.auth_reset_token_minutes),
        )
        session.add(reset_record)
        await session.commit()
        reset_url = (
            f"{settings.frontend_public_url.rstrip('/')}/login"
            f"#reset-token={quote(token)}"
        )
        try:
            await asyncio.to_thread(_send_password_reset_email, user.email, reset_url)
        except (OSError, smtplib.SMTPException, socket.timeout):
            logger.exception("Password reset email delivery failed")
            await session.execute(
                delete(PasswordResetTokenRecord).where(
                    PasswordResetTokenRecord.reset_id == reset_record.reset_id
                )
            )
            await session.commit()
    return {"message": "If this email belongs to an active account, reset instructions will be sent if email delivery is available."}


@router.post("/reset-password")
async def reset_password(
    request: ResetPasswordRequest,
    response: Response,
    http_request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    _rate_limit(http_request, "reset-password", request.token, 10, 3600)
    token_hash = _token_digest(request.token)
    reset_record = await session.scalar(
        select(PasswordResetTokenRecord)
        .where(PasswordResetTokenRecord.token_hash == token_hash)
        .with_for_update()
    )
    now = datetime.now(timezone.utc)
    expires_at = reset_record.expires_at if reset_record else None
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if reset_record is None or reset_record.used_at is not None or expires_at <= now:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Reset link is invalid or expired.")

    user = await session.get(UserRecord, reset_record.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Reset link is invalid or expired.")
    user.password_hash = password_hasher.hash(request.new_password)
    reset_record.used_at = now
    await session.execute(delete(AuthSessionRecord).where(AuthSessionRecord.user_id == user.user_id))
    await session.commit()
    response.delete_cookie(
        SESSION_COOKIE,
        path="/",
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="lax",
    )
    return {"message": "Password changed. Please sign in with your new password."}


@router.post("/change-password")
async def change_password(
    request: ChangePasswordRequest,
    response: Response,
    token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    user: UserRecord = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    try:
        current_password_valid = password_hasher.verify(user.password_hash, request.current_password)
    except VerificationError:
        current_password_valid = False
    if not current_password_valid:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect.")
    if request.current_password == request.new_password:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Choose a password different from your current password.")

    user.password_hash = password_hasher.hash(request.new_password)
    await session.execute(delete(AuthSessionRecord).where(AuthSessionRecord.user_id == user.user_id))
    result = await _issue_session(user, response, session, remember=False)
    return {**result, "message": "Password changed successfully."}


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    session: AsyncSession = Depends(get_session),
) -> None:
    if token:
        await session.execute(delete(AuthSessionRecord).where(AuthSessionRecord.token_hash == _token_digest(token)))
        await session.commit()
    response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, secure=settings.auth_cookie_secure, samesite="lax")
