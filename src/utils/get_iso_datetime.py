from datetime import datetime, timezone


def get_iso_datetime() -> str:
    """Get current datetime in ISO format with UTC timezone, safe for filenames"""
    # Replace colons with hyphens to make it Windows-compatible
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
        .replace(":", "-")
    )
