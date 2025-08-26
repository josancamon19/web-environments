import os
import sys

# Default mode is dev
DEFAULT_MODE = "dev"

def get_mode():
    """Get the current mode from command line arguments or environment variable."""
    # Check command line arguments
    for arg in sys.argv:
        if arg.startswith("--mode="):
            return arg.split("=")[1]
        elif arg == "--prod":
            return "prod"
        elif arg == "--dev":
            return "dev"
    
    # Check environment variable
    mode = os.getenv("APP_MODE", DEFAULT_MODE)
    return mode

def get_data_dir():
    """Get the data directory based on the current mode."""
    mode = get_mode()
    if mode == "prod":
        return os.path.join("data", "prod")
    else:  # dev mode (default)
        return os.path.join("data", "dev")

ROOT_DIR = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
DATA_DIR = get_data_dir()
SCREENSHOTS_DIR = os.path.join(DATA_DIR, "screenshots")
VIDEOS_DIR = os.path.join(DATA_DIR, "videos")
DB_PATH = os.path.join(DATA_DIR, "tasks.db")

