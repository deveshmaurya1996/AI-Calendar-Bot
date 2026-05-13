from app.integrations.pollinations_client import PollinationsClient
from app.api.schemas import SchedulingIntent
from app.logger import setup_logger
from typing import Any, List, Optional

logger = setup_logger(__name__)


class AIService:
    """LLM wrapper; slot times are shown only as structured UI (SlotSelector), not in prose."""

    _SLOTS_FALLBACK_NO_TIMES = (
        "I found openings in your calendar for the next few days. "
        "Pick a time using the buttons below, then share the meeting title, "
        "guest name and email if someone else should be invited, and an optional description."
    )

    def __init__(self):
        self.llm = PollinationsClient()

    async def refine_response_with_slots(
        self,
        user_message: str,
        intent: SchedulingIntent,
        conversation_history: Optional[List[dict]],
        raw_slots: list[dict[str, Any]],
    ) -> str:
        try:
            text = await self.llm.generate_response(
                user_message,
                intent,
                available_slots=raw_slots,
                conversation_history=conversation_history,
                is_opening_turn=False,
                slots_grounding=True,
            )
            if text and text.strip():
                return text.strip()
        except Exception as e:
            logger.warning("refine_response_with_slots failed: %s", e)
        return self._SLOTS_FALLBACK_NO_TIMES

    async def process_user_message(
        self,
        user_message: str,
        conversation_history: Optional[List[dict]] = None,
        user_timezone: str = "UTC",
    ) -> tuple[SchedulingIntent, str]:
        try:
            if conversation_history is None:
                conversation_history = []

            intent = await self.llm.extract_scheduling_intent(
                user_message,
                conversation_history,
                user_timezone,
            )

            response = await self.llm.generate_response(
                user_message,
                intent,
                conversation_history=conversation_history,
                is_opening_turn=len(conversation_history) == 0,
            )

            logger.info(
                "AI processing complete. Intent: %s, Response generated", intent.intent
            )

            return intent, response

        except Exception as e:
            logger.error("Error in AI service: %s", e)
            raise