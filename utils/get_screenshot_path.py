import os
from utils.get_iso_datetime import get_iso_datetime
from storage_config import SCREENSHOTS_DIR


def get_screenshot_path(task_id: int, prefix: str):
    timestamp = get_iso_datetime()
    filename = f"task{task_id}/{prefix}_{timestamp}.png"
    return os.path.join(SCREENSHOTS_DIR, filename)