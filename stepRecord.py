import logging
from source_data.database import Database
from utils.get_iso_datetime import get_iso_datetime
from task import TaskManager
import json

logger = logging.getLogger(__name__)

class StepRecord:
    def __init__(self):
        self.db = Database.get_instance()
        self.task_manager = TaskManager()

    def record_step(self, step_info: dict):
        timestamp = get_iso_datetime()
        actual_task = self.task_manager.get_actual_task()

        logger.info(f"Actual task: {actual_task}")

        logger.info(f"Recording step: {step_info['event_info']['event_type']}")
        logger.info(f"Event data: {step_info['event_info']['event_data']}")
        logger.info(f"Actual task: {actual_task.id}")
        logger.info(f"Timestamp: {timestamp}")

        step_id = self.db.insert_step(
                task_id=actual_task.id,
                timestamp=timestamp,
                event_type=step_info['event_info']['event_type'],
                event_data=json.dumps(step_info['event_info']['event_data'], ensure_ascii=False),
                dom_snapshot=step_info['event_info']['dom_snapshot'],
                screenshot_path='5555',
            )
        logger.info(f"Saved step {step_id} to database")