import os

from utils.get_iso_datetime import get_safe_datetime_for_filename
from config.storage import VIDEOS_DIR
from db.task import TaskManager
import logging

logger = logging.getLogger(__name__)


def get_tasks_video_path():
    task_manager = TaskManager()
    task = task_manager.get_current_task()

    if not task:
        logger.error("No active task found when getting video path")
        # Return a default path without task ID
        timestamp = get_safe_datetime_for_filename()
        filename = f"notask_{timestamp}.mp4"
        return os.path.join(VIDEOS_DIR, filename)

    logger.info(f"Task: {task.id}")

    timestamp = get_safe_datetime_for_filename()
    filename = f"task{task.id}_{timestamp}.mp4"
    return os.path.join(VIDEOS_DIR, filename)
