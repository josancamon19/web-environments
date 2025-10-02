import os
import time
from config.storage_config import SCREENSHOTS_DIR


def get_screenshot_path(task_id: int, event_name: str):
    # Use Unix timestamp for better ordering (milliseconds precision)
    timestamp = int(time.time() * 1000)

    # Create task-specific directory if it doesn't exist
    task_dir = os.path.join(SCREENSHOTS_DIR, f"task{task_id}")
    os.makedirs(task_dir, exist_ok=True)

    # Format: timestamp_eventname.png (e.g., 1699123456789_click.png)
    filename = f"{timestamp}_{event_name}.png"
    return os.path.join(task_dir, filename)
