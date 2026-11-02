import asyncio
import smtplib
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient

from app.main import app
from app.database import SessionFactory
from app.config import settings
from app.models import PasswordResetTokenRecord, UserRecord
from app.routes import auth
from sqlalchemy import select


def test_signup_login_logout_and_password_hash():
    email = f"auth-test-{uuid4().hex}@example.test"
    with TestClient(app, base_url="https://testserver") as client:
        registered = client.post("/api/auth/register", json={
            "email": email,
            "full_name": "Auth Test User",
            "password": "correct-horse-battery-42",
        })
        assert registered.status_code == 201
        assert registered.json()["user"]["role"] == "Developer"
        assert "cva_session" in registered.headers.get("set-cookie", "")
        assert client.get("/api/auth/me").json()["user"]["email"] == email

        user_id = registered.json()["user"]["id"]
        with TestClient(app, base_url="https://testserver") as anonymous_client:
            denied = anonymous_client.get("/api/projects")
            assert denied.status_code == 401

        logged_out = client.post("/api/auth/logout")
        assert logged_out.status_code == 204
        assert client.get("/api/auth/me").status_code == 401
        assert client.post("/api/auth/login", json={
            "email": email,
            "password": "correct-horse-battery-42",
        }).status_code == 200

    async def read_password_hash():
        async with SessionFactory() as session:
            return await session.scalar(select(UserRecord.password_hash).where(UserRecord.user_id == user_id))

    stored_hash = asyncio.run(read_password_hash())
    assert stored_hash is not None
    assert stored_hash != "correct-horse-battery-42"
    assert stored_hash.startswith("$argon2")


def test_duplicate_registration_and_invalid_login_are_rejected():
    email = f"duplicate-test-{uuid4().hex}@example.test"
    with TestClient(app, base_url="https://testserver") as client:
        body = {"email": email, "full_name": "Duplicate User", "password": "correct-horse-battery-42"}
        assert client.post("/api/auth/register", json=body).status_code == 201
        assert client.post("/api/auth/register", json=body).status_code == 409
        assert client.post("/api/auth/login", json={"email": email, "password": "not-the-password"}).status_code == 401


def test_registration_rejects_invalid_fields():
    with TestClient(app, base_url="https://testserver") as client:
        short_password = client.post("/api/auth/register", json={
            "email": f"short-{uuid4().hex}@example.test",
            "full_name": "Invalid Password",
            "password": "short",
        })
        invalid_email = client.post("/api/auth/register", json={
            "email": "not-an-email",
            "full_name": "Invalid Email",
            "password": "valid-password-123",
        })
    assert short_password.status_code == invalid_email.status_code == 422


def test_projects_are_isolated_by_owner():
    with TestClient(app, base_url="https://testserver") as owner, TestClient(app, base_url="https://testserver") as other:
        owner_signup = owner.post("/api/auth/register", json={
            "email": f"owner-{uuid4().hex}@example.test",
            "full_name": "Project Owner",
            "password": "correct-horse-battery-42",
        })
        other_signup = other.post("/api/auth/register", json={
            "email": f"other-{uuid4().hex}@example.test",
            "full_name": "Other User",
            "password": "correct-horse-battery-42",
        })
        assert owner_signup.status_code == other_signup.status_code == 201
        created = owner.post("/api/projects", json={
            "name": "private project",
            "files": [{"path": "main.py", "content": "print('private')\n"}],
        })
        assert created.status_code == 200
        project_id = created.json()["project_id"]
        assert other.get(f"/api/projects/{project_id}").status_code == 404
        assert other.get("/api/projects").json()["total"] == 0


def test_bootstrap_admin_can_manage_users_and_developer_cannot(monkeypatch):
    admin_email = f"bootstrap-{uuid4().hex}@example.test"
    monkeypatch.setattr(settings, "bootstrap_admin_email", admin_email)
    monkeypatch.setattr(settings, "bootstrap_admin_password", "bootstrap-password-long-42")
    with TestClient(app, base_url="https://testserver") as admin_client:
        admin_login = admin_client.post("/api/auth/login", json={
            "email": admin_email,
            "password": "bootstrap-password-long-42",
            "remember": True,
        })
        assert admin_login.status_code == 200
        assert admin_login.json()["user"]["role"] == "Administrator"

        created = admin_client.post("/api/admin/users", json={
            "full_name": "Review Lead",
            "email": f"lead-{uuid4().hex}@example.test",
            "role": "Technical Lead",
            "password": "temporary-password-long-42",
        })
        assert created.status_code == 201
        assert created.json()["role"] == "Technical Lead"
        assert "password" not in created.json()
        assert "password_hash" not in created.json()

        developer_email = f"developer-{uuid4().hex}@example.test"
        with TestClient(app, base_url="https://testserver") as developer_client:
            assert developer_client.post("/api/auth/register", json={
                "email": developer_email,
                "full_name": "Regular Developer",
                "password": "developer-password-long-42",
            }).status_code == 201
            assert developer_client.get("/api/admin/users").status_code == 403


def test_forgot_password_reset_expiry_and_one_time_use(monkeypatch):
    email = f"reset-{uuid4().hex}@example.test"
    sent_messages = []
    monkeypatch.setattr(settings, "smtp_host", "mail.example.test")
    monkeypatch.setattr(settings, "smtp_from_email", "noreply@example.test")
    monkeypatch.setattr(settings, "frontend_public_url", "https://app.example.test")
    monkeypatch.setattr(auth, "_send_password_reset_email", lambda address, link: sent_messages.append((address, link)))

    with TestClient(app, base_url="https://testserver") as client:
        registered = client.post("/api/auth/register", json={
            "email": email,
            "full_name": "Reset User",
            "password": "original-password-123",
        })
        assert registered.status_code == 201
        old_session = client.cookies.get("cva_session")

        request = client.post("/api/auth/forgot-password", json={"email": email})
        assert request.status_code == 202
        assert email not in request.text
        assert sent_messages[0][0] == email
        token = parse_qs(urlsplit(sent_messages[0][1]).fragment)["reset-token"][0]

        changed = client.post("/api/auth/reset-password", json={
            "token": token,
            "new_password": "updated-password-456",
        })
        assert changed.status_code == 200
        assert client.get("/api/auth/me").status_code == 401
        assert client.post("/api/auth/reset-password", json={
            "token": token,
            "new_password": "another-password-789",
        }).status_code == 400
        assert client.post("/api/auth/login", json={
            "email": email,
            "password": "original-password-123",
        }).status_code == 401
        assert client.post("/api/auth/login", json={
            "email": email,
            "password": "updated-password-456",
        }).status_code == 200
        assert old_session != client.cookies.get("cva_session")

        assert client.post("/api/auth/forgot-password", json={"email": email}).status_code == 202
        expired_token = parse_qs(urlsplit(sent_messages[1][1]).fragment)["reset-token"][0]

        async def expire_reset_token():
            async with SessionFactory() as session:
                token_record = await session.scalar(
                    select(PasswordResetTokenRecord).where(
                        PasswordResetTokenRecord.token_hash == auth._token_digest(expired_token)
                    )
                )
                assert token_record is not None
                token_record.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
                await session.commit()

        asyncio.run(expire_reset_token())
        assert client.post("/api/auth/reset-password", json={
            "token": expired_token,
            "new_password": "expired-password-123",
        }).status_code == 400

        unknown = client.post("/api/auth/forgot-password", json={
            "email": f"unknown-{uuid4().hex}@example.test",
        })
        assert unknown.status_code == request.status_code
        assert unknown.json() == request.json()
        assert len(sent_messages) == 2


def test_authenticated_password_change_checks_current_password_and_rotates_sessions():
    email = f"change-{uuid4().hex}@example.test"
    with TestClient(app, base_url="https://testserver") as client:
        assert client.post("/api/auth/register", json={
            "email": email,
            "full_name": "Password Change User",
            "password": "original-password-123",
        }).status_code == 201
        session_before = client.cookies.get("cva_session")
        incorrect = client.post("/api/auth/change-password", json={
            "current_password": "incorrect-password",
            "new_password": "updated-password-456",
        })
        assert incorrect.status_code == 400
        assert client.cookies.get("cva_session") == session_before

        changed = client.post("/api/auth/change-password", json={
            "current_password": "original-password-123",
            "new_password": "updated-password-456",
        })
        assert changed.status_code == 200
        assert client.cookies.get("cva_session") != session_before
        assert client.get("/api/auth/me").status_code == 200
        client.post("/api/auth/logout")
        assert client.post("/api/auth/login", json={
            "email": email,
            "password": "original-password-123",
        }).status_code == 401
        assert client.post("/api/auth/login", json={
            "email": email,
            "password": "updated-password-456",
        }).status_code == 200


def test_authentication_rate_limits_repeated_failed_logins():
    email = f"rate-limit-{uuid4().hex}@example.test"
    with TestClient(app, base_url="https://testserver") as client:
        for _ in range(10):
            assert client.post("/api/auth/login", json={
                "email": email,
                "password": "incorrect-password",
            }).status_code == 401
        limited = client.post("/api/auth/login", json={
            "email": email,
            "password": "incorrect-password",
        })
        assert limited.status_code == 429


def test_forgot_password_reports_missing_smtp_configuration(monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", None)
    monkeypatch.setattr(settings, "smtp_from_email", None)
    with TestClient(app, base_url="https://testserver") as client:
        response = client.post("/api/auth/forgot-password", json={
            "email": f"missing-smtp-{uuid4().hex}@example.test",
        })
    assert response.status_code == 503
    assert "temporarily unavailable" in response.json()["detail"]


def test_reset_request_does_not_disclose_email_or_delivery_failure(monkeypatch):
    email = f"mail-failure-{uuid4().hex}@example.test"
    monkeypatch.setattr(settings, "smtp_host", "mail.example.test")
    monkeypatch.setattr(settings, "smtp_from_email", "noreply@example.test")

    def fail_delivery(_address, _link):
        raise smtplib.SMTPException("delivery unavailable")

    monkeypatch.setattr(auth, "_send_password_reset_email", fail_delivery)
    with TestClient(app, base_url="https://testserver") as client:
        assert client.post("/api/auth/register", json={
            "email": email,
            "full_name": "Mail Failure User",
            "password": "original-password-123",
        }).status_code == 201
        registered = client.post("/api/auth/forgot-password", json={"email": email})
        unknown = client.post("/api/auth/forgot-password", json={
            "email": f"not-registered-{uuid4().hex}@example.test",
        })

    assert registered.status_code == unknown.status_code == 202
    assert registered.json() == unknown.json()