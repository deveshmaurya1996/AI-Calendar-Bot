from fastapi import APIRouter, Depends, HTTPException, Query, Header
from app.api.schemas import (
    CalendarContextResponse,
    ChatRequest,
    ChatResponse,
    ConfirmationRequest,
    ConfirmationResponse,
    SlotSuggestion,
)
from app.services.ai_service import AIService
from app.services.scheduling_service import SchedulingService
from app.services.google_credentials import (
    google_credentials_for_api,
    parse_bearer_authorization,
    server_calendar_credentials,
)
from app.api.deps import require_app_user
from app.config import get_settings
from app.logger import setup_logger
from app.core.exceptions import (
    NoAvailableSlotsError,
    ConflictError,
    InvalidTimeError,
    CalendarAccessError,
)
from typing import Optional

logger = setup_logger(__name__)


def _booking_page_url() -> Optional[str]:
    url = (get_settings().GOOGLE_APPOINTMENT_SCHEDULE_URL or "").strip()
    return url or None


router = APIRouter(prefix="/api", tags=["scheduling"])

ai_service = AIService()
scheduling_service = SchedulingService()

user_sessions = {}


def _resolve_session_credentials(
    session: dict,
    authorization: Optional[str],
    google_refresh_token: Optional[str],
) -> None:
    access_token = parse_bearer_authorization(authorization)
    if access_token:
        session["credentials"] = google_credentials_for_api(
            access_token, google_refresh_token
        )
        return
    server = server_calendar_credentials()
    if server:
        session["credentials"] = server
        return
    session["credentials"] = None


@router.post("/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    _user: str = Depends(require_app_user),
    authorization: Optional[str] = Header(default=None),
):
    """
    Conversational scheduling. Calendar access uses the server refresh token
    saved after **Connect Google Calendar** when no Authorization Bearer header
    is sent (single-calendar mode).
    """
    try:
        logger.info("Chat request for conversation %s", request.conversation_id)
        settings = get_settings()

        if request.conversation_id not in user_sessions:
            user_sessions[request.conversation_id] = {
                "history": [],
                "credentials": None,
                "calendar_id": settings.GOOGLE_CALENDAR_ID,
            }

        session = user_sessions[request.conversation_id]

        _resolve_session_credentials(
            session,
            authorization,
            request.google_refresh_token,
        )

        session["history"].append({"role": "user", "content": request.message})

        intent, ai_response = await ai_service.process_user_message(
            request.message,
            session["history"][:-1],
            request.user_timezone or "UTC",
        )

        available_slots = None
        requires_confirmation = False
        error: Optional[str] = None
        event_link: Optional[str] = None
        missing_fields: Optional[list[str]] = None

        calendar_intents = ("schedule", "check_availability", "confirm")
        if intent.intent.value in calendar_intents:
            if not session["credentials"]:
                ai_response = (
                    f"{ai_response}\n\nI cannot read Google Calendar yet: the server "
                    "has no calendar credentials. Use **Connect Google Calendar** in "
                    "the app (and ensure GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET are "
                    "set), then try again."
                )
                error = "calendar_not_configured"
            else:
                if request.meeting_title and request.meeting_title.strip():
                    intent.meeting_title = request.meeting_title.strip()
                if request.participant_email and request.participant_email.strip():
                    intent.participant_email = request.participant_email.strip()
                if request.meeting_description and request.meeting_description.strip():
                    intent.meeting_description = request.meeting_description.strip()
                if request.attendee_name and request.attendee_name.strip():
                    intent.attendee_name = request.attendee_name.strip()
                try:
                    scheduling_result = await scheduling_service.process_scheduling_request(
                        request.conversation_id,
                        intent,
                        session["credentials"],
                        session["calendar_id"],
                        request.user_timezone or "UTC",
                        selected_slot_start=request.selected_slot_start,
                        participant_email=request.participant_email,
                        meeting_title=request.meeting_title,
                        meeting_description=request.meeting_description,
                        attendee_name=request.attendee_name,
                        user_message=request.message,
                    )

                    if scheduling_result.get("status") == "confirmed":
                        ai_response = scheduling_result.get(
                            "message", ai_response
                        )
                        event_link = scheduling_result.get("event_link")
                        available_slots = None
                        requires_confirmation = False
                        missing_fields = None
                    elif scheduling_result.get("status") == "ready_to_book":
                        ai_response = scheduling_result.get("message", ai_response)
                        available_slots = None
                        requires_confirmation = scheduling_result.get(
                            "requires_confirmation", True
                        )
                        missing_fields = None
                    elif scheduling_result.get("status") == "needs_booking_details":
                        ai_response = scheduling_result.get("message", ai_response)
                        raw_slots = scheduling_result.get("available_slots")
                        if raw_slots:
                            available_slots = [SlotSuggestion(**s) for s in raw_slots]
                        else:
                            available_slots = None
                        requires_confirmation = scheduling_result.get(
                            "requires_confirmation", True
                        )
                        missing_fields = scheduling_result.get("missing_fields")
                    else:
                        raw_slots = scheduling_result.get("available_slots")
                        if raw_slots:
                            available_slots = [SlotSuggestion(**s) for s in raw_slots]
                            ai_response = await ai_service.refine_response_with_slots(
                                request.message,
                                intent,
                                session["history"][:-1],
                                raw_slots,
                            )
                        else:
                            available_slots = None
                        requires_confirmation = scheduling_result.get(
                            "requires_confirmation", False
                        )
                        missing_fields = None
                except InvalidTimeError as e:
                    logger.info("Chat: invalid slot / confirm: %s", e.message)
                    ai_response = f"{ai_response}\n\n{e.message}"
                    error = "invalid_slot"
                    raw_cached = scheduling_service.slots_ui_for_session(
                        request.conversation_id,
                        request.user_timezone or "UTC",
                    )
                    if raw_cached:
                        available_slots = [
                            SlotSuggestion(**s) for s in raw_cached
                        ]
                except ConflictError as e:
                    logger.info("Chat: booking conflict — %s", e.message)
                    alt_msg = e.message
                    if session.get("credentials"):
                        try:
                            alts = await scheduling_service.suggest_alternative_slots(
                                session["credentials"],
                                session["calendar_id"],
                                request.user_timezone or "UTC",
                                intent.duration_minutes or 30,
                                exclude_start_iso=request.selected_slot_start,
                            )
                            if alts:
                                bullets = "\n".join(f"- {a['display']}" for a in alts)
                                alt_msg = (
                                    f"{e.message}\n\n"
                                    "Here are the next closest options:\n"
                                    f"{bullets}"
                                )
                        except Exception as ex:
                            logger.debug("Alternative slot fetch skipped: %s", ex)
                    ai_response = f"{ai_response}\n\n{alt_msg}"
                    error = "conflict"
                except NoAvailableSlotsError as e:
                    logger.info("Chat: no available slots: %s", e.message)
                    ai_response = (
                        f"{ai_response}\n\nI couldn't find openings: {e.message} "
                        "Try another day, a different part of the day (morning/afternoon/evening), "
                        "or a shorter meeting."
                    )
                    error = "no_available_slots"
                except CalendarAccessError as e:
                    logger.warning("Chat: calendar access failed: %s", e.message)
                    ai_response = (
                        f"{ai_response}\n\nI couldn't reach Google Calendar: {e.message} "
                        "Check server calendar configuration and API quotas."
                    )
                    error = "calendar_access"

        session["history"].append({"role": "assistant", "content": ai_response})

        return ChatResponse(
            conversation_id=request.conversation_id,
            intent=intent,
            message=ai_response,
            available_slots=available_slots,
            requires_confirmation=requires_confirmation,
            error=error,
            event_link=event_link,
            missing_fields=missing_fields,
        )

    except Exception as e:
        logger.error(f"Chat error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/schedule/confirm", response_model=ConfirmationResponse)
async def confirm_booking(
    request: ConfirmationRequest,
    _user: str = Depends(require_app_user),
    authorization: Optional[str] = Header(default=None),
):
    try:
        session = user_sessions.get(request.conversation_id)
        if not session:
            raise HTTPException(
                status_code=404,
                detail="No conversation session. Send a chat message first.",
            )

        _resolve_session_credentials(
            session,
            authorization,
            request.google_refresh_token,
        )

        if not session.get("credentials"):
            raise HTTPException(
                status_code=400,
                detail="No calendar credentials: connect Google Calendar in the app (or place "
                "a refresh token in the server token file) or send Authorization: Bearer "
                "with a valid access token.",
            )

        conv_state = scheduling_service.conversation_states.get(request.conversation_id)
        if conv_state and conv_state.get("intent"):
            conv_state["intent"].meeting_title = request.meeting_title
            pe = (
                request.participant_email.strip()
                if request.participant_email and request.participant_email.strip()
                else None
            )
            conv_state["intent"].participant_email = pe
            if request.meeting_description and request.meeting_description.strip():
                conv_state["intent"].meeting_description = (
                    request.meeting_description.strip()
                )
            if request.attendee_name and request.attendee_name.strip():
                conv_state["intent"].attendee_name = request.attendee_name.strip()

        tz = request.user_timezone or "UTC"
        confirm_email = (
            request.participant_email.strip()
            if request.participant_email and request.participant_email.strip()
            else None
        )
        result = await scheduling_service._handle_confirmation(
            request.conversation_id,
            session["credentials"],
            session["calendar_id"],
            tz,
            selected_slot_start=request.selected_slot,
            participant_email=confirm_email,
            user_message=None,
            bypass_final_confirm=True,
        )

        return ConfirmationResponse(
            success=True,
            event_id=result["event_id"],
            event_link=result["event_link"],
            message=result["message"],
        )

    except ConflictError as e:
        logger.info("Confirm: conflict — %s", e.message)
        raise HTTPException(
            status_code=409,
            detail={"code": "conflict", "message": e.message},
        ) from e
    except InvalidTimeError as e:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_slot", "message": e.message},
        ) from e
    except CalendarAccessError as e:
        logger.warning("Confirm: calendar error — %s", e.message)
        raise HTTPException(
            status_code=503,
            detail={"code": "calendar_unavailable", "message": e.message},
        ) from e
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Confirmation error: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/availability")
async def get_availability(
    conversation_id: str = Query(...),
    date: str = Query(...),
    time_preference: Optional[str] = Query(None),
):
    raise HTTPException(
        status_code=410,
        detail="Use POST /api/chat with a scheduling message; slots are returned there.",
    )


@router.get("/calendar/context", response_model=CalendarContextResponse)
async def calendar_context(_user: str = Depends(require_app_user)):
    settings = get_settings()
    calendar_id = settings.GOOGLE_CALENDAR_ID
    booking_url = _booking_page_url()
    creds = server_calendar_credentials()
    if not creds:
        return CalendarContextResponse(
            configured=False,
            calendar_id_requested=calendar_id,
            booking_page_url=booking_url,
            oauth_account_name=None,
            error='No Google refresh token — use "Connect Google Calendar" in the app (token is saved on the server), then restart the API if needed.',
        )
    try:
        meta = await scheduling_service.calendar_service.get_calendar_list_entry(
            creds, calendar_id
        )
        profile = await scheduling_service.calendar_service.oauth_user_profile(creds)
        return CalendarContextResponse(
            configured=True,
            calendar_id_requested=calendar_id,
            calendar_id_resolved=meta.get("id"),
            oauth_account_email=profile.get("email"),
            oauth_account_name=profile.get("name"),
            summary=meta.get("summary"),
            time_zone=meta.get("timeZone"),
            access_role=meta.get("accessRole"),
            primary=meta.get("primary"),
            booking_page_url=booking_url,
        )
    except CalendarAccessError as e:
        return CalendarContextResponse(
            configured=False,
            calendar_id_requested=calendar_id,
            booking_page_url=booking_url,
            oauth_account_name=None,
            error=e.message,
        )


@router.get("/health")
async def health():
    return {"status": "healthy", "version": "1.0.0"}
