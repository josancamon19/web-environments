import logging
from src.source_data.database import Database
from src.utils.get_iso_datetime import get_iso_datetime
from src.tasks.task import TaskManager
import json
from src.page.actual_page import ActualPage
from src.steps.step import StepManager
from src.utils.get_screenshot_path import get_screenshot_path
from src.steps.step import Step

logger = logging.getLogger(__name__)


class StepRecord:
    def __init__(self):
        self.db = Database.get_instance()
        self.task_manager = TaskManager()
        self.actual_page = ActualPage()
        self.step_manager = StepManager()

    async def record_step(self, step_info: dict, omit_screenshot: bool = False):
        try:
            timestamp = get_iso_datetime()
            actual_task = self.task_manager.get_actual_task()

            if not actual_task:
                logger.error("[RECORD_STEP] No active task found")
                return

            event_info = step_info.get("event_info", {})
            event_type = event_info.get("event_type", "unknown")
            context = event_info.get("event_context", "unknown")

            logger.info(f"[RECORD_STEP] Recording: {context}:{event_type}")

            context_type_action = f"{context}:{event_type}"
            context_type_action_formatted = context_type_action.replace(":", "_")

            screenshot_path = get_screenshot_path(
                actual_task.id, context_type_action_formatted
            )

            # Determine if we should take a screenshot based on event type
            should_screenshot = self._should_take_screenshot(event_type)
            logger.debug(
                f"[RECORD_STEP] Event: {event_type}, Should screenshot: {should_screenshot}, Omit: {omit_screenshot}"
            )

            actual_screenshot_path = ""
            if not omit_screenshot and should_screenshot:
                logger.info(f"[RECORD_STEP] Taking screenshot for {event_type}")
                try:
                    await self.take_screenshot(screenshot_path)
                    actual_screenshot_path = screenshot_path
                    logger.info(f"[RECORD_STEP] Screenshot saved to {screenshot_path}")
                except Exception as e:
                    logger.error(f"[RECORD_STEP] Screenshot failed: {e}")

            # Extract event data safely
            event_data = event_info.get("event_data", {})
            dom_snapshot = event_info.get("dom_snapshot", "")

            # Save to database
            step_id = self.db.insert_step(
                task_id=actual_task.id,
                timestamp=timestamp,
                event_type=context_type_action,
                event_data=json.dumps(event_data, ensure_ascii=False),
                dom_snapshot=dom_snapshot,
                screenshot_path=actual_screenshot_path,
            )

            self.step_manager.set_actual_step(
                Step(
                    id=step_id,
                    task_id=actual_task.id,
                    timestamp=timestamp,
                    event_type=context_type_action,
                    event_data=json.dumps(event_data, ensure_ascii=False),
                    dom_snapshot=dom_snapshot,
                    screenshot_path=actual_screenshot_path,
                )
            )

        except Exception as e:
            logger.error(f"[RECORD_STEP] Failed to record step: {e}", exc_info=True)

    def _should_take_screenshot(self, event_type: str) -> bool:
        """
        Determine if a screenshot should be taken for this event type.
        Skip screenshots for rapid/continuous events to prevent browser jumping.
        """
        # Events that should NOT trigger screenshots (high frequency events)
        skip_screenshot_events = {
            "keydown",  # Individual key presses
            "input",  # Text input events
            "scroll",  # Scrolling events
            "mousemove",  # Mouse movement
            "mousedown",  # Mouse button down
            "mouseup",  # Mouse button up
            "pointerdown",  # Pointer down
            "pointerup",  # Pointer up
            "pointermove",  # Pointer movement
        }

        # Only take screenshots for significant events
        important_events = {
            "click",  # User clicks
            "load",  # Page load
            "navigate_start",  # Navigation start
            "navigated",  # Navigation complete
            "domcontentloaded",  # DOM ready
            "contextmenu",  # Right-click menu
            "loaded",  # Page fully loaded
        }

        # Check if this is an event we should screenshot
        if event_type in important_events:
            return True
        elif event_type in skip_screenshot_events:
            return False
        else:
            # Default to false for unknown events to be safe
            return False

    async def take_screenshot(self, screenshot_path: str):
        """Take a screenshot - using regular method for stability."""
        try:
            logger.debug(f"[SCREENSHOT] Starting screenshot capture")
            page = self.actual_page.get_page()

            # For now, use the regular screenshot method for stability
            # We can optimize later once we identify the crash cause
            await page.screenshot(path=screenshot_path, full_page=False)
            logger.debug(f"[SCREENSHOT] Screenshot captured successfully")

        except Exception as e:
            logger.error(f"[SCREENSHOT] Failed to take screenshot: {e}")
            raise
