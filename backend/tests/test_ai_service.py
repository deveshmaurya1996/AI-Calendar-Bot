"""AIService: schema checks and slot-refinement behavior."""

import types

import pytest

from app.api.schemas import Intent, SchedulingIntent, TimePreference
from app.services.ai_service import AIService


def test_scheduling_intent_defaults() -> None:
    i = SchedulingIntent(intent=Intent.SCHEDULE, date="2026-05-12")
    assert i.duration_minutes == 30
    assert i.time_preference is None


@pytest.mark.asyncio
async def test_refine_response_with_slots_does_not_append_time_bullets() -> None:
    svc = AIService()
    captured: dict = {}

    async def fake_generate_response(
        self,
        user_message,
        intent,
        available_slots=None,
        conversation_history=None,
        *,
        is_opening_turn: bool = False,
        slots_grounding: bool = False,
    ) -> str:
        captured["slots_grounding"] = slots_grounding
        captured["has_slots"] = bool(available_slots)
        return "Great — I checked the calendar."

    svc.llm.generate_response = types.MethodType(fake_generate_response, svc.llm)

    intent = SchedulingIntent(
        intent=Intent.SCHEDULE,
        time_preference=TimePreference.AFTERNOON,
        confidence=0.9,
    )
    raw = [
        {
            "start": "2026-05-15T15:00:00+00:00",
            "end": "2026-05-15T15:30:00+00:00",
            "display": "03:00 PM on Thursday, May 15",
        }
    ]
    out = await svc.refine_response_with_slots("tomorrow afternoon", intent, [], raw)
    assert captured.get("slots_grounding") is True
    assert captured.get("has_slots") is True
    assert out == "Great — I checked the calendar."
    assert "03:00 PM on Thursday, May 15" not in out


@pytest.mark.asyncio
async def test_refine_response_with_slots_fallback_has_no_time_list() -> None:
    svc = AIService()

    async def boom(
        self,
        user_message,
        intent,
        available_slots=None,
        conversation_history=None,
        *,
        is_opening_turn: bool = False,
        slots_grounding: bool = False,
    ) -> str:
        raise RuntimeError("unavailable")

    svc.llm.generate_response = types.MethodType(boom, svc.llm)

    intent = SchedulingIntent(
        intent=Intent.SCHEDULE,
        time_preference=TimePreference.AFTERNOON,
        confidence=0.9,
    )
    raw = [
        {
            "start": "2026-05-15T15:00:00+00:00",
            "end": "2026-05-15T15:30:00+00:00",
            "display": "03:00 PM on Thursday, May 15",
        }
    ]
    out = await svc.refine_response_with_slots("x", intent, [], raw)
    assert "03:00 PM" not in out
    assert "buttons below" in out.lower() or "openings" in out.lower()
