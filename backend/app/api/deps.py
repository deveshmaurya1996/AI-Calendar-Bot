

from __future__ import annotations

from fastapi import HTTPException, Request

from app.config import get_settings
from app.services.app_session import decode_app_session_token

SESSION_COOKIE_NAME = "ai_calendar_session"


def _app_login_configured() -> bool:
    s = get_settings()
    u = (s.APP_LOGIN_USERNAME or "").strip()
    p = (s.APP_LOGIN_PASSWORD or "").strip()
    return bool(u and p)


def _google_oauth_configured() -> bool:
    s = get_settings()
    return bool(
        (s.GOOGLE_CLIENT_ID or "").strip()
        and (s.GOOGLE_CLIENT_SECRET or "").strip()
        and (s.GOOGLE_REDIRECT_URI or "").strip()
    )


def auth_configured() -> bool:
    return _app_login_configured() or _google_oauth_configured()


def app_password_login_configured() -> bool:
    return _app_login_configured()


def session_username_from_request(request: Request) -> str | None:
    if not auth_configured():
        return None
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None
    return decode_app_session_token(token, get_settings().SECRET_KEY)


async def require_app_user(request: Request) -> str:
    if not auth_configured():
        return "__open__"
    user = session_username_from_request(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user
