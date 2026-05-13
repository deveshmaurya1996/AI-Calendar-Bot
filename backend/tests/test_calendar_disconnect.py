"""POST /api/auth/google/calendar/disconnect — admin-only, clears server refresh token file."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import auth_routes, deps
from app.config import Settings
from app.api.deps import SESSION_COOKIE_NAME
from app.services import google_credentials
from app.services.app_session import create_app_session_token


def _mini_app() -> FastAPI:
    app = FastAPI()
    app.include_router(auth_routes.auth_router, prefix="/api")
    return app


def _settings(tmp_path: Path, *, admin_emails: str) -> Settings:
    return Settings(
        POLLINATIONS_API_KEY="sk-test",
        GOOGLE_CLIENT_ID="id",
        GOOGLE_CLIENT_SECRET="secret",
        GOOGLE_REDIRECT_URI="http://localhost:8000/api/auth/google/callback",
        FRONTEND_PUBLIC_URL="http://localhost:3000",
        GOOGLE_CALENDAR_ID="primary",
        SECRET_KEY="0" * 32,
        GOOGLE_REFRESH_TOKEN_FILE=str(tmp_path / "refresh.txt"),
        GOOGLE_CALENDAR_ADMIN_EMAILS=admin_emails,
    )


def test_disconnect_removes_token_for_admin(monkeypatch, tmp_path: Path) -> None:
    s = _settings(tmp_path, admin_emails="admin@example.com")
    monkeypatch.setattr(auth_routes, "get_settings", lambda: s)
    monkeypatch.setattr(google_credentials, "get_settings", lambda: s)
    monkeypatch.setattr(deps, "get_settings", lambda: s)

    token_path = tmp_path / "refresh.txt"
    token_path.write_text("refresh-token-value", encoding="utf-8")

    client = TestClient(_mini_app())
    session = create_app_session_token(s.SECRET_KEY, "admin@example.com", 7)
    client.cookies.set(SESSION_COOKIE_NAME, session)
    r = client.post("/api/auth/google/calendar/disconnect")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "had_token": True}
    assert not token_path.is_file()


def test_disconnect_no_token_still_ok_for_admin(monkeypatch, tmp_path: Path) -> None:
    s = _settings(tmp_path, admin_emails="admin@example.com")
    monkeypatch.setattr(auth_routes, "get_settings", lambda: s)
    monkeypatch.setattr(google_credentials, "get_settings", lambda: s)
    monkeypatch.setattr(deps, "get_settings", lambda: s)

    client = TestClient(_mini_app())
    session = create_app_session_token(s.SECRET_KEY, "admin@example.com", 7)
    client.cookies.set(SESSION_COOKIE_NAME, session)
    r = client.post("/api/auth/google/calendar/disconnect")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["had_token"] is False


def test_disconnect_forbidden_non_admin(monkeypatch, tmp_path: Path) -> None:
    s = _settings(tmp_path, admin_emails="admin@example.com")
    monkeypatch.setattr(auth_routes, "get_settings", lambda: s)
    monkeypatch.setattr(google_credentials, "get_settings", lambda: s)
    monkeypatch.setattr(deps, "get_settings", lambda: s)

    (tmp_path / "refresh.txt").write_text("keep-me", encoding="utf-8")

    client = TestClient(_mini_app())
    session = create_app_session_token(s.SECRET_KEY, "other@example.com", 7)
    client.cookies.set(SESSION_COOKIE_NAME, session)
    r = client.post("/api/auth/google/calendar/disconnect")
    assert r.status_code == 403
    assert (tmp_path / "refresh.txt").read_text(encoding="utf-8") == "keep-me"


def test_disconnect_unauthenticated(monkeypatch, tmp_path: Path) -> None:
    s = _settings(tmp_path, admin_emails="admin@example.com")
    monkeypatch.setattr(auth_routes, "get_settings", lambda: s)
    monkeypatch.setattr(google_credentials, "get_settings", lambda: s)
    monkeypatch.setattr(deps, "get_settings", lambda: s)

    client = TestClient(_mini_app())
    r = client.post("/api/auth/google/calendar/disconnect")
    assert r.status_code == 401
