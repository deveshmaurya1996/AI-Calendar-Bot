
from __future__ import annotations

from pathlib import Path
from typing import Optional

from app.config import Settings, get_settings
from app.integrations.google_calendar import GoogleCalendarService


def _backend_root() -> Path:
    return Path(__file__).resolve().parent.parent


def resolve_refresh_token_file_path(s: Settings | None = None) -> Path:
    settings = s or get_settings()
    root = _backend_root()
    if settings.GOOGLE_REFRESH_TOKEN_FILE:
        p = Path(settings.GOOGLE_REFRESH_TOKEN_FILE)
        return p if p.is_absolute() else (root / p).resolve()
    return (root / ".google_refresh_token").resolve()


def save_server_refresh_token(refresh_token: str) -> None:
    path = resolve_refresh_token_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(refresh_token.strip(), encoding="utf-8")


def server_refresh_token_present() -> bool:
    return bool((_read_refresh_token_from_file() or "").strip())


def clear_server_refresh_token(s: Settings | None = None) -> bool:
    path = resolve_refresh_token_file_path(s)
    try:
        if path.is_file():
            path.unlink()
            return True
    except OSError:
        pass
    return False


def _admin_email_set(s: Settings) -> set[str]:
    raw = (s.GOOGLE_CALENDAR_ADMIN_EMAILS or "").strip()
    if not raw:
        return set()
    return {p.strip().lower() for p in raw.split(",") if p.strip()}


def should_persist_calendar_refresh_token(signer_email: str, s: Settings | None = None) -> bool:
    settings = s or get_settings()
    em = signer_email.strip().lower()
    admins = _admin_email_set(settings)
    if not admins:
        return False
    return em in admins


def can_start_server_calendar_oauth(signer_email: str | None, s: Settings | None = None) -> bool:
    em_raw = (signer_email or "").strip()
    if not em_raw:
        return False
    settings = s or get_settings()
    admins = _admin_email_set(settings)
    if not admins:
        return False
    return em_raw.lower() in admins


def _read_refresh_token_from_file() -> str | None:
    path = resolve_refresh_token_file_path()
    try:
        if path.is_file():
            t = path.read_text(encoding="utf-8").strip()
            return t or None
    except OSError:
        return None
    return None


def google_credentials_for_api(
    access_token: Optional[str], refresh_token: Optional[str]
) -> dict:
    s = get_settings()
    return {
        "token": access_token,
        "refresh_token": refresh_token,
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": s.GOOGLE_CLIENT_ID,
        "client_secret": s.GOOGLE_CLIENT_SECRET,
        "scopes": list(GoogleCalendarService.SCOPES),
    }


def server_calendar_credentials() -> dict | None:
    token = (_read_refresh_token_from_file() or "").strip()
    if not token:
        return None
    return google_credentials_for_api(None, token)


def parse_bearer_authorization(authorization: str | None) -> str | None:
    if not authorization:
        return None
    p = authorization.strip()
    if p.lower().startswith("bearer "):
        return p[7:].strip() or None
    return None
