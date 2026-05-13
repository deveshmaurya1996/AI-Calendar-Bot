from unittest.mock import AsyncMock

import pytest

from app.api.schemas import AvailableSlot, Intent, SchedulingIntent, TimePreference
from app.services.scheduling_service import SchedulingService


def test_same_instant_z_vs_offset() -> None:
    assert SchedulingService._same_instant(
        "2026-05-11T10:00:00Z", "2026-05-11T10:00:00+00:00"
    )


def test_pick_slot_by_start() -> None:
    svc = SchedulingService()
    state = {
        "available_slots": [
            {"start": "2026-05-12T09:00:00", "end": "2026-05-12T09:30:00"},
            {"start": "2026-05-12T14:00:00", "end": "2026-05-12T14:30:00"},
        ]
    }
    picked = svc._pick_slot(state, "2026-05-12T14:00:00")
    assert picked["start"] == "2026-05-12T14:00:00"


def test_pick_slot_iso_variants_match() -> None:
    svc = SchedulingService()
    state = {
        "available_slots": [
            {
                "start": "2026-05-13T14:30:00+00:00",
                "end": "2026-05-13T15:30:00+00:00",
            },
        ]
    }
    picked = svc._pick_slot(state, "2026-05-13T14:30:00.000Z")
    assert picked["start"] == "2026-05-13T14:30:00+00:00"


def test_same_instant_fractional_z() -> None:
    assert SchedulingService._same_instant(
        "2026-05-13T09:30:00.000Z",
        "2026-05-13T09:30:00+00:00",
    )


def test_resolve_slot_uses_explicit_pair_when_no_ui_selection() -> None:
    svc = SchedulingService()
    intent = SchedulingIntent(
        intent=Intent.CONFIRM,
        time_preference=TimePreference.MORNING,
        confidence=1.0,
        slot_start_iso="2026-05-14T15:00:00+00:00",
        slot_end_iso="2026-05-14T16:00:00+00:00",
        meeting_title="Sync",
    )
    state = {"available_slots": []}
    picked = svc._resolve_slot_for_booking(state, None, intent)
    assert picked["start"] == "2026-05-14T15:00:00+00:00"
    assert picked["end"] == "2026-05-14T16:00:00+00:00"


def test_pick_slot_defaults_when_only_one_slot() -> None:
    svc = SchedulingService()
    state = {
        "available_slots": [
            {"start": "2026-05-12T09:00:00", "end": "2026-05-12T09:30:00"},
        ]
    }
    picked = svc._pick_slot(state, None)
    assert picked["start"] == "2026-05-12T09:00:00"


@pytest.mark.asyncio
async def test_suggest_alternative_slots_excludes_busy_instant() -> None:
    svc = SchedulingService()
    slots = [
        AvailableSlot(
            start_time="2026-05-16T10:00:00+00:00",
            end_time="2026-05-16T10:30:00+00:00",
            duration_minutes=30,
        ),
        AvailableSlot(
            start_time="2026-05-16T11:00:00+00:00",
            end_time="2026-05-16T11:30:00+00:00",
            duration_minutes=30,
        ),
    ]

    async def fake_get_slots_across_days(*_a, **_kw):
        return slots

    svc.availability_service.get_slots_across_days = fake_get_slots_across_days

    alts = await svc.suggest_alternative_slots(
        {},
        "primary",
        "UTC",
        30,
        exclude_start_iso="2026-05-16T10:00:00+00:00",
    )
    assert len(alts) == 1
    assert alts[0]["start"] == "2026-05-16T11:00:00+00:00"


@pytest.mark.asyncio
async def test_try_auto_book_from_explicit_slot_calls_confirmation() -> None:
    svc = SchedulingService()
    svc.conversation_states["c1"] = {
        "intent": None,
        "available_slots": [],
        "selected_slot": None,
        "confirmed": False,
        "awaiting_final_user_confirm": False,
    }
    svc._handle_confirmation = AsyncMock(
        return_value={
            "status": "confirmed",
            "message": "Booked",
            "event_link": "https://calendar.example/e",
            "event_id": "id1",
        }
    )

    intent = SchedulingIntent(
        intent=Intent.SCHEDULE,
        time_preference=TimePreference.AFTERNOON,
        confidence=1.0,
        meeting_title="Design review",
        attendee_name="Guest User",
        slot_start_iso="2026-05-15T15:00:00+00:00",
        slot_end_iso="2026-05-15T15:30:00+00:00",
    )
    avail = {
        "status": "available_slots",
        "available_slots": [
            {
                "start": "2026-05-15T15:00:00+00:00",
                "end": "2026-05-15T15:30:00+00:00",
                "display": "03:00 PM",
            }
        ],
        "requires_confirmation": True,
    }
    result = await svc._try_auto_book_from_explicit_slot(
        "c1",
        intent,
        {},
        "primary",
        "UTC",
        avail,
        participant_email="guest@example.com",
    )
    assert result is not None
    assert result["status"] == "confirmed"
    svc._handle_confirmation.assert_awaited_once()
    last_call = svc._handle_confirmation.await_args
    assert last_call is not None
    assert last_call.kwargs.get("bypass_final_confirm") is True


@pytest.mark.asyncio
async def test_try_auto_book_skips_without_title() -> None:
    svc = SchedulingService()
    intent = SchedulingIntent(
        intent=Intent.SCHEDULE,
        time_preference=TimePreference.AFTERNOON,
        confidence=1.0,
        slot_start_iso="2026-05-15T15:00:00+00:00",
        slot_end_iso="2026-05-15T15:30:00+00:00",
    )
    avail = {
        "available_slots": [
            {
                "start": "2026-05-15T15:00:00+00:00",
                "end": "2026-05-15T15:30:00+00:00",
                "display": "03:00 PM",
            }
        ],
    }
    svc._handle_confirmation = AsyncMock()
    result = await svc._try_auto_book_from_explicit_slot(
        "c1", intent, {}, "primary", "UTC", avail, participant_email=None
    )
    assert result is None
    svc._handle_confirmation.assert_not_called()


@pytest.mark.asyncio
async def test_try_auto_book_skips_when_guest_email_without_attendee_name() -> None:
    svc = SchedulingService()
    intent = SchedulingIntent(
        intent=Intent.SCHEDULE,
        time_preference=TimePreference.AFTERNOON,
        confidence=1.0,
        meeting_title="Design review",
        participant_email="guest@example.com",
        slot_start_iso="2026-05-15T15:00:00+00:00",
        slot_end_iso="2026-05-15T15:30:00+00:00",
    )
    avail = {
        "available_slots": [
            {
                "start": "2026-05-15T15:00:00+00:00",
                "end": "2026-05-15T15:30:00+00:00",
                "display": "03:00 PM",
            }
        ],
    }
    svc._handle_confirmation = AsyncMock()
    result = await svc._try_auto_book_from_explicit_slot(
        "c1", intent, {}, "primary", "UTC", avail, participant_email="guest@example.com"
    )
    assert result is None
    svc._handle_confirmation.assert_not_called()


def test_missing_booking_fields_guest_requires_name_and_email_pair() -> None:
    assert "meeting_title" in SchedulingService.missing_booking_fields(
        SchedulingIntent(
            intent=Intent.CONFIRM,
            time_preference=TimePreference.FLEXIBLE,
            confidence=1.0,
            meeting_title="",
            participant_email="a@b.com",
            attendee_name="Ann",
        )
    )
    assert "attendee_name" in SchedulingService.missing_booking_fields(
        SchedulingIntent(
            intent=Intent.CONFIRM,
            time_preference=TimePreference.FLEXIBLE,
            confidence=1.0,
            meeting_title="Sync",
            participant_email="a@b.com",
            attendee_name=None,
        )
    )
    assert "participant_email" in SchedulingService.missing_booking_fields(
        SchedulingIntent(
            intent=Intent.CONFIRM,
            time_preference=TimePreference.FLEXIBLE,
            confidence=1.0,
            meeting_title="Sync",
            participant_email=None,
            attendee_name="Ann",
        )
    )
    assert (
        SchedulingService.missing_booking_fields(
            SchedulingIntent(
                intent=Intent.CONFIRM,
                time_preference=TimePreference.FLEXIBLE,
                confidence=1.0,
                meeting_title="Sync",
                participant_email="a@b.com",
                attendee_name="Ann",
            )
        )
        == []
    )


@pytest.mark.asyncio
async def test_handle_confirmation_returns_needs_booking_details_without_title() -> None:
    svc = SchedulingService()
    svc.conversation_states["c1"] = {
        "intent": SchedulingIntent(
            intent=Intent.CONFIRM,
            time_preference=TimePreference.FLEXIBLE,
            confidence=1.0,
            meeting_title=None,
        ),
        "available_slots": [
            {
                "start": "2026-06-01T14:00:00+00:00",
                "end": "2026-06-01T14:30:00+00:00",
                "duration": 30,
            }
        ],
        "selected_slot": None,
        "confirmed": False,
        "awaiting_final_user_confirm": False,
    }
    out = await svc._handle_confirmation(
        "c1",
        {"token": "t"},
        "primary",
        "UTC",
        selected_slot_start="2026-06-01T14:00:00+00:00",
        participant_email=None,
    )
    assert out["status"] == "needs_booking_details"
    assert "meeting_title" in out["missing_fields"]
    assert out["available_slots"] and out["available_slots"][0]["display"]
    assert "You've selected" in out["message"]


@pytest.mark.asyncio
async def test_handle_confirmation_ready_to_book_then_yes_creates_event() -> None:
    svc = SchedulingService()
    svc.calendar_service.create_event = AsyncMock(
        return_value={
            "event_id": "e1",
            "event_link": "https://calendar.example/e",
            "summary": "Review",
            "start": "2026-06-01T14:00:00+00:00",
            "end": "2026-06-01T14:30:00+00:00",
        }
    )
    intent = SchedulingIntent(
        intent=Intent.CONFIRM,
        time_preference=TimePreference.FLEXIBLE,
        confidence=1.0,
        meeting_title="Review",
        participant_email="guest@example.com",
        attendee_name="Guest User",
    )
    svc.conversation_states["c3"] = {
        "intent": intent,
        "available_slots": [
            {
                "start": "2026-06-01T14:00:00+00:00",
                "end": "2026-06-01T14:30:00+00:00",
                "duration": 30,
            }
        ],
        "selected_slot": None,
        "confirmed": False,
        "awaiting_final_user_confirm": False,
    }
    first = await svc._handle_confirmation(
        "c3",
        {"token": "t"},
        "primary",
        "UTC",
        selected_slot_start="2026-06-01T14:00:00+00:00",
        participant_email=None,
        user_message="I'd like to confirm the time slot: …",
    )
    assert first["status"] == "ready_to_book"
    assert "Here's what I'll book" in first["message"]
    svc.calendar_service.create_event.assert_not_called()

    second = await svc._handle_confirmation(
        "c3",
        {"token": "t"},
        "primary",
        "UTC",
        selected_slot_start="2026-06-01T14:00:00+00:00",
        participant_email=None,
        user_message="yes",
    )
    assert second["status"] == "confirmed"
    svc.calendar_service.create_event.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_confirmation_ambiguous_after_preview_stays_ready() -> None:
    svc = SchedulingService()
    svc.calendar_service.create_event = AsyncMock(
        return_value={
            "event_id": "e1",
            "event_link": "https://calendar.example/e",
            "summary": "Sync",
            "start": "2026-06-02T10:00:00+00:00",
            "end": "2026-06-02T10:30:00+00:00",
        }
    )
    intent = SchedulingIntent(
        intent=Intent.CONFIRM,
        time_preference=TimePreference.FLEXIBLE,
        confidence=1.0,
        meeting_title="Sync",
        participant_email="a@b.com",
        attendee_name="Ann",
    )
    svc.conversation_states["c4"] = {
        "intent": intent,
        "available_slots": [
            {
                "start": "2026-06-02T10:00:00+00:00",
                "end": "2026-06-02T10:30:00+00:00",
                "duration": 30,
            }
        ],
        "selected_slot": None,
        "confirmed": False,
        "awaiting_final_user_confirm": False,
    }
    await svc._handle_confirmation(
        "c4",
        {"token": "t"},
        "primary",
        "UTC",
        selected_slot_start="2026-06-02T10:00:00+00:00",
    )
    vague = await svc._handle_confirmation(
        "c4",
        {"token": "t"},
        "primary",
        "UTC",
        selected_slot_start="2026-06-02T10:00:00+00:00",
        user_message="maybe change the title",
    )
    assert vague["status"] == "ready_to_book"
    assert "didn't catch" in vague["message"].lower()
    svc.calendar_service.create_event.assert_not_called()


@pytest.mark.asyncio
async def test_availability_check_resets_awaiting_final_confirm() -> None:
    svc = SchedulingService()
    svc.conversation_states["c5"] = {
        "intent": None,
        "available_slots": [],
        "selected_slot": None,
        "confirmed": False,
        "awaiting_final_user_confirm": True,
    }

    async def fake_get_slots_across_days(*_a, **_kw):
        return [
            AvailableSlot(
                start_time="2026-05-20T10:00:00+00:00",
                end_time="2026-05-20T10:30:00+00:00",
                duration_minutes=30,
            )
        ]

    svc.availability_service.get_slots_across_days = fake_get_slots_across_days

    intent = SchedulingIntent(
        intent=Intent.CHECK_AVAILABILITY,
        date="2026-05-20",
        time_preference=TimePreference.FLEXIBLE,
        confidence=1.0,
    )
    await svc._handle_availability_check(
        "c5", intent, {}, "primary", "UTC"
    )
    assert svc.conversation_states["c5"]["awaiting_final_user_confirm"] is False


@pytest.mark.parametrize(
    ("msg", "want"),
    [
        ("show me available times", True),
        ("What times work Tuesday?", True),
        ("when can we meet please show slots", True),
        ("book a sync tomorrow afternoon", False),
        ("", False),
        ("   ", False),
    ],
)
def test_user_wants_slot_display(msg: str, want: bool) -> None:
    assert SchedulingService.user_wants_slot_display(msg) is want


def test_filter_strips_slots_for_schedule_without_display_phrase() -> None:
    svc = SchedulingService()
    intent = SchedulingIntent(
        intent=Intent.SCHEDULE,
        date="2026-05-20",
        time_preference=TimePreference.AFTERNOON,
        confidence=1.0,
    )
    avail = {
        "status": "available_slots",
        "available_slots": [
            {
                "start": "2026-05-20T15:00:00+00:00",
                "end": "2026-05-20T15:30:00+00:00",
                "display": "3:00 PM",
            }
        ],
        "requires_confirmation": True,
    }
    out = svc._filter_slot_payload_for_intent(
        intent, avail, "I'd like a 30 min meeting tomorrow afternoon"
    )
    assert out["status"] == "available_slots"
    assert out["available_slots"] is None


def test_filter_keeps_slots_for_check_availability() -> None:
    svc = SchedulingService()
    intent = SchedulingIntent(
        intent=Intent.CHECK_AVAILABILITY,
        date="2026-05-20",
        time_preference=TimePreference.FLEXIBLE,
        confidence=1.0,
    )
    slots = [
        {
            "start": "2026-05-20T15:00:00+00:00",
            "end": "2026-05-20T15:30:00+00:00",
            "display": "3:00 PM",
        }
    ]
    avail = {"status": "available_slots", "available_slots": slots, "requires_confirmation": True}
    out = svc._filter_slot_payload_for_intent(intent, avail, "anything")
    assert out["available_slots"] == slots


def test_filter_keeps_slots_for_schedule_when_user_asks() -> None:
    svc = SchedulingService()
    intent = SchedulingIntent(
        intent=Intent.SCHEDULE,
        date="2026-05-20",
        time_preference=TimePreference.AFTERNOON,
        confidence=1.0,
    )
    slots = [
        {
            "start": "2026-05-20T15:00:00+00:00",
            "end": "2026-05-20T15:30:00+00:00",
            "display": "3:00 PM",
        }
    ]
    avail = {"status": "available_slots", "available_slots": slots, "requires_confirmation": True}
    out = svc._filter_slot_payload_for_intent(
        intent, avail, "Please show available times for that day"
    )
    assert out["available_slots"] == slots
