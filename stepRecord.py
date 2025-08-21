import logging
from source_data.database import Database
from utils.get_iso_datetime import get_iso_datetime
from task import TaskManager
import json
from actual_page import ActualPage
from step import StepManager
from utils.get_screenshot_path import get_screenshot_path
from step import Step

logger = logging.getLogger(__name__)

class StepRecord:
    def __init__(self):
        self.db = Database.get_instance()
        self.task_manager = TaskManager()
        self.actual_page = ActualPage()
        self.step_manager = StepManager()

    async def record_step(self, step_info: dict):
        timestamp = get_iso_datetime()
        actual_task = self.task_manager.get_actual_task()

        logger.info(f"Actual task: {actual_task}")

        logger.info(f"Recording step: {step_info['event_info']['event_type']}")
        logger.info(f"Event data: {step_info['event_info']['event_data']}")
        logger.info(f"Actual task: {actual_task.id}")
        logger.info(f"Timestamp: {timestamp}")

        screenshot_path = get_screenshot_path(actual_task.id, step_info['prefix_action'])
        logger.info(f"Screenshot path: {screenshot_path}")
        await self.take_screenshot(screenshot_path)

        step_id = self.db.insert_step(
                task_id=actual_task.id,
                timestamp=timestamp,
                event_type=step_info['event_info']['event_type'],
                event_data=json.dumps(step_info['event_info']['event_data'], ensure_ascii=False),
                dom_snapshot=step_info['event_info']['dom_snapshot'] or '',
                screenshot_path=screenshot_path,
            )
        
        self.step_manager.set_actual_step(Step(
            id=step_id,
            task_id=actual_task.id,
            timestamp=timestamp,
            event_type=step_info['event_info']['event_type'],
            event_data=json.dumps(step_info['event_info']['event_data'], ensure_ascii=False),
            dom_snapshot='',
            screenshot_path=screenshot_path,
        ))        

    async def take_screenshot(self, screenshot_path: str):
        await self.actual_page.get_page().screenshot(path=screenshot_path, full_page=True)