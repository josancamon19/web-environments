import json
import os
import platform
import sys
from typing import Any, Dict

from src.config.browser_config import BROWSER_ARGS, CONTEXT_CONFIG


def _safe_playwright_info() -> Dict[str, Any]:
    try:
        import playwright  # type: ignore

        return {
            "playwright_version": getattr(playwright, "__version__", "unknown"),
        }
    except Exception:
        return {"playwright_version": "unavailable"}


def get_environment_fingerprint() -> Dict[str, Any]:
    fingerprint: Dict[str, Any] = {
        "python": {
            "version": platform.python_version(),
            "implementation": platform.python_implementation(),
            "executable": os.path.realpath(sys.executable or ""),
        },
        "os": {
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
        },
        "browser": {
            "requested_channel": os.environ.get("RECORDER_BROWSER_CHANNEL", "chrome"),
            "user_data_dir": os.environ.get("RECORDER_USER_DATA_DIR"),
            "args": BROWSER_ARGS,
        },
        "context_config": CONTEXT_CONFIG,
    }

    fingerprint.update(_safe_playwright_info())

    return fingerprint


def get_environment_fingerprint_json() -> str:
    return json.dumps(get_environment_fingerprint(), ensure_ascii=False)
