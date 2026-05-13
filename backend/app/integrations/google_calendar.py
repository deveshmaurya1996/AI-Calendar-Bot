from __future__ import annotations

import base64
import json
from typing import Any

import httpx
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from google.auth.exceptions import RefreshError
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from oauthlib.oauth2.rfc6749.errors import OAuth2Error
from datetime import datetime, timedelta, timezone
import pytz
from app.logger import setup_logger
from app.config import get_settings
from app.core.exceptions import CalendarAccessError, ConflictError
from app.utils.google_calendar_links import normalize_google_calendar_event_url

logger = setup_logger(__name__)
settings = get_settings()


def _rfc3339_utc(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    utc = dt.astimezone(timezone.utc)
    return utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def _decode_jwt_payload_unverified(jwt_str: str) -> dict[str, Any]:
    """Parse OIDC id_token payload without signature verification (trusted path: our refresh)."""
    try:
        parts = jwt_str.split(".")
        if len(parts) != 3:
            return {}
        payload_b64 = parts[1]
        padding = "=" * ((4 - len(payload_b64) % 4) % 4)
        raw = base64.urlsafe_b64decode(payload_b64 + padding)
        data = json.loads(raw.decode("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


class GoogleCalendarService:
    LOGIN_SCOPES = [
        "openid",
        "https://www.googleapis.com/auth/userinfo.email",
        "https://www.googleapis.com/auth/userinfo.profile",
    ]
    CALENDAR_SCOPES = [
        "openid",
        "https://www.googleapis.com/auth/userinfo.email",
        "https://www.googleapis.com/auth/userinfo.profile",
        "https://www.googleapis.com/auth/calendar.readonly",
        "https://www.googleapis.com/auth/calendar.events",
    ]
    SCOPES = CALENDAR_SCOPES

    def __init__(self):
        self.settings = settings

    def get_auth_flow(self, scopes: list[str]) -> Flow:
        try:
            redirect_uri = (self.settings.GOOGLE_REDIRECT_URI or "").strip()
            client_id = (self.settings.GOOGLE_CLIENT_ID or "").strip()
            client_secret = (self.settings.GOOGLE_CLIENT_SECRET or "").strip()
            client_config = {
                "web": {
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": [redirect_uri],
                }
            }
            return Flow.from_client_config(
                client_config,
                scopes=list(scopes),
                redirect_uri=redirect_uri,
            )
        except Exception as e:
            logger.error("Failed to build Google OAuth flow: %s", e)
            raise CalendarAccessError("OAuth credentials not configured") from e

    def get_authorization_url(
        self,
        state: str,
        scopes: list[str],
        *,
        access_type: str,
        prompt: str | None,
    ) -> tuple[str, str, str]:
        flow = self.get_auth_flow(scopes)
        kwargs: dict[str, Any] = {
            "access_type": access_type,
            "state": state,
        }
        if prompt:
            kwargs["prompt"] = prompt
        auth_url, returned_state = flow.authorization_url(**kwargs)
        code_verifier = flow.code_verifier
        if not code_verifier:
            raise CalendarAccessError("OAuth PKCE verifier was not generated")
        return auth_url, returned_state, code_verifier

    def handle_oauth_callback(
        self,
        code: str,
        scopes: list[str],
        *,
        code_verifier: str,
    ) -> dict:
        try:
            flow = self.get_auth_flow(scopes)
            flow.code_verifier = code_verifier
            flow.fetch_token(code=code)
            creds = flow.credentials
            expires_in = 3600
            if creds.expiry:
                now = (
                    datetime.now(timezone.utc).replace(tzinfo=None)
                    if creds.expiry.tzinfo is None
                    else datetime.now(creds.expiry.tzinfo)
                )
                expires_in = max(
                    60,
                    int((creds.expiry - now).total_seconds()),
                )
            return {
                "access_token": creds.token,
                "refresh_token": creds.refresh_token,
                "expires_in": expires_in,
                "token_type": "Bearer",
            }
        except OAuth2Error as e:
            err = getattr(e, "error", None) or type(e).__name__
            desc = (getattr(e, "description", None) or "").strip()
            human = f"{err}: {desc}".strip() if desc else str(err)
            logger.warning(
                "Google OAuth token endpoint error (check consent screen + Workspace policy): %s",
                human,
            )
            raise CalendarAccessError(
                f"OAuth token exchange rejected by Google: {human}"
            ) from e
        except Exception as e:
            logger.exception("OAuth token exchange failed: %s", e)
            raise CalendarAccessError(f"OAuth authorization failed: {str(e)}") from e
    
    async def get_free_busy_info(
        self,
        credentials: dict,
        calendar_id: str,
        start_time: datetime,
        end_time: datetime
    ) -> list:
        try:
            creds = Credentials(**credentials)
            
            if creds.expired and creds.refresh_token:
                request = Request()
                creds.refresh(request)
            
            service = build('calendar', 'v3', credentials=creds)

            time_min = _rfc3339_utc(start_time)
            time_max = _rfc3339_utc(end_time)
            if time_min >= time_max:
                raise CalendarAccessError(
                    "Invalid free/busy window: start must be before end "
                    f"(got timeMin={time_min}, timeMax={time_max})."
                )

            body = {
                "items": [{"id": calendar_id}],
                "timeMin": time_min,
                "timeMax": time_max,
            }

            result = service.freebusy().query(body=body).execute()
            busy_times = result["calendars"][calendar_id]["busy"]

            logger.info(f"Free/busy check: Found {len(busy_times)} busy blocks")
            return busy_times

        except RefreshError:
            raise CalendarAccessError(
                "Calendar token could not be refreshed. Re-connect Google Calendar or check OAuth client configuration."
            )
        except HttpError as e:
            status = getattr(e.resp, "status", None)
            logger.error("freeBusy query failed (HTTP %s): %s", status, e)
            raise CalendarAccessError(
                f"Could not check calendar availability (HTTP {status}). "
                "Verify GOOGLE_CALENDAR_ID and OAuth calendar scopes."
            ) from e
        except CalendarAccessError:
            raise
        except Exception as e:
            logger.error(f"Error checking free/busy: {str(e)}")
            raise CalendarAccessError(f"Could not check calendar availability: {str(e)}")

    async def get_calendar_list_entry(
        self, credentials: dict, calendar_id: str
    ) -> dict[str, Any]:
        try:
            creds = Credentials(**credentials)

            if creds.expired and creds.refresh_token:
                request = Request()
                creds.refresh(request)

            service = build("calendar", "v3", credentials=creds)
            entry = service.calendarList().get(calendarId=calendar_id).execute()
            return {
                "id": entry.get("id"),
                "summary": entry.get("summary"),
                "timeZone": entry.get("timeZone"),
                "accessRole": entry.get("accessRole"),
                "primary": entry.get("primary", False),
            }
        except RefreshError:
            raise CalendarAccessError(
                "Calendar token could not be refreshed. Re-connect Google Calendar or check OAuth client configuration."
            ) from None
        except HttpError as e:
            status = getattr(e.resp, "status", None)
            logger.warning("calendarList.get failed (HTTP %s): %s", status, e)
            raise CalendarAccessError(
                f"Could not load calendar metadata (HTTP {status}). "
                "Ensure GOOGLE_CALENDAR_ID is a calendar this account can access."
            ) from e
        except Exception as e:
            logger.error("get_calendar_list_entry error: %s", e)
            raise CalendarAccessError(f"Could not load calendar metadata: {e}") from e

    def _ensure_credentials_with_access_token(
        self, credentials: dict
    ) -> Credentials | None:
        """Refresh if needed; return credentials with a valid access token."""
        try:
            creds = Credentials(**credentials)
            if (not creds.valid) and creds.refresh_token:
                creds.refresh(Request())
            if not creds.token:
                return None
            return creds
        except RefreshError:
            logger.warning("oauth user profile: token refresh failed")
            return None
        except Exception as e:
            logger.warning("oauth user profile: credential error: %s", e)
            return None

    async def oauth_user_profile(self, credentials: dict) -> dict[str, str | None]:
        creds = self._ensure_credentials_with_access_token(credentials)
        if not creds or not creds.token:
            return {"email": None, "name": None}

        email: str | None = None
        name: str | None = None

        id_raw = getattr(creds, "id_token", None)
        if isinstance(id_raw, str) and id_raw.strip():
            claims = _decode_jwt_payload_unverified(id_raw.strip())
            em = claims.get("email")
            if isinstance(em, str) and em.strip():
                email = em.strip()
            nm = claims.get("name")
            if isinstance(nm, str) and nm.strip():
                name = nm.strip()

        token = creds.token

        userinfo_urls = (
            "https://openidconnect.googleapis.com/v1/userinfo",
            "https://www.googleapis.com/oauth2/v3/userinfo",
            "https://www.googleapis.com/oauth2/v2/userinfo",
        )
        try:
            async with httpx.AsyncClient() as client:
                for url in userinfo_urls:
                    try:
                        r = await client.get(
                            url,
                            headers={"Authorization": f"Bearer {token}"},
                            timeout=15.0,
                        )
                        if r.status_code != 200:
                            continue
                        data = r.json()
                        em = data.get("email")
                        if isinstance(em, str) and em.strip():
                            email = em.strip()
                        nm = data.get("name") or data.get("given_name")
                        if isinstance(nm, str) and nm.strip():
                            name = nm.strip()
                        if email and name:
                            break
                    except Exception as e:
                        logger.debug("userinfo %s failed: %s", url, e)
                        continue

                if not email:
                    tr = await client.get(
                        "https://www.googleapis.com/oauth2/v3/tokeninfo",
                        params={"access_token": token},
                        timeout=15.0,
                    )
                    if tr.status_code == 200:
                        body = tr.json()
                        em = body.get("email")
                        if isinstance(em, str) and em.strip():
                            email = em.strip()
        except Exception as e:
            logger.warning("oauth_user_profile: request failed: %s", e)

        return {"email": email, "name": name}

    async def oauth_account_email(self, credentials: dict) -> str | None:
        profile = await self.oauth_user_profile(credentials)
        return profile.get("email")

    async def create_event(
        self,
        credentials: dict,
        calendar_id: str,
        event_data: dict
    ) -> dict:
        try:
            creds = Credentials(**credentials)
            
            if creds.expired and creds.refresh_token:
                request = Request()
                creds.refresh(request)
            
            service = build('calendar', 'v3', credentials=creds)
            
            event = {
                'summary': (event_data.get('title') or '').strip() or 'Meeting',
                'description': event_data.get('description', ''),
                'start': {
                    'dateTime': event_data['start_time'],
                    'timeZone': event_data.get('timezone', 'UTC'),
                },
                'end': {
                    'dateTime': event_data['end_time'],
                    'timeZone': event_data.get('timezone', 'UTC'),
                },
                'attendees': event_data.get('attendees', []),
            }
            
            created_event = service.events().insert(
                calendarId=calendar_id,
                body=event
            ).execute()
            
            logger.info(f"Event created: {created_event['id']}")

            raw_link = created_event.get("htmlLink")
            event_link = normalize_google_calendar_event_url(raw_link) or raw_link

            return {
                'event_id': created_event['id'],
                'event_link': event_link,
                'summary': created_event['summary'],
                'start': created_event['start']['dateTime'],
                'end': created_event['end']['dateTime'],
            }
            
        except HttpError as e:
            status = getattr(e.resp, "status", None)
            if status == 409:
                raise ConflictError(
                    "That time conflicts with another event. Pick a different slot."
                ) from e
            logger.error("Error creating event (HTTP %s): %s", status, e)
            raise CalendarAccessError(f"Could not create event: {e}") from e
        except Exception as e:
            logger.error(f"Error creating event: {str(e)}")
            if "conflict" in str(e).lower():
                raise ConflictError("Time slot is no longer available") from e
            raise CalendarAccessError(f"Could not create event: {str(e)}") from e
    
    async def get_calendar_list(self, credentials: dict) -> list:
        try:
            creds = Credentials(**credentials)
            
            if creds.expired and creds.refresh_token:
                request = Request()
                creds.refresh(request)
            
            service = build('calendar', 'v3', credentials=creds)
            
            calendar_list = service.calendarList().list().execute()
            calendars = []
            
            for calendar in calendar_list.get('items', []):
                calendars.append({
                    'id': calendar['id'],
                    'summary': calendar['summary'],
                    'timezone': calendar.get('timeZone', 'UTC'),
                    'primary': calendar.get('primary', False),
                })
            
            return calendars
            
        except Exception as e:
            logger.error(f"Error getting calendar list: {str(e)}")
            raise CalendarAccessError("Could not retrieve calendar list")


class GoogleCalendarClient:
    def __init__(
        self,
        access_token: str,
        refresh_token: str | None = None,
        calendar_id: str = "primary",
    ) -> None:
        self._calendar_id = calendar_id
        self._creds = Credentials(
            token=access_token,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=settings.GOOGLE_CLIENT_ID,
            client_secret=settings.GOOGLE_CLIENT_SECRET,
            scopes=GoogleCalendarService.SCOPES,
        )

    def _ensure_fresh_credentials(self) -> None:
        if self._creds.expired and self._creds.refresh_token:
            self._creds.refresh(Request())

    def list_events(self, time_min_iso: str, time_max_iso: str) -> list[dict[str, Any]]:
        try:
            self._ensure_fresh_credentials()
            service = build("calendar", "v3", credentials=self._creds)
            events_result = (
                service.events()
                .list(
                    calendarId=self._calendar_id,
                    timeMin=time_min_iso,
                    timeMax=time_max_iso,
                    singleEvents=True,
                    orderBy="startTime",
                )
                .execute()
            )
            items: list[dict[str, Any]] = []
            for ev in events_result.get("items", []):
                items.append(
                    {
                        "id": ev.get("id"),
                        "summary": ev.get("summary", ""),
                        "start": ev.get("start", {}),
                        "end": ev.get("end", {}),
                        "status": ev.get("status"),
                    }
                )
            return items
        except RefreshError:
            raise CalendarAccessError(
                "Calendar token could not be refreshed. Re-connect Google Calendar or check OAuth client configuration."
            )
        except Exception as e:
            logger.error(f"list_events error: {e}")
            raise CalendarAccessError(f"Could not list calendar events: {e}") from e