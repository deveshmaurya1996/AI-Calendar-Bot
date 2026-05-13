import json
import re
from typing import Any, Optional, Sequence, cast

from openai import APIStatusError, AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam

from app.api.schemas import Intent, SchedulingIntent, TimePreference
from app.config import get_settings
from app.logger import setup_logger

logger = setup_logger(__name__)
settings = get_settings()

ChatHistory = list[dict[str, str]]


def _as_chat_messages(messages: Sequence[dict[str, Any]]) -> list[ChatCompletionMessageParam]:
    return cast(list[ChatCompletionMessageParam], list(messages))


class PollinationsClient:
    """Scheduling LLM via Pollinations OpenAI-compatible API (https://gen.pollinations.ai/docs)."""

    def __init__(self) -> None:
        base = settings.POLLINATIONS_BASE_URL.rstrip("/")
        self.client = AsyncOpenAI(
            api_key=settings.POLLINATIONS_API_KEY,
            base_url=base,
        )
        self.model = settings.POLLINATIONS_MODEL

    async def extract_scheduling_intent(
        self,
        user_message: str,
        conversation_history: Optional[ChatHistory] = None,
        user_timezone: str = "UTC",
    ) -> SchedulingIntent:
        history = conversation_history or []

        system_prompt = """You are an AI assistant that extracts scheduling intentions from user messages.

Your job supports a scheduling chatbot that: (1) talks naturally with the user, (2) collects scheduling preferences, (3) shows available time slots from Google Calendar for roughly the next 2-3 working days (free/busy), (4) lets the user pick a slot and confirm, then (5) completes booking—either on Google's appointment scheduling page in the browser (when that URL is configured) or by creating the calendar event on the server after confirmation.

When the user wants to book or check availability, use intent "schedule" or "check_availability". If they are vague (no day and no part of day like morning/afternoon), set date to null and time_preference to "flexible" (do not guess a specific date). If they name a day but not time of day, set date and time_preference "flexible". If they name only "morning/afternoon/evening" without a date, set time_preference accordingly and date null. The chat will ask a short follow-up when needed.

When the user clearly wants to finalize a time they already discussed or selected (e.g. "book it", "yes book that", "confirm", "schedule that slot", "that's the one"), use intent "confirm"—not "schedule". When they reply with a short final approval after booking details or a booking summary (e.g. "yes", "y", "go ahead", "create it"), use intent "confirm" so the server can complete the booking.

When the user gives a complete booking in one message (intent "confirm" or "schedule") with an explicit wall-clock start and end (e.g. "book tomorrow 9:30am for 1 hour"), set slot_start_iso and slot_end_iso as RFC3339 strings with numeric offset (e.g. +05:30 or Z) for that instant. Use the user's IANA timezone from the user message context line when interpreting local times. Both slot_start_iso and slot_end_iso must be set together or both null.

When the user books for themselves (e.g. "for me", "on my calendar", "put it on my schedule"), leave participant_email null — the app uses the connected Google account. Still set meeting_title if they gave one.

Extract the following information:
- intent: "schedule", "check_availability", "confirm", "cancel", or "reschedule"
- date: in YYYY-MM-DD format (optional if not mentioned — leave null so the app uses its default multi-day window)
- time_preference: "morning", "afternoon", "evening", or "flexible"
- duration_minutes: 15-480 minutes (default 30)
- participant_email: guest invite email if the user gave one (optional)
- attendee_name: guest display name when inviting someone else (optional unless participant_email is set)
- meeting_title: title of the meeting (optional)
- meeting_description: agenda or notes for the event body (optional)
- timezone: timezone string (optional)
- slot_start_iso: RFC3339 start (optional, with slot_end_iso for direct booking)
- slot_end_iso: RFC3339 end (optional)
- confidence: 0.0-1.0 confidence score

You MUST reply with one JSON object only (no markdown), using exactly these keys:
intent, date, time_preference, duration_minutes, participant_email, attendee_name, meeting_title, meeting_description, timezone, slot_start_iso, slot_end_iso, confidence."""

        user_block = (
            f"User message: {user_message}\n"
            f"User IANA timezone for local-time parsing: {user_timezone}"
        )

        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": system_prompt},
            *_as_chat_messages(history),
            {"role": "user", "content": user_block},
        ]

        try:
            response = await self._intent_completion(messages, prefer_json_object=True)
            content = response.choices[0].message.content or "{}"
            logger.info("Pollinations intent response received")

            json_match = re.search(r"\{.*\}", content, re.DOTALL)
            if json_match:
                intent_data = json.loads(json_match.group())
            else:
                intent_data = json.loads(content)

            em = intent_data.get("participant_email")
            em = em.strip() if isinstance(em, str) and em.strip() else None
            desc = intent_data.get("meeting_description")
            desc = desc.strip() if isinstance(desc, str) and desc.strip() else None
            legacy_pn = intent_data.get("participant_name")
            if isinstance(legacy_pn, str) and legacy_pn.strip():
                s = legacy_pn.strip()
                if not em and "@" in s:
                    em = s
                elif not desc:
                    desc = s

            an = intent_data.get("attendee_name")
            if not (isinstance(an, str) and an.strip()):
                an = None
            else:
                an = an.strip()

            intent = SchedulingIntent(
                intent=Intent(intent_data.get("intent") or "schedule"),
                date=intent_data.get("date"),
                time_preference=TimePreference(
                    intent_data.get("time_preference") or "flexible"
                ),
                duration_minutes=int(intent_data.get("duration_minutes") or 30),
                participant_email=em,
                attendee_name=an,
                meeting_title=intent_data.get("meeting_title"),
                meeting_description=desc,
                timezone=intent_data.get("timezone"),
                slot_start_iso=intent_data.get("slot_start_iso"),
                slot_end_iso=intent_data.get("slot_end_iso"),
                confidence=float(intent_data.get("confidence", 0.5)),
            )

            logger.info(f"Extracted Intent: {intent}")
            return intent

        except Exception as e:
            logger.error(f"Error extracting intent: {str(e)}")
            raise Exception(f"Could not extract scheduling intent: {str(e)}") from e

    async def _intent_completion(
        self,
        messages: list[ChatCompletionMessageParam],
        *,
        prefer_json_object: bool,
    ):
        params: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": 512,
            "temperature": 0.2,
        }
        if prefer_json_object:
            params["response_format"] = {"type": "json_object"}
        try:
            return await self.client.chat.completions.create(**params)
        except APIStatusError as e:
            if prefer_json_object and e.status_code in (400, 422):
                logger.info(
                    "Pollinations: retrying intent extraction without response_format "
                    f"(status={e.status_code})"
                )
                params.pop("response_format", None)
                return await self.client.chat.completions.create(**params)
            raise

    async def generate_response(
        self,
        user_message: str,
        intent: SchedulingIntent,
        available_slots: Optional[list[Any]] = None,
        conversation_history: Optional[ChatHistory] = None,
        *,
        is_opening_turn: bool = False,
        slots_grounding: bool = False,
    ) -> str:
        history = conversation_history or []

        opening_rule = ""
        if is_opening_turn:
            opening_rule = (
                "This is the user's first message in this chat. Greet briefly as their scheduling "
                "assistant. Say you can check their Google Calendar for the next few days and list "
                "open times, then help them book. Ask what day or time works for them (e.g. morning "
                "or afternoon). Everything happens in this chat—no separate booking page required. "
                "Then respond to what they said. "
            )

        vague_rule = ""
        if intent.intent.value in ("schedule", "check_availability"):
            tp = (
                intent.time_preference.value
                if intent.time_preference is not None
                else "flexible"
            )
            if intent.date is None and tp == "flexible":
                vague_rule = (
                    "If they did not specify a day or time of day, ask one short clarifying "
                    "question (e.g. which day or morning vs afternoon vs evening). "
                )

        grounding_extra = ""
        if available_slots and slots_grounding:
            grounding_extra = (
                "\n• SLOTS (mandatory): The app shows real times as clickable buttons below your message. "
                "Do NOT list individual times, ranges, or bullet lists of slots in your reply. "
                "You may mention that several options exist or give a high-level date range only. "
                "Direct the user to use the buttons to pick a time, then confirm in chat and provide "
                "meeting title, guest name and email if needed, and optional description.\n"
            )

        system_prompt = f"""You are the conversational layer of a scheduling chatbot. Requirements you must satisfy through your replies:

• Natural language: be concise, friendly, clear.
• Preferences: acknowledge duration, time-of-day, date, and who/what the meeting is for when the user mentions them; if something important is missing, ask once.
• Slots: when the app will check the calendar, say you're looking at the next few days (not a single day). When structured slots exist in context, do not enumerate them—the UI shows buttons; point the user to those buttons.
• Confirm & booking: after they pick a slot (or give a full booking in chat with title, time, and optional guest), they confirm in this chat—the server creates a Google Calendar event and replies with a link.
• Errors: if they mention problems or the context implies no slots, be empathetic and suggest trying another day, time of day, or shorter duration—do not invent slots.
{grounding_extra}
{opening_rule}{vague_rule}Always acknowledge what the user is trying to do."""

        slot_info = ""
        if available_slots:
            slot_info = f"\n\nAvailable slots to suggest:\n{available_slots}"

        tp_display = (
            intent.time_preference.value
            if intent.time_preference is not None
            else "flexible"
        )
        user_context = (
            f"User message: {user_message}\n"
            f"Extracted intent: {intent.intent.value} at {tp_display}\n"
            f"Duration: {intent.duration_minutes} min{slot_info}"
        )

        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": system_prompt},
            *_as_chat_messages(history),
            {"role": "user", "content": user_context},
        ]

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=320,
                temperature=0.7,
            )

            text = response.choices[0].message.content
            if text and text.strip():
                return text.strip()
            return (
                "I'm not sure how to respond to that. Could you tell me when you'd like to meet "
                "and roughly how long you need?"
            )

        except Exception as e:
            logger.error(f"Error generating Pollinations response: {str(e)}")
            return (
                "I'm having trouble reaching the assistant right now. Please try again in a moment, "
                "or rephrase your scheduling request."
            )
