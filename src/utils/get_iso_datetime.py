from datetime import datetime, timezone

def get_iso_datetime() -> str:
    """Get current datetime in ISO format with UTC timezone"""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace('+00:00', 'Z')