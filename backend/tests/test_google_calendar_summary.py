"""Sanity check: empty-string titles must not produce an empty Calendar summary."""

from unittest.mock import MagicMock, patch

import pytest

from app.integrations.google_calendar import GoogleCalendarService


@pytest.mark.asyncio
async def test_create_event_summary_falls_back_when_title_blank() -> None:
    svc = GoogleCalendarService()

    execute_return = {
        "id": "ev1",
        "htmlLink": "https://calendar.example/e",
        "summary": "Meeting",
        "start": {"dateTime": "2026-06-01T10:00:00Z"},
        "end": {"dateTime": "2026-06-01T10:30:00Z"},
    }
    m_insert = MagicMock(
        return_value=MagicMock(execute=MagicMock(return_value=execute_return))
    )
    m_events = MagicMock()
    m_events.insert = m_insert
    m_service = MagicMock()
    m_service.events = MagicMock(return_value=m_events)

    with patch("app.integrations.google_calendar.build", return_value=m_service):
        with patch("app.integrations.google_calendar.Credentials") as cred_cls:
            cred_inst = MagicMock()
            cred_inst.expired = False
            cred_inst.refresh_token = None
            cred_cls.return_value = cred_inst
            await svc.create_event(
                {"token": "x"},
                "primary",
                {
                    "title": "   ",
                    "description": "d",
                    "start_time": "2026-06-01T10:00:00Z",
                    "end_time": "2026-06-01T10:30:00Z",
                    "timezone": "UTC",
                    "attendees": [],
                },
            )

    body = m_insert.call_args.kwargs["body"]
    assert body.get("summary") == "Meeting"


@pytest.mark.asyncio
async def test_create_event_returns_normalized_event_link_when_eid_present() -> None:
    svc = GoogleCalendarService()
    raw_link = (
        "https://www.google.com/calendar/event?"
        "eid=YWJjZGVmMTIzQGV4YW1wbGUuY29t"
    )
    execute_return = {
        "id": "ev1",
        "htmlLink": raw_link,
        "summary": "Meeting",
        "start": {"dateTime": "2026-06-01T10:00:00Z"},
        "end": {"dateTime": "2026-06-01T10:30:00Z"},
    }
    m_insert = MagicMock(
        return_value=MagicMock(execute=MagicMock(return_value=execute_return))
    )
    m_events = MagicMock()
    m_events.insert = m_insert
    m_service = MagicMock()
    m_service.events = MagicMock(return_value=m_events)

    with patch("app.integrations.google_calendar.build", return_value=m_service):
        with patch("app.integrations.google_calendar.Credentials") as cred_cls:
            cred_inst = MagicMock()
            cred_inst.expired = False
            cred_inst.refresh_token = None
            cred_cls.return_value = cred_inst
            result = await svc.create_event(
                {"token": "x"},
                "primary",
                {
                    "title": "Sync",
                    "description": "d",
                    "start_time": "2026-06-01T10:00:00Z",
                    "end_time": "2026-06-01T10:30:00Z",
                    "timezone": "UTC",
                    "attendees": [],
                },
            )

    assert result["event_link"] is not None
    assert result["event_link"].startswith(
        "https://calendar.google.com/calendar/event?"
    )
    assert "action=VIEW" in result["event_link"]
