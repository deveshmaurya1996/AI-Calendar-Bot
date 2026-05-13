from datetime import date, datetime, timedelta

import pytest
import pytz

from app.api.schemas import AvailableSlot
from app.core.exceptions import NoAvailableSlotsError
from app.services.availability_service import AvailabilityService


def test_generate_time_slots_respects_duration() -> None:
    svc = AvailabilityService()
    tz = pytz.UTC
    start = tz.localize(datetime(2026, 5, 11, 9, 0, 0))
    end = tz.localize(datetime(2026, 5, 11, 10, 0, 0))
    slots = svc._generate_time_slots(start, end, 30)
    assert len(slots) >= 3
    for a, b in slots:
        assert b - a == timedelta(minutes=30)


def test_generate_time_slots_long_meetings_step_by_duration_not_15() -> None:
    """60-minute meetings should not offer starts every 15 minutes (overlapping)."""
    svc = AvailabilityService()
    tz = pytz.UTC
    start = tz.localize(datetime(2026, 5, 13, 12, 0, 0))
    end = tz.localize(datetime(2026, 5, 13, 15, 0, 0))
    slots = svc._generate_time_slots(start, end, 60)
    assert slots == [
        (start, start + timedelta(hours=1)),
        (start + timedelta(hours=1), start + timedelta(hours=2)),
        (start + timedelta(hours=2), start + timedelta(hours=3)),
    ]


def test_filter_available_slots_excludes_busy() -> None:
    svc = AvailabilityService()
    tz = pytz.UTC
    start = tz.localize(datetime(2026, 5, 11, 9, 0, 0))
    all_slots = [
        (start, start + timedelta(minutes=30)),
        (start + timedelta(minutes=30), start + timedelta(minutes=60)),
    ]
    busy = [{"start": "2026-05-11T09:00:00Z", "end": "2026-05-11T09:30:00Z"}]
    free = svc._filter_available_slots(all_slots, busy)
    assert len(free) == 1
    assert free[0].start_time.startswith("2026-05-11")


@pytest.mark.asyncio
async def test_get_slots_across_days_skips_empty_days_and_merges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc = AvailabilityService()
    called: list[str] = []

    async def fake_get(
        creds, cal_id, d, pref, dur, tz
    ):
        called.append(d)
        if d.endswith("13"):
            raise NoAvailableSlotsError("none")
        return [
            AvailableSlot(
                start_time=f"{d}T10:00:00+00:00",
                end_time=f"{d}T10:30:00+00:00",
                duration_minutes=30,
            )
        ]

    monkeypatch.setattr(svc, "get_available_slots", fake_get)
    slots = await svc.get_slots_across_days(
        {},
        "primary",
        date(2026, 5, 12),
        3,
        "flexible",
        30,
        "UTC",
        max_slots=10,
    )

    assert called == ["2026-05-12", "2026-05-13", "2026-05-14"]
    assert len(slots) == 2
    assert all("2026-05-12" in s.start_time or "2026-05-14" in s.start_time for s in slots)


@pytest.mark.asyncio
async def test_get_slots_across_days_raises_when_all_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc = AvailabilityService()

    async def fake_empty(*args, **kwargs):
        raise NoAvailableSlotsError("empty")

    monkeypatch.setattr(svc, "get_available_slots", fake_empty)
    with pytest.raises(NoAvailableSlotsError):
        await svc.get_slots_across_days(
            {},
            "primary",
            date(2026, 5, 12),
            2,
            "flexible",
            30,
            "UTC",
        )
