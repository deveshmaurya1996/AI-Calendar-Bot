from __future__ import annotations

from urllib.parse import parse_qs, urlencode, urlparse


def normalize_google_calendar_event_url(html_link: str | None) -> str | None:
    if not html_link or not str(html_link).strip():
        return None
    raw = str(html_link).strip()
    try:
        parsed = urlparse(raw)
    except Exception:
        return raw
    qs = parse_qs(parsed.query, keep_blank_values=False)
    eid_vals = qs.get("eid")
    if not eid_vals or not (eid_vals[0] or "").strip():
        return raw
    eid = eid_vals[0].strip()
    base = "https://calendar.google.com/calendar/event"
    return f"{base}?{urlencode({'action': 'VIEW', 'eid': eid})}"
