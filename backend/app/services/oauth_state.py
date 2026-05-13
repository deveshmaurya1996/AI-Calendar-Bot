from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt


def create_oauth_state(
    secret: str,
    *,
    mode: str = "login",
    ttl_minutes: int = 10,
) -> str:
    now = datetime.now(timezone.utc)
    exp = now + timedelta(minutes=ttl_minutes)
    payload = {
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
        "typ": "google_oauth",
        "mode": mode,
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def get_oauth_state_mode(state: str, secret: str) -> str | None:
    try:
        p = jwt.decode(state, secret, algorithms=["HS256"])
        if p.get("typ") != "google_oauth":
            return None
        if p.get("mode") == "calendar":
            return "calendar"
        return "login"
    except jwt.PyJWTError:
        return None
