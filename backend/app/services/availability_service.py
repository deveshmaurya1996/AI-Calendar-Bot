from datetime import date, datetime, timedelta
from typing import List, Optional
import pytz
from app.logger import setup_logger
from app.integrations.google_calendar import GoogleCalendarService
from app.core.exceptions import NoAvailableSlotsError
from app.api.schemas import AvailableSlot

logger = setup_logger(__name__)
google_calendar = GoogleCalendarService()

class AvailabilityService:
    SLOT_DURATION = 30  # minutes
    WORKING_HOURS_START = 9  # 9 AM
    WORKING_HOURS_END = 18  # 6 PM
    
    async def get_available_slots(
        self,
        credentials: dict,
        calendar_id: str,
        date: str,
        time_preference: Optional[str] = "flexible",
        duration_minutes: int = 30,
        timezone: str = "UTC"
    ) -> List[AvailableSlot]:
        try:
            tz = pytz.timezone(timezone)
            target_date = datetime.strptime(date, "%Y-%m-%d")
            target_date = tz.localize(target_date)
            
            start_of_day = target_date.replace(hour=self.WORKING_HOURS_START, minute=0, second=0)
            end_of_day = target_date.replace(hour=self.WORKING_HOURS_END, minute=0, second=0)
            
            if time_preference == "morning":
                end_of_day = target_date.replace(hour=12, minute=0, second=0)
            elif time_preference == "afternoon":
                start_of_day = target_date.replace(hour=12, minute=0, second=0)
                end_of_day = target_date.replace(hour=17, minute=0, second=0)
            elif time_preference == "evening":
                start_of_day = target_date.replace(hour=17, minute=0, second=0)
            
            busy_blocks = await google_calendar.get_free_busy_info(
                credentials,
                calendar_id,
                start_of_day,
                end_of_day
            )
            
            all_slots = self._generate_time_slots(start_of_day, end_of_day, duration_minutes)
            
            available_slots = self._filter_available_slots(all_slots, busy_blocks)
            
            if not available_slots:
                raise NoAvailableSlotsError(
                    f"No {duration_minutes}-minute slots available on {date}"
                )
            
            logger.info(f"Found {len(available_slots)} available slots")
            return available_slots
            
        except NoAvailableSlotsError:
            raise
        except Exception as e:
            logger.error(f"Error getting available slots: {str(e)}")
            raise
    
    async def get_slots_across_days(
        self,
        credentials: dict,
        calendar_id: str,
        start_day: date,
        num_days: int,
        time_preference: Optional[str] = "flexible",
        duration_minutes: int = 30,
        timezone: str = "UTC",
        max_slots: int = 12,
    ) -> List[AvailableSlot]:
        collected: List[AvailableSlot] = []
        num_days = max(1, min(num_days, 7))

        for i in range(num_days):
            day = start_day + timedelta(days=i)
            date_str = day.strftime("%Y-%m-%d")
            try:
                day_slots = await self.get_available_slots(
                    credentials,
                    calendar_id,
                    date_str,
                    time_preference,
                    duration_minutes,
                    timezone,
                )
                collected.extend(day_slots)
            except NoAvailableSlotsError:
                logger.info("No slots on %s; continuing multi-day search", date_str)
                continue

        if not collected:
            raise NoAvailableSlotsError(
                f"No {duration_minutes}-minute openings in the {num_days} calendar day(s) "
                f"starting {start_day.isoformat()} (working hours, with your time preference)."
            )

        collected.sort(key=lambda s: s.start_time)
        return collected[:max_slots]

    def _generate_time_slots(
        self,
        start: datetime,
        end: datetime,
        duration_minutes: int
    ) -> List[tuple]:
        slots = []
        current = start
        step_minutes = 15 if duration_minutes <= 30 else duration_minutes

        while current + timedelta(minutes=duration_minutes) <= end:
            slot_end = current + timedelta(minutes=duration_minutes)
            slots.append((current, slot_end))
            current += timedelta(minutes=step_minutes)
        
        return slots
    
    def _filter_available_slots(
        self,
        all_slots: List[tuple],
        busy_blocks: List[dict]
    ) -> List[AvailableSlot]:
        available = []
        
        for slot_start, slot_end in all_slots:
            is_available = True
            
            for busy_block in busy_blocks:
                busy_start = datetime.fromisoformat(busy_block['start'].replace('Z', '+00:00'))
                busy_end = datetime.fromisoformat(busy_block['end'].replace('Z', '+00:00'))
                
                if not (slot_end <= busy_start or slot_start >= busy_end):
                    is_available = False
                    break
            
            if is_available:
                available.append(AvailableSlot(
                    start_time=slot_start.isoformat(),
                    end_time=slot_end.isoformat(),
                    duration_minutes=int((slot_end - slot_start).total_seconds() / 60)
                ))
        
        return available