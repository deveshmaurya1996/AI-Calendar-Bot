from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
from enum import Enum

class TimePreference(str, Enum):
    MORNING = "morning"
    AFTERNOON = "afternoon"
    EVENING = "evening"
    FLEXIBLE = "flexible"

class Intent(str, Enum):
    SCHEDULE = "schedule"
    CHECK_AVAILABILITY = "check_availability"
    CONFIRM = "confirm"
    CANCEL = "cancel"
    RESCHEDULE = "reschedule"

class SchedulingIntent(BaseModel):
    intent: Intent
    date: Optional[str] = Field(default=None, description="YYYY-MM-DD format")
    time_preference: Optional[TimePreference] = Field(default=None)
    duration_minutes: Optional[int] = Field(default=30, ge=15, le=480)
    meeting_title: Optional[str] = Field(default=None)
    participant_email: Optional[str] = Field(
        default=None,
        description="Guest email when mentioned in chat or merged from the UI.",
    )
    attendee_name: Optional[str] = Field(
        default=None,
        description="Guest display name for the calendar invite when inviting someone else.",
    )
    meeting_description: Optional[str] = Field(
        default=None,
        description="Optional agenda or notes for the calendar event body.",
    )
    timezone: Optional[str] = Field(default=None)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    slot_start_iso: Optional[str] = Field(
        default=None,
        description="RFC3339 start when user gave an exact wall time (chat booking without UI slot).",
    )
    slot_end_iso: Optional[str] = Field(
        default=None,
        description="RFC3339 end matching slot_start_iso and duration.",
    )

class ChatMessage(BaseModel):
    role: str
    content: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)

class ChatRequest(BaseModel):
    conversation_id: str
    message: str
    user_timezone: Optional[str] = "UTC"
    google_refresh_token: Optional[str] = None
    selected_slot_start: Optional[str] = Field(
        default=None,
        description="ISO start time of the slot to book when intent is confirm (from UI or client).",
    )
    meeting_title: Optional[str] = None
    participant_email: Optional[str] = None
    meeting_description: Optional[str] = Field(
        default=None,
        description="Optional agenda or notes (from UI or chat).",
    )
    attendee_name: Optional[str] = Field(
        default=None,
        description="Guest display name (from UI or chat) for Calendar attendees.displayName.",
    )


class SlotSuggestion(BaseModel):
    start: str
    end: str
    display: str


class ChatResponse(BaseModel):
    conversation_id: str
    intent: SchedulingIntent
    message: str
    available_slots: Optional[List[SlotSuggestion]] = None
    requires_confirmation: bool = False
    error: Optional[str] = None
    event_link: Optional[str] = Field(
        None,
        description="Google Calendar event URL when confirm created an event via API.",
    )
    missing_fields: Optional[List[str]] = Field(
        default=None,
        description="When status is needs_booking_details, which fields are still required.",
    )

class AvailableSlot(BaseModel):
    start_time: str
    end_time: str
    duration_minutes: int

class AvailabilityResponse(BaseModel):
    date: str
    slots: List[AvailableSlot]
    timezone: str

class ConfirmationRequest(BaseModel):
    conversation_id: str
    selected_slot: str
    meeting_title: str
    participant_email: Optional[str] = Field(
        default=None,
        description="Guest invite email; omit to use the connected Google account email (self-booking).",
    )
    meeting_description: Optional[str] = Field(
        default=None,
        description="Optional agenda or notes stored in the event description.",
    )
    attendee_name: Optional[str] = Field(
        default=None,
        description="Guest display name on the invite when participant_email is set.",
    )
    user_timezone: Optional[str] = "UTC"
    google_refresh_token: Optional[str] = None

class ConfirmationResponse(BaseModel):
    success: bool
    event_id: str
    event_link: str
    message: str


class CalendarContextResponse(BaseModel):
    configured: bool
    calendar_id_requested: Optional[str] = None
    calendar_id_resolved: Optional[str] = None
    oauth_account_email: Optional[str] = None
    oauth_account_name: Optional[str] = Field(
        default=None,
        description="Google account display name from userinfo, when available.",
    )
    summary: Optional[str] = None
    time_zone: Optional[str] = None
    access_role: Optional[str] = None
    primary: Optional[bool] = None
    booking_page_url: Optional[str] = Field(
        None,
        description="Google Appointment Scheduling URL when GOOGLE_APPOINTMENT_SCHEDULE_URL is set.",
    )
    error: Optional[str] = None


class GoogleAuthStartResponse(BaseModel):
    authorization_url: str
    state: str


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=256)
    password: str = Field(..., min_length=1, max_length=512)


class LoginResponse(BaseModel):
    username: str


class MeResponse(BaseModel):
    auth_configured: bool
    authenticated: bool
    username: str | None = None
    can_link_server_calendar: bool = False


class LogoutResponse(BaseModel):
    ok: bool = True


class DisconnectCalendarResponse(BaseModel):
    ok: bool = True
    had_token: bool = False