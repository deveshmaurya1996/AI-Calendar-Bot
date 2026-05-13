from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import auth_routes
from app.api.auth_routes import _oauth_pkce_cookie_name
from app.config import get_settings
from app.integrations.google_calendar import GoogleCalendarService
from app.services.oauth_state import create_oauth_state


class FakeCalendarService:
    def __init__(self) -> None:
        self.last_code_verifier: str | None = None

    def get_authorization_url(
        self,
        state: str,
        scopes: list[str],
        *,
        access_type: str,
        prompt: str | None,
    ) -> tuple[str, str, str]:
        return (
            f"https://accounts.google.com/o/oauth2/auth?state={state}",
            state,
            "test-code-verifier",
        )

    def handle_oauth_callback(
        self,
        code: str,
        scopes: list[str],
        *,
        code_verifier: str,
    ) -> dict:
        self.last_code_verifier = code_verifier
        return {"access_token": "access-token", "refresh_token": None}


def _client_with_fake_service(monkeypatch) -> tuple[TestClient, FakeCalendarService]:
    app = FastAPI()
    app.include_router(auth_routes.auth_router, prefix="/api")
    fake_service = FakeCalendarService()
    monkeypatch.setattr(auth_routes._scheduling, "calendar_service", fake_service)

    async def fake_google_user_email(access_token: str) -> str:
        return "user@example.com"

    monkeypatch.setattr(auth_routes, "_google_user_email", fake_google_user_email)
    return TestClient(app), fake_service


def test_google_oauth_callback_uses_pkce_verifier_cookie(monkeypatch) -> None:
    client, fake_service = _client_with_fake_service(monkeypatch)

    start = client.get("/api/auth/google/start")
    assert start.status_code == 200
    state = start.json()["state"]
    assert client.cookies.get(_oauth_pkce_cookie_name(state)) == "test-code-verifier"

    callback = client.get(
        "/api/auth/google/callback",
        params={"code": "auth-code", "state": state},
        follow_redirects=False,
    )

    assert callback.status_code == 302
    assert callback.headers["location"] == "http://localhost:3000/?signed_in=1"
    assert fake_service.last_code_verifier == "test-code-verifier"
    assert not client.cookies.get(_oauth_pkce_cookie_name(state))


def test_google_oauth_callback_rejects_missing_pkce_verifier(monkeypatch) -> None:
    client, fake_service = _client_with_fake_service(monkeypatch)
    state = create_oauth_state(get_settings().SECRET_KEY, mode="login")

    callback = client.get(
        "/api/auth/google/callback",
        params={"code": "auth-code", "state": state},
        follow_redirects=False,
    )

    assert callback.status_code == 302
    assert callback.headers["location"] == (
        "http://localhost:3000/?calendar_error=missing_code_verifier"
    )
    assert fake_service.last_code_verifier is None


def test_google_oauth_callback_uses_server_pkce_store_when_cookie_cleared(
    monkeypatch,
) -> None:
    """Browsers may not persist the PKCE cookie on cross-origin localhost flows."""
    client, fake_service = _client_with_fake_service(monkeypatch)

    start = client.get("/api/auth/google/start")
    assert start.status_code == 200
    state = start.json()["state"]
    client.cookies.clear()

    callback = client.get(
        "/api/auth/google/callback",
        params={"code": "auth-code", "state": state},
        follow_redirects=False,
    )

    assert callback.status_code == 302
    assert callback.headers["location"] == "http://localhost:3000/?signed_in=1"
    assert fake_service.last_code_verifier == "test-code-verifier"


def test_google_oauth_callback_handles_naive_google_expiry(monkeypatch) -> None:
    service = GoogleCalendarService()

    class FakeCredentials:
        token = "access-token"
        refresh_token = "refresh-token"
        expiry = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)

    class FakeFlow:
        def __init__(self) -> None:
            self.code_verifier: str | None = None
            self.fetch_kwargs: dict | None = None

        def fetch_token(self, **kwargs) -> None:
            self.fetch_kwargs = kwargs

        @property
        def credentials(self) -> FakeCredentials:
            return FakeCredentials()

    fake_flow = FakeFlow()
    monkeypatch.setattr(service, "get_auth_flow", lambda scopes: fake_flow)

    tokens = service.handle_oauth_callback(
        "auth-code",
        GoogleCalendarService.LOGIN_SCOPES,
        code_verifier="test-code-verifier",
    )

    assert tokens["access_token"] == "access-token"
    assert tokens["refresh_token"] == "refresh-token"
    assert tokens["expires_in"] > 0
    assert fake_flow.code_verifier == "test-code-verifier"
    assert fake_flow.fetch_kwargs == {"code": "auth-code"}
