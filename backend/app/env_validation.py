from __future__ import annotations

from app.config import Settings

_WEAK_SECRET_KEYS = frozenset(
    {
        "your-secret-key-change-in-production",
        "your_secret_key_here",
    }
)


def validate_settings(settings: Settings) -> None:
    errors: list[str] = []

    if not (settings.POLLINATIONS_API_KEY or "").strip():
        errors.append("POLLINATIONS_API_KEY is required for the chat agent")

    if not (settings.GOOGLE_CLIENT_ID or "").strip():
        errors.append("GOOGLE_CLIENT_ID is required for Google sign-in and Calendar")
    if not (settings.GOOGLE_CLIENT_SECRET or "").strip():
        errors.append("GOOGLE_CLIENT_SECRET is required for Google sign-in and Calendar")
    if not (settings.GOOGLE_REDIRECT_URI or "").strip():
        errors.append("GOOGLE_REDIRECT_URI is required (OAuth callback URL)")
    else:
        uri = settings.GOOGLE_REDIRECT_URI.strip()
        if "google/callback" not in uri:
            errors.append(
                "GOOGLE_REDIRECT_URI must use path /api/auth/google/callback "
                "(e.g. http://localhost:8000/api/auth/google/callback)"
            )

    if not (settings.FRONTEND_PUBLIC_URL or "").strip():
        errors.append("FRONTEND_PUBLIC_URL is required (OAuth return redirects)")

    sk = (settings.SECRET_KEY or "").strip()
    if not sk:
        errors.append("SECRET_KEY is required for sessions and OAuth state")
    elif settings.ENVIRONMENT == "production":
        if sk in _WEAK_SECRET_KEYS or len(sk) < 32:
            errors.append(
                "SECRET_KEY must be a long random value (32+ characters) in production"
            )

    if not (settings.GOOGLE_CALENDAR_ID or "").strip():
        errors.append("GOOGLE_CALENDAR_ID is required (calendar id or primary)")

    if errors:
        raise RuntimeError(
            "Configuration error — set these in backend .env:\n  • "
            + "\n  • ".join(errors)
        )
