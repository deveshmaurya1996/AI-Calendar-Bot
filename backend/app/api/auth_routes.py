
from __future__ import annotations

import hashlib
import time
from urllib.parse import urlencode, urlparse, urlunparse

import httpx
from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse

from app.api.deps import (
    SESSION_COOKIE_NAME,
    app_password_login_configured,
    auth_configured,
    session_username_from_request,
)
from app.api.schemas import (
    DisconnectCalendarResponse,
    GoogleAuthStartResponse,
    LoginRequest,
    LoginResponse,
    LogoutResponse,
    MeResponse,
)
from app.config import get_settings
from app.core.exceptions import CalendarAccessError
from app.logger import setup_logger
from app.services.app_session import create_app_session_token
from app.integrations.google_calendar import GoogleCalendarService
from app.services.google_credentials import (
    can_start_server_calendar_oauth,
    clear_server_refresh_token,
    save_server_refresh_token,
    server_refresh_token_present,
    should_persist_calendar_refresh_token,
)
from app.services.oauth_state import create_oauth_state, get_oauth_state_mode
from app.services.scheduling_service import SchedulingService

logger = setup_logger(__name__)

auth_router = APIRouter(prefix="/auth", tags=["auth"])

_scheduling = SchedulingService()
_OAUTH_PKCE_COOKIE_PREFIX = "google_oauth_pkce_"
_OAUTH_PKCE_COOKIE_MAX_AGE_SECONDS = 10 * 60
_OAUTH_PKCE_COOKIE_PATH = "/api/auth/google"

_oauth_pkce_verifier_store: dict[str, tuple[str, float]] = {}
_OAUTH_PKCE_STORE_TTL_SEC = float(_OAUTH_PKCE_COOKIE_MAX_AGE_SECONDS)


def _pkce_store_put(state: str, code_verifier: str) -> None:
    _oauth_pkce_verifier_store[state] = (code_verifier, time.monotonic())


def _pkce_store_discard(state: str) -> None:
    _oauth_pkce_verifier_store.pop(state, None)


def _pkce_store_take(state: str) -> str | None:
    entry = _oauth_pkce_verifier_store.pop(state, None)
    if not entry:
        return None
    verifier, ts = entry
    if time.monotonic() - ts > _OAUTH_PKCE_STORE_TTL_SEC:
        return None
    return verifier


async def _google_user_email(access_token: str) -> str:
    async with httpx.AsyncClient() as client:
        r = await client.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=15.0,
        )
        r.raise_for_status()
        data = r.json()
    email = data.get("email")
    if not email:
        raise ValueError("Google userinfo did not include an email")
    return str(email)


def _frontend_redirect(query: dict[str, str]) -> str:
    s = get_settings()
    base = s.FRONTEND_PUBLIC_URL.strip().rstrip("/")
    parts = urlparse(base)
    path = parts.path if parts.path else "/"
    q = urlencode(query)
    return urlunparse(
        (parts.scheme, parts.netloc, path, parts.params, q, parts.fragment)
    )


def _oauth_pkce_cookie_name(state: str) -> str:
    state_hash = hashlib.sha256(state.encode("utf-8")).hexdigest()[:32]
    return f"{_OAUTH_PKCE_COOKIE_PREFIX}{state_hash}"


def _set_oauth_pkce_cookie(
    response: Response,
    *,
    state: str,
    code_verifier: str,
) -> None:
    _pkce_store_put(state, code_verifier)
    settings = get_settings()
    response.set_cookie(
        key=_oauth_pkce_cookie_name(state),
        value=code_verifier,
        httponly=True,
        max_age=_OAUTH_PKCE_COOKIE_MAX_AGE_SECONDS,
        samesite="lax",
        secure=settings.ENVIRONMENT == "production",
        path=_OAUTH_PKCE_COOKIE_PATH,
    )


def _delete_oauth_pkce_cookie(response: Response, *, state: str) -> None:
    _pkce_store_discard(state)
    settings = get_settings()
    response.delete_cookie(
        key=_oauth_pkce_cookie_name(state),
        path=_OAUTH_PKCE_COOKIE_PATH,
        samesite="lax",
        secure=settings.ENVIRONMENT == "production",
    )


def _oauth_error_redirect(error: str, *, state: str | None = None) -> RedirectResponse:
    response = RedirectResponse(
        url=_frontend_redirect({"calendar_error": error}),
        status_code=302,
    )
    if state:
        _delete_oauth_pkce_cookie(response, state=state)
    return response


@auth_router.get("/me", response_model=MeResponse)
async def app_me(request: Request):
    if not auth_configured():
        return MeResponse(auth_configured=False, authenticated=True, username=None)
    settings = get_settings()
    uname = session_username_from_request(request)
    can_link = bool(
        uname and can_start_server_calendar_oauth(uname, settings)
    )
    return MeResponse(
        auth_configured=True,
        authenticated=uname is not None,
        username=uname,
        can_link_server_calendar=can_link,
    )


@auth_router.post("/login", response_model=LoginResponse)
async def app_login(body: LoginRequest, response: Response):
    settings = get_settings()
    if not app_password_login_configured():
        raise HTTPException(
            status_code=503,
            detail="Password login is not configured (set APP_LOGIN_USERNAME and APP_LOGIN_PASSWORD).",
        )
    expected_u = (settings.APP_LOGIN_USERNAME or "").strip()
    expected_p = (settings.APP_LOGIN_PASSWORD or "").strip()
    if body.username.strip() != expected_u or body.password != expected_p:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    token = create_app_session_token(
        settings.SECRET_KEY,
        body.username.strip(),
        settings.SESSION_EXPIRE_DAYS,
    )
    max_age = settings.SESSION_EXPIRE_DAYS * 86400
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        max_age=max_age,
        samesite="lax",
        secure=settings.ENVIRONMENT == "production",
        path="/",
    )
    return LoginResponse(username=body.username.strip())


@auth_router.post("/logout", response_model=LogoutResponse)
async def app_logout(response: Response):
    settings = get_settings()
    response.delete_cookie(
        SESSION_COOKIE_NAME,
        path="/",
        samesite="lax",
        secure=settings.ENVIRONMENT == "production",
    )
    return LogoutResponse()


@auth_router.get("/google/start", response_model=GoogleAuthStartResponse)
async def google_oauth_start(response: Response):
    settings = get_settings()
    state = create_oauth_state(settings.SECRET_KEY, mode="login")
    try:
        auth_url, returned_state, code_verifier = (
            _scheduling.calendar_service.get_authorization_url(
                state,
                GoogleCalendarService.LOGIN_SCOPES,
                access_type="online",
                prompt="select_account",
            )
        )
    except CalendarAccessError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    if returned_state != state:
        logger.warning("OAuth state mismatch from Google flow (unexpected)")
    _set_oauth_pkce_cookie(
        response,
        state=returned_state,
        code_verifier=code_verifier,
    )
    return GoogleAuthStartResponse(authorization_url=auth_url, state=returned_state)


@auth_router.get("/google/calendar/start", response_model=GoogleAuthStartResponse)
async def google_calendar_oauth_start(request: Request, response: Response):
    settings = get_settings()
    email = session_username_from_request(request)
    if not email or not can_start_server_calendar_oauth(email, settings):
        raise HTTPException(
            status_code=403,
            detail="Server calendar linking is not available for this account.",
        )
    state = create_oauth_state(settings.SECRET_KEY, mode="calendar")
    try:
        auth_url, returned_state, code_verifier = (
            _scheduling.calendar_service.get_authorization_url(
                state,
                GoogleCalendarService.CALENDAR_SCOPES,
                access_type="offline",
                prompt="consent",
            )
        )
    except CalendarAccessError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    if returned_state != state:
        logger.warning("OAuth state mismatch from Google flow (unexpected)")
    _set_oauth_pkce_cookie(
        response,
        state=returned_state,
        code_verifier=code_verifier,
    )
    return GoogleAuthStartResponse(authorization_url=auth_url, state=returned_state)


@auth_router.post(
    "/google/calendar/disconnect",
    response_model=DisconnectCalendarResponse,
)
async def google_calendar_disconnect(request: Request):
    """Remove the server-stored Google Calendar refresh token (admin only)."""
    settings = get_settings()
    email = session_username_from_request(request)
    if not email:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if not can_start_server_calendar_oauth(email, settings):
        raise HTTPException(
            status_code=403,
            detail="Only calendar admin accounts may disconnect the shared calendar.",
        )
    had = server_refresh_token_present()
    clear_server_refresh_token(settings)
    return DisconnectCalendarResponse(ok=True, had_token=had)


@auth_router.get("/google/callback")
async def google_oauth_callback(
    request: Request,
    code: str | None = Query(None),
    state: str | None = Query(None),
    error: str | None = Query(None),
):
    settings = get_settings()
    if error:
        logger.warning("Google OAuth error query param: %s", error)
        return _oauth_error_redirect(error, state=state)
    if not code or not state:
        return _oauth_error_redirect("missing_code_or_state", state=state)
    mode = get_oauth_state_mode(state, settings.SECRET_KEY)
    if mode is None:
        logger.warning("Invalid or expired OAuth state")
        return _oauth_error_redirect("invalid_state", state=state)
    cookie_verifier = request.cookies.get(_oauth_pkce_cookie_name(state))
    if cookie_verifier:
        _pkce_store_discard(state)
        code_verifier = cookie_verifier
    else:
        code_verifier = _pkce_store_take(state)
    if not code_verifier:
        logger.warning(
            "Missing OAuth PKCE verifier (no cookie and no server store for state)"
        )
        return _oauth_error_redirect("missing_code_verifier", state=state)
    scopes = (
        GoogleCalendarService.CALENDAR_SCOPES
        if mode == "calendar"
        else GoogleCalendarService.LOGIN_SCOPES
    )
    try:
        tokens = _scheduling.calendar_service.handle_oauth_callback(
            code,
            scopes,
            code_verifier=code_verifier,
        )
    except CalendarAccessError as e:
        err = "token_exchange_failed_calendar" if mode == "calendar" else "token_exchange_failed"
        logger.warning("OAuth token exchange failed (%s): %s", mode, e)
        return _oauth_error_redirect(err, state=state)
    access = tokens.get("access_token")
    if not access:
        logger.warning("Google token response missing access_token")
        return _oauth_error_redirect("missing_access_token", state=state)
    try:
        email = await _google_user_email(str(access))
    except (httpx.HTTPError, ValueError) as e:
        logger.warning("Could not read Google profile for session: %s", e)
        return _oauth_error_redirect("userinfo_failed", state=state)

    if mode == "calendar":
        refresh = tokens.get("refresh_token")
        if refresh:
            if should_persist_calendar_refresh_token(email, settings):
                try:
                    save_server_refresh_token(refresh)
                except OSError as e:
                    logger.error("Failed to persist refresh token: %s", e)
                    raise HTTPException(
                        status_code=500, detail="Could not save credentials"
                    ) from e
            else:
                logger.info(
                    "Calendar refresh token not persisted (email not in GOOGLE_CALENDAR_ADMIN_EMAILS)"
                )
        elif not server_refresh_token_present():
            logger.warning("No refresh_token and server has no stored calendar token")
            return _oauth_error_redirect("no_refresh_token", state=state)
        query = {"calendar_connected": "1"}
    else:
        query = {"signed_in": "1"}

    session_token = create_app_session_token(
        settings.SECRET_KEY,
        email,
        settings.SESSION_EXPIRE_DAYS,
    )
    max_age = settings.SESSION_EXPIRE_DAYS * 86400
    redirect = RedirectResponse(
        url=_frontend_redirect(query),
        status_code=302,
    )
    _delete_oauth_pkce_cookie(redirect, state=state)
    redirect.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_token,
        httponly=True,
        max_age=max_age,
        samesite="lax",
        secure=settings.ENVIRONMENT == "production",
        path="/",
    )
    return redirect
