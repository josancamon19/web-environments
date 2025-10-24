from datetime import datetime, timezone


def get_iso_datetime() -> str:
    """Get current datetime in ISO format with UTC timezone"""
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def get_safe_datetime_for_filename() -> str:
    """Get current datetime safe for use in filenames (Windows-compatible)"""
    # Replace colons with hyphens to make it Windows-compatible
    return get_iso_datetime().replace(":", "-")
