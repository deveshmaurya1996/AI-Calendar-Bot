

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt


def create_app_session_token(
    secret: str, username: str, expire_days: int
) -> str:
    now = datetime.now(timezone.utc)
    exp = now + timedelta(days=expire_days)
    payload = {
        "sub": username,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
        "typ": "app_session",
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def decode_app_session_token(token: str, secret: str) -> str | None:
    try:
        data = jwt.decode(token, secret, algorithms=["HS256"])
        if data.get("typ") != "app_session":
            return None
        sub = data.get("sub")
        if not sub:
            return None
        return str(sub).strip()
    except jwt.PyJWTError:
        return None
