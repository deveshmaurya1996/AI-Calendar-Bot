from typing import Any

from app.integrations.google_calendar import GoogleCalendarClient


class CalendarService:
    def __init__(self, access_token: str, refresh_token: str | None = None) -> None:
        self._client = GoogleCalendarClient(access_token, refresh_token=refresh_token)

    def list_busy_events(self, time_min_iso: str, time_max_iso: str) -> list[dict[str, Any]]:
        return self._client.list_events(time_min_iso, time_max_iso)
