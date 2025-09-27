import os
import sys
from pathlib import Path

APP_NAME = "TaskCollector"

# Handle frozen (compiled) executables differently
if getattr(sys, "frozen", False):
    # For compiled apps, use the directory containing the executable
    PROJECT_ROOT = Path(sys.executable).resolve().parent
    ROOT_DIR = PROJECT_ROOT  # In frozen apps, these are the same
else:
    # For development, use the source tree structure
    ROOT_DIR = Path(__file__).resolve().parents[1]
    PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _platform_support_dir() -> Path:
    """Return a user-writable directory for bundled builds."""
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / APP_NAME
    if sys.platform.startswith("win"):
        return Path(os.environ.get("APPDATA", home / "AppData" / "Roaming")) / APP_NAME
    return Path(os.environ.get("XDG_DATA_HOME", home / ".local" / "share")) / APP_NAME


def _ensure_writable(path: Path) -> bool:
    """Check whether the given path is writable; create it if possible."""
    try:
        path.mkdir(parents=True, exist_ok=True)
        test_file = path / ".write_test"
        test_file.touch(exist_ok=True)
        test_file.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def _resolve_storage_root() -> Path:
    override = os.environ.get("TASK_COLLECTOR_DATA_ROOT")
    if override:
        return Path(override).expanduser()

    # For frozen executables, always create data next to the executable
    if getattr(sys, "frozen", False):
        data_dir = PROJECT_ROOT / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        return data_dir

    # For development, try project root first
    candidate = PROJECT_ROOT / "data"
    if _ensure_writable(candidate):
        return candidate

    fallback = _platform_support_dir()
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


MODE = "prod" if "--prod" in sys.argv else "dev"
DATA_DIR = _resolve_storage_root() / MODE
SCREENSHOTS_DIR = DATA_DIR / "screenshots"
VIDEOS_DIR = DATA_DIR / "videos"
DB_PATH = DATA_DIR / "tasks.db"
