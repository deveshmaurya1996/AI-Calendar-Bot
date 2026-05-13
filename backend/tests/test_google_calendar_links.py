"""Tests for Google Calendar web URL normalization."""

from app.utils.google_calendar_links import normalize_google_calendar_event_url


def test_normalize_rewrites_google_com_htmllink_with_eid() -> None:
    raw = (
        "https://www.google.com/calendar/event?"
        "eid=YWJjZGVmMTIzQGV4YW1wbGUuY29t"
    )
    out = normalize_google_calendar_event_url(raw)
    assert out is not None
    assert out.startswith("https://calendar.google.com/calendar/event?")
    assert "action=VIEW" in out
    assert "eid=" in out


def test_normalize_preserves_when_no_eid() -> None:
    raw = "https://calendar.example.com/some/path"
    assert normalize_google_calendar_event_url(raw) == raw


def test_normalize_none_returns_none() -> None:
    assert normalize_google_calendar_event_url(None) is None


def test_normalize_empty_returns_none() -> None:
    assert normalize_google_calendar_event_url("  ") is None


def test_normalize_calendar_google_host_with_eid() -> None:
    raw = (
        "https://calendar.google.com/calendar/event?"
        "eid=TXlFdmVudF9pZF9oZXJlIHRlc3RAZ21haWwuY29t"
    )
    out = normalize_google_calendar_event_url(raw)
    assert out is not None
    assert "calendar.google.com/calendar/event" in out
    assert "action=VIEW" in out
