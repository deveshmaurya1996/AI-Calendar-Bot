
from datetime import datetime

import pytz

from app.integrations.google_calendar import _rfc3339_utc


def test_rfc3339_utc_from_eastern_no_invalid_suffix() -> None:
    dt = pytz.timezone("America/New_York").localize(datetime(2026, 5, 13, 9, 0, 0))
    s = _rfc3339_utc(dt)
    assert s.endswith("Z")
    assert "+00:00Z" not in s
    assert "-04:00Z" not in s
    assert "-05:00Z" not in s


def test_rfc3339_utc_naive_treated_as_utc() -> None:
    s = _rfc3339_utc(datetime(2026, 1, 1, 12, 0, 0))
    assert s == "2026-01-01T12:00:00Z"
