import re
from datetime import datetime, timedelta
from typing import Optional

import pytz
from app.logger import setup_logger
from app.config import get_settings
from app.integrations.google_calendar import GoogleCalendarService
from app.services.availability_service import AvailabilityService
from app.api.schemas import Intent, SchedulingIntent, TimePreference
from app.core.exceptions import ConflictError, InvalidTimeError, NoAvailableSlotsError

logger = setup_logger(__name__)

_SLOT_DISPLAY_PHRASES = re.compile(
    r"|".join(
        [
            r"\bshow\s+(?:me\s+)?(?:the\s+)?(?:available\s+)?(?:times?|slots?|openings?)\b",
            r"\bwhat\s+(?:times?|slots?|openings?)\b",
            r"\bwhen\s+(?:are\s+you|am\s+i|can\s+we)\s+(?:free|available)\b",
            r"\blist\s+(?:the\s+)?(?:available\s+)?(?:times?|slots?)\b",
            r"\bavailable\s+(?:times?|slots?|openings?)\b",
            r"\bcheck\s+(?:your\s+)?(?:calendar\s+)?(?:for\s+)?(?:open\s+)?(?:times?|slots?|availability)\b",
            r"\bsee\s+(?:the\s+)?(?:available\s+)?(?:times?|slots?|options?)\b",
            r"\bopen\s+times?\b",
            r"\btimes?\s+again\b",
            r"\bwhich\s+times?\b",
            r"\bany\s+openings?\b",
        ]
    ),
    re.IGNORECASE,
)


class SchedulingService:
    def __init__(self):
        self.calendar_service = GoogleCalendarService()
        self.availability_service = AvailabilityService()
        self.conversation_states = {}

    @staticmethod
    def user_wants_slot_display(user_message: str | None) -> bool:
        if not user_message or not str(user_message).strip():
            return False
        return bool(_SLOT_DISPLAY_PHRASES.search(str(user_message)))

    def should_expose_slots_in_chat(
        self, intent: SchedulingIntent, user_message: str | None
    ) -> bool:
        if intent.intent == Intent.CHECK_AVAILABILITY:
            return True
        if intent.intent == Intent.SCHEDULE:
            return self.user_wants_slot_display(user_message)
        return False

    def slots_ui_for_session(
        self, conversation_id: str, user_timezone: str
    ) -> list[dict[str, str]] | None:
        state = self.conversation_states.get(conversation_id)
        if not state:
            return None
        slots = self._slots_with_display(state, user_timezone)
        return slots if slots else None

    def _filter_slot_payload_for_intent(
        self,
        intent: SchedulingIntent,
        avail: dict,
        user_message: str | None,
    ) -> dict:
        if avail.get("status") != "available_slots":
            return avail
        if self.should_expose_slots_in_chat(intent, user_message):
            return avail
        out = dict(avail)
        out["available_slots"] = None
        return out

    async def process_scheduling_request(
        self,
        conversation_id: str,
        intent: SchedulingIntent,
        credentials: dict,
        calendar_id: str,
        user_timezone: str = "UTC",
        *,
        selected_slot_start: str | None = None,
        participant_email: str | None = None,
        meeting_title: str | None = None,
        meeting_description: str | None = None,
        attendee_name: str | None = None,
        user_message: str | None = None,
    ) -> dict:
        try:
            if conversation_id not in self.conversation_states:
                self.conversation_states[conversation_id] = {
                    "intent": None,
                    "available_slots": [],
                    "selected_slot": None,
                    "confirmed": False,
                    "awaiting_final_user_confirm": False,
                }
            
            state = self.conversation_states[conversation_id]
            state['intent'] = intent
            
            if intent.intent.value == "confirm":
                if meeting_title and meeting_title.strip():
                    intent.meeting_title = meeting_title.strip()
                if meeting_description and meeting_description.strip():
                    intent.meeting_description = meeting_description.strip()
                if participant_email and participant_email.strip():
                    intent.participant_email = participant_email.strip()
                if attendee_name and attendee_name.strip():
                    intent.attendee_name = attendee_name.strip()
                state["intent"] = intent
                email = (
                    participant_email.strip()
                    if participant_email and participant_email.strip()
                    else None
                ) or (
                    intent.participant_email.strip()
                    if intent.participant_email and intent.participant_email.strip()
                    else None
                )
                slot_start = (
                    selected_slot_start.strip()
                    if selected_slot_start and selected_slot_start.strip()
                    else None
                )
                return await self._handle_confirmation(
                    conversation_id,
                    credentials,
                    calendar_id,
                    user_timezone,
                    selected_slot_start=slot_start,
                    participant_email=email,
                    user_message=user_message,
                )
            
            if intent.intent.value in ("check_availability", "schedule"):
                if (
                    intent.intent == Intent.SCHEDULE
                    and intent.date is None
                    and (
                        intent.time_preference is None
                        or intent.time_preference == TimePreference.FLEXIBLE
                    )
                ):
                    return {
                        "status": "needs_preferences",
                        "available_slots": [],
                        "requires_confirmation": False,
                    }
                avail = await self._handle_availability_check(
                    conversation_id, intent, credentials, calendar_id, user_timezone
                )
                if intent.intent == Intent.SCHEDULE:
                    booked = await self._try_auto_book_from_explicit_slot(
                        conversation_id,
                        intent,
                        credentials,
                        calendar_id,
                        user_timezone,
                        avail,
                        participant_email=participant_email,
                    )
                    if booked is not None:
                        return booked
                return self._filter_slot_payload_for_intent(
                    intent, avail, user_message
                )
            
            return {
                'status': 'pending',
                'message': 'Please provide more details about your scheduling needs.'
            }
            
        except Exception as e:
            logger.error(f"Error processing scheduling request: {str(e)}")
            raise
    
    async def _handle_availability_check(
        self,
        conversation_id: str,
        intent: SchedulingIntent,
        credentials: dict,
        calendar_id: str,
        user_timezone: str
    ) -> dict:
        try:
            settings = get_settings()
            window_days = max(2, min(settings.AVAILABILITY_WINDOW_DAYS, 3))

            tz = pytz.timezone(user_timezone)
            today_local = datetime.now(tz).date()

            if intent.date:
                start_day = datetime.strptime(intent.date, "%Y-%m-%d").date()
                if start_day < today_local:
                    start_day = today_local
            else:
                start_day = today_local + timedelta(days=1)

            pref = (
                intent.time_preference.value
                if intent.time_preference
                else "flexible"
            )
            duration = intent.duration_minutes or settings.SLOT_DURATION_MINUTES

            slots = await self.availability_service.get_slots_across_days(
                credentials,
                calendar_id,
                start_day,
                window_days,
                pref,
                duration,
                user_timezone,
                max_slots=12,
            )

            self.conversation_states[conversation_id]['available_slots'] = [
                {
                    'start': slot.start_time,
                    'end': slot.end_time,
                    'duration': slot.duration_minutes,
                }
                for slot in slots
            ]
            self.conversation_states[conversation_id][
                "awaiting_final_user_confirm"
            ] = False

            end_day = start_day + timedelta(days=window_days - 1)

            return {
                'status': 'available_slots',
                'available_slots': [
                    {
                        'start': slot.start_time,
                        'end': slot.end_time,
                        'display': self._format_slot_display(
                            slot.start_time, user_timezone
                        ),
                    }
                    for slot in slots
                ],
                'date_range': {
                    'start': start_day.isoformat(),
                    'end': end_day.isoformat(),
                    'days': window_days,
                },
                'requires_confirmation': True,
            }

        except Exception as e:
            logger.error(f"Error checking availability: {str(e)}")
            raise

    @staticmethod
    def missing_booking_fields(intent: SchedulingIntent) -> list[str]:
        """Required: meeting title. Guest invite: both email and attendee name."""
        missing: list[str] = []
        title = (intent.meeting_title or "").strip()
        if not title:
            missing.append("meeting_title")
        guest_email = (intent.participant_email or "").strip()
        guest_name = (intent.attendee_name or "").strip()
        if guest_email and not guest_name:
            missing.append("attendee_name")
        if guest_name and not guest_email:
            missing.append("participant_email")
        return missing

    def _slots_with_display(
        self, state: dict, user_timezone: str
    ) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        for s in state.get("available_slots") or []:
            st = str(s.get("start", ""))
            out.append(
                {
                    "start": st,
                    "end": str(s.get("end", "")),
                    "display": self._format_slot_display(st, user_timezone),
                }
            )
        return out

    def _needs_booking_details_message(self, missing: list[str]) -> str:
        labels = {
            "meeting_title": "a **meeting title**",
            "attendee_name": "the **guest's name** (as it should appear on the invite)",
            "participant_email": "the **guest's email**",
        }
        parts = [labels[m] for m in missing if m in labels]
        if not parts:
            parts = ["a few more booking details"]
        if len(parts) == 1:
            joined = parts[0]
        else:
            joined = ", ".join(parts[:-1]) + " and " + parts[-1]
        return (
            f"Almost there — I still need {joined}. "
            "You can reply in one message (optional **description** or agenda for the event body). "
            "When everything is set, tap **Confirm this time in chat** again or say **confirm**."
        )

    @staticmethod
    def _is_final_affirmation(msg: str | None) -> bool:
        if not msg or not str(msg).strip():
            return False
        t_raw = str(msg).strip().lower()
        t = re.sub(r"[!?.]+$", "", t_raw).strip()
        allow = {
            "yes",
            "y",
            "confirm",
            "book it",
            "go ahead",
            "create it",
            "ok",
            "okay",
            "sure",
            "do it",
            "please",
        }
        if t in allow:
            return True
        for prefix in ("yes ", "confirm ", "ok ", "y "):
            if t.startswith(prefix):
                return True
        return False

    def _slot_selection_ack_line(
        self,
        state: dict,
        selected_slot_start: str | None,
        user_timezone: str,
    ) -> str:
        if not selected_slot_start or not str(selected_slot_start).strip():
            return ""
        sel = str(selected_slot_start).strip()
        display: str | None = None
        for s in state.get("available_slots") or []:
            if self._same_instant(str(s.get("start", "")), sel):
                display = self._format_slot_display(str(s.get("start", "")), user_timezone)
                break
        if not display:
            display = self._format_slot_display(sel, user_timezone)
        return f"You've selected **{display}**.\n\n"

    def _compose_ready_to_book_message(
        self,
        *,
        selected: dict,
        intent: SchedulingIntent,
        title_clean: str,
        description: str,
        attendee_email: str,
        used_booker_email_fallback: bool,
        user_timezone: str,
        nudge: bool = False,
    ) -> str:
        time_line = self._format_slot_display(str(selected["start"]), user_timezone)
        guest_email = (intent.participant_email or "").strip()
        guest_name = (intent.attendee_name or "").strip()
        if guest_email:
            guest_line = f"{guest_name} <{guest_email}>" if guest_name else guest_email
        elif used_booker_email_fallback:
            guest_line = f"Your calendar (self) — {attendee_email}"
        else:
            guest_line = attendee_email
        desc_line = (intent.meeting_description or "").strip() or description or "None"
        lines = [
            "Here's what I'll book:",
            "",
            f"• **Time:** {time_line}",
            f"• **Title:** {title_clean}",
            f"• **Guest:** {guest_line}",
            f"• **Description:** {desc_line}",
            "",
            "Reply **yes** or **confirm** to create the event, or send changes "
            "if something looks wrong.",
        ]
        if nudge:
            lines.extend(
                [
                    "",
                    'I didn\'t catch a clear "yes" — reply **yes** to book or type '
                    "what you'd like to change.",
                ]
            )
        return "\n".join(lines)

    async def _try_auto_book_from_explicit_slot(
        self,
        conversation_id: str,
        intent: SchedulingIntent,
        credentials: dict,
        calendar_id: str,
        user_timezone: str,
        avail_result: dict,
        *,
        participant_email: Optional[str],
    ) -> Optional[dict]:
        """If the user already gave a RFC3339 slot + meeting title matching a free slot, book immediately."""
        if intent.intent != Intent.SCHEDULE:
            return None
        override_pe = (participant_email or "").strip() or None
        intent_for_gate = (
            intent.model_copy(update={"participant_email": override_pe})
            if override_pe
            else intent
        )
        if self.missing_booking_fields(intent_for_gate):
            return None
        explicit = self._explicit_slot_from_intent(intent)
        title = (intent.meeting_title or "").strip()
        if not explicit or not title:
            return None
        raw = avail_result.get("available_slots") or []
        for s in raw:
            if self._same_instant(str(s.get("start", "")), explicit["start"]):
                st = self.conversation_states.get(conversation_id)
                if st is not None:
                    st["intent"] = intent
                return await self._handle_confirmation(
                    conversation_id,
                    credentials,
                    calendar_id,
                    user_timezone,
                    selected_slot_start=str(s["start"]),
                    participant_email=participant_email,
                    user_message=None,
                    bypass_final_confirm=True,
                )
        return None

    async def suggest_alternative_slots(
        self,
        credentials: dict,
        calendar_id: str,
        user_timezone: str,
        duration_minutes: int,
        *,
        exclude_start_iso: Optional[str] = None,
        max_suggestions: int = 5,
    ) -> list[dict]:
        """Fresh FreeBusy-based suggestions after a conflict (next few days, flexible preference)."""
        settings = get_settings()
        window = max(2, min(settings.AVAILABILITY_WINDOW_DAYS, 3))
        tz = pytz.timezone(user_timezone)
        today_local = datetime.now(tz).date()
        start_day = today_local + timedelta(days=1)
        try:
            slots = await self.availability_service.get_slots_across_days(
                credentials,
                calendar_id,
                start_day,
                window,
                "flexible",
                duration_minutes,
                user_timezone,
                max_slots=max_suggestions + 8,
            )
        except NoAvailableSlotsError:
            return []
        out: list[dict] = []
        for slot in slots:
            if exclude_start_iso and self._same_instant(
                slot.start_time, exclude_start_iso
            ):
                continue
            out.append(
                {
                    "start": slot.start_time,
                    "end": slot.end_time,
                    "display": self._format_slot_display(
                        slot.start_time, user_timezone
                    ),
                }
            )
            if len(out) >= max_suggestions:
                break
        return out

    @staticmethod
    def _parse_slot_instant(iso_s: str) -> Optional[datetime]:
        if not iso_s or not str(iso_s).strip():
            return None
        s = str(iso_s).strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return None
        if dt.tzinfo is None:
            return pytz.UTC.localize(dt)
        return dt.astimezone(pytz.UTC)

    @staticmethod
    def _same_instant(a: str, b: str) -> bool:
        da = SchedulingService._parse_slot_instant(a)
        db = SchedulingService._parse_slot_instant(b)
        if da is not None and db is not None:
            return da == db
        if a == b:
            return True
        return a.replace("Z", "+00:00") == b.replace("Z", "+00:00")

    @staticmethod
    def _explicit_slot_from_intent(intent: SchedulingIntent) -> Optional[dict]:
        a = (intent.slot_start_iso or "").strip()
        b = (intent.slot_end_iso or "").strip()
        if a and b:
            return {"start": a, "end": b}
        return None

    def _resolve_slot_for_booking(
        self,
        state: dict,
        selected_slot_start: str | None,
        intent: SchedulingIntent,
    ) -> dict:
        """Pick a slot from the last availability list, UI selection, or LLM-provided RFC3339 pair."""
        explicit = self._explicit_slot_from_intent(intent)
        slots = state.get("available_slots") or []

        if selected_slot_start:
            sel = str(selected_slot_start).strip()
            for s in slots:
                if self._same_instant(str(s.get("start", "")), sel):
                    return s
            if explicit and self._same_instant(explicit["start"], sel):
                return explicit
            raise InvalidTimeError(
                "Selected time is not in the current availability list."
            )

        if len(slots) == 1:
            return slots[0]
        if explicit:
            return explicit
        if not slots:
            raise InvalidTimeError(
                "No availability in this session. Ask for open times first, "
                "or include an exact start and end time in your message."
            )
        raise InvalidTimeError("Select a specific time slot before confirming.")

    def _pick_slot(self, state: dict, selected_slot_start: str | None) -> dict:
        """Backward-compatible helper for tests (no explicit intent slots)."""
        placeholder = SchedulingIntent(
            intent=Intent.SCHEDULE,
            time_preference=TimePreference.FLEXIBLE,
            confidence=1.0,
        )
        return self._resolve_slot_for_booking(state, selected_slot_start, placeholder)

    async def _handle_confirmation(
        self,
        conversation_id: str,
        credentials: dict,
        calendar_id: str,
        user_timezone: str,
        *,
        selected_slot_start: str | None = None,
        participant_email: str | None = None,
        user_message: str | None = None,
        bypass_final_confirm: bool = False,
    ) -> dict:
        try:
            state = self.conversation_states.get(conversation_id)
            if not state:
                raise InvalidTimeError("No active scheduling session")

            state.setdefault("awaiting_final_user_confirm", False)

            intent = state["intent"]

            missing = self.missing_booking_fields(intent)
            if missing:
                slots_ui = self._slots_with_display(state, user_timezone)
                ack = self._slot_selection_ack_line(
                    state, selected_slot_start, user_timezone
                )
                body = self._needs_booking_details_message(missing)
                msg = f"{ack}{body}" if ack else body
                return {
                    "status": "needs_booking_details",
                    "missing_fields": missing,
                    "message": msg,
                    "available_slots": slots_ui,
                    "requires_confirmation": True,
                }

            selected = self._resolve_slot_for_booking(
                state, selected_slot_start, intent
            )

            attendee_email = (
                (participant_email or "").strip() or None
            ) or (
                (intent.participant_email or "").strip() or None
            )
            booker_profile: dict[str, str | None] | None = None
            used_booker_email_fallback = False
            if not attendee_email:
                booker_profile = await self.calendar_service.oauth_user_profile(
                    credentials
                )
                em = booker_profile.get("email")
                if isinstance(em, str) and em.strip():
                    attendee_email = em.strip()
                    used_booker_email_fallback = True
            if not attendee_email:
                raise InvalidTimeError(
                    "Could not determine an invitee email. Connect Google Calendar "
                    "so we can use your account, or add a guest email when confirming."
                )

            if intent.meeting_description and intent.meeting_description.strip():
                description = intent.meeting_description.strip()
            elif used_booker_email_fallback and booker_profile:
                name = booker_profile.get("name")
                if isinstance(name, str) and name.strip():
                    description = f"Scheduled for {name.strip()}"
                else:
                    description = "Scheduled meeting"
            else:
                description = "Scheduled meeting"

            title_clean = (intent.meeting_title or "").strip() or "Meeting"
            attendee_row: dict[str, str] = {"email": attendee_email}
            disp = (intent.attendee_name or "").strip()
            if disp:
                attendee_row["displayName"] = disp

            if not bypass_final_confirm:
                if not state["awaiting_final_user_confirm"]:
                    state["awaiting_final_user_confirm"] = True
                    preview = self._compose_ready_to_book_message(
                        selected=selected,
                        intent=intent,
                        title_clean=title_clean,
                        description=description,
                        attendee_email=attendee_email,
                        used_booker_email_fallback=used_booker_email_fallback,
                        user_timezone=user_timezone,
                        nudge=False,
                    )
                    return {
                        "status": "ready_to_book",
                        "message": preview,
                        "available_slots": None,
                        "requires_confirmation": True,
                        "missing_fields": None,
                    }
                if not self._is_final_affirmation(user_message):
                    preview = self._compose_ready_to_book_message(
                        selected=selected,
                        intent=intent,
                        title_clean=title_clean,
                        description=description,
                        attendee_email=attendee_email,
                        used_booker_email_fallback=used_booker_email_fallback,
                        user_timezone=user_timezone,
                        nudge=True,
                    )
                    return {
                        "status": "ready_to_book",
                        "message": preview,
                        "available_slots": None,
                        "requires_confirmation": True,
                        "missing_fields": None,
                    }

            state["awaiting_final_user_confirm"] = False

            event_data = {
                "title": title_clean,
                "description": description,
                "start_time": selected["start"],
                "end_time": selected["end"],
                "timezone": user_timezone,
                "attendees": [attendee_row] if attendee_email else [],
            }

            result = await self.calendar_service.create_event(
                credentials, calendar_id, event_data
            )

            state["confirmed"] = True
            state["selected_slot"] = result["event_id"]

            return {
                "status": "confirmed",
                "event_id": result["event_id"],
                "event_link": result["event_link"],
                "summary": result["summary"],
                "start": result["start"],
                "end": result["end"],
                "message": (
                    f"Meeting confirmed! {result['summary']} at "
                    f"{self._format_slot_display(result['start'], user_timezone)}\n\n"
                    f"Open in Google Calendar: {result['event_link']}"
                ),
            }

        except Exception as e:
            logger.error(f"Error confirming booking: {str(e)}")
            raise
    
    def _format_slot_display(self, iso_time: str, timezone: str) -> str:
        try:
            dt = datetime.fromisoformat(iso_time.replace('Z', '+00:00'))
            tz = pytz.timezone(timezone)
            local_dt = dt.astimezone(tz)
            return local_dt.strftime("%I:%M %p on %A, %B %d")
        except Exception:
            return iso_time