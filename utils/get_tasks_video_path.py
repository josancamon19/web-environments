import os

from utils.get_iso_datetime import get_iso_datetime
from storage_config import VIDEOS_DIR
from task import TaskManager
import logging

logger = logging.getLogger(__name__)

def get_tasks_video_path():
    task_manager = TaskManager()
    task = task_manager.get_actual_task()
    logger.info(f"Task: {task.id}")

    timestamp = get_iso_datetime()
    filename = f"task{task.id}_{timestamp}.mp4"
    return os.path.join(VIDEOS_DIR, filename)