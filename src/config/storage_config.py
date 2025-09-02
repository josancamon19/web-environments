import os
import sys

ROOT_DIR = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))

# Check if --api flag is present in command line arguments
if "--prod" in sys.argv:
    DATA_DIR = os.path.join("data", "prod")
else:
    DATA_DIR = os.path.join("data", "dev")

SCREENSHOTS_DIR = os.path.join(DATA_DIR, "screenshots")
VIDEOS_DIR = os.path.join(DATA_DIR, "videos")
DB_PATH = os.path.join(DATA_DIR, "tasks.db")
