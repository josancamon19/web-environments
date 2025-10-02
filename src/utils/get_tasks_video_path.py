import os

from utils.get_iso_datetime import get_iso_datetime
from config.storage_config import VIDEOS_DIR
from tasks.task import TaskManager
import logging

logger = logging.getLogger(__name__)


def get_tasks_video_path():
    task_manager = TaskManager()
    task = task_manager.get_actual_task()

    if not task:
        logger.error("No active task found when getting video path")
        # Return a default path without task ID
        timestamp = get_iso_datetime()
        filename = f"notask_{timestamp}.mp4"
        return os.path.join(VIDEOS_DIR, filename)

    logger.info(f"Task: {task.id}")

    timestamp = get_iso_datetime()
    filename = f"task{task.id}_{timestamp}.mp4"
    return os.path.join(VIDEOS_DIR, filename)
