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
        timestamp = get_iso_datetime()
        actual_task = self.task_manager.get_actual_task()

        # logger.info(f"Actual task: {actual_task}")

        # logger.info(f"Recording step: {step_info['event_info']['event_type']}")
        # logger.info(f"Event data: {step_info['event_info']['event_data']}")
        # logger.info(f"Actual task: {actual_task.id}")
        # logger.info(f"Timestamp: {timestamp}")

        context_type_action = f"{step_info['event_info']['event_context']}:{step_info['event_info']['event_type']}"
        context_type_action_formatted = context_type_action.replace(":", "_")
        
        screenshot_path = get_screenshot_path(actual_task.id, context_type_action_formatted)
        # logger.info(f"Screenshot path: {screenshot_path}")

        if not omit_screenshot:
            await self.take_screenshot(screenshot_path)

        step_id = self.db.insert_step(
                task_id=actual_task.id,
                timestamp=timestamp,
                event_type=context_type_action,
                event_data=json.dumps(step_info['event_info']['event_data'], ensure_ascii=False),
                dom_snapshot= step_info['event_info']['dom_snapshot'] if 'dom_snapshot' in step_info['event_info'] else '',
                screenshot_path=screenshot_path if not omit_screenshot else '',
            )
        
        self.step_manager.set_actual_step(Step(
            id=step_id,
            task_id=actual_task.id,
            timestamp=timestamp,
            event_type=context_type_action,
            event_data=json.dumps(step_info['event_info']['event_data'], ensure_ascii=False),
            dom_snapshot=step_info['event_info']['dom_snapshot'] if 'dom_snapshot' in step_info['event_info'] else '',
            screenshot_path=screenshot_path if not omit_screenshot else '',
        ))        

    async def take_screenshot(self, screenshot_path: str):
        await self.actual_page.get_page().screenshot(path=screenshot_path, full_page=True)