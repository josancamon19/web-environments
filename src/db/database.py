"""Database management using Peewee ORM."""
import os
import logging
from datetime import datetime
from typing import Optional

from config.storage import DB_PATH
from db.models import (
    ALL_MODELS,
    db,
    TaskModel,
    StepModel,
    RequestModel,
    ResponseModel,
)
from utils.get_iso_datetime import get_iso_datetime

logger = logging.getLogger(__name__)


class Database:
    """Singleton Database class for managing Peewee ORM."""

    _instance: Optional["Database"] = None
    _initialized: bool = False

    def __new__(cls, db_path: str = None) -> "Database":
        """Create singleton instance."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, db_path: str = None):
        """Initialize the singleton (only once)."""
        if not self._initialized:
            # Use provided path or default
            self.db_path = db_path or DB_PATH
            os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

            # Initialize Peewee database
            db.init(self.db_path, pragmas={"foreign_keys": 1, "journal_mode": "wal"})

            # Create tables if they don't exist
            self._ensure_schema()
            Database._initialized = True

    @classmethod
    def get_instance(cls, db_path: str = None) -> "Database":
        """Get the singleton instance."""
        if cls._instance is None:
            cls._instance = cls(db_path or DB_PATH)
        return cls._instance

    def _ensure_schema(self):
        """Create tables if they don't exist."""
        db.connect(reuse_if_open=True)
        db.create_tables(ALL_MODELS, safe=True)

    @staticmethod
    def _parse_iso_datetime(timestamp_str: str) -> datetime:
        """Parse ISO datetime string, handling both old (with hyphens) and new (proper ISO) formats."""
        # Normalize the timestamp string
        normalized = timestamp_str.replace("Z", "+00:00")

        # Try parsing as-is first (proper ISO format)
        try:
            return datetime.fromisoformat(normalized)
        except ValueError:
            pass

        # If that fails, it might be the old format with hyphens in the time part
        # Format: 2025-10-24T06-28-30.794+00:00 should become 2025-10-24T06:28:30.794+00:00
        # Only replace hyphens in the time part (after the 'T')
        if "T" in normalized:
            date_part, time_part = normalized.split("T", 1)
            # Replace first two hyphens in time part with colons (HH-MM-SS -> HH:MM:SS)
            time_part = time_part.replace("-", ":", 2)
            normalized = f"{date_part}T{time_part}"
            return datetime.fromisoformat(normalized)

        raise ValueError(f"Cannot parse timestamp: {timestamp_str}")

    def close(self):
        """Close database connection."""
        try:
            if not db.is_closed():
                db.close()
        except Exception:
            pass

    def get_connection(self):
        """Get the database connection (for compatibility)."""
        return db if not db.is_closed() else None

    def is_initialized(self) -> bool:
        """Check if database is initialized."""
        return self._initialized

    def get_db_path(self) -> str:
        """Get database file path."""
        return self.db_path if hasattr(self, "db_path") else ""

    def start_task(
        self,
        description: str,
        task_type: str = "action",
        source: str = "none",
        website: Optional[str] = None,
        environment_fingerprint: Optional[str] = None,
    ) -> int:
        """Create a new task and return its ID."""
        created_at = get_iso_datetime()
        task = TaskModel.create(
            description=description,
            task_type=task_type,
            source=source,
            website=website,
            created_at=created_at,
            environment_fingerprint=environment_fingerprint,
        )
        website_info = f", Website: {website}" if website else ""
        print(
            f"Task started: {task.id} (Type: {task_type}, Source: {source}{website_info})"
        )
        return task.id

    def end_task(self, task_id: int):
        """Mark a task as ended and calculate duration."""
        ended_at = get_iso_datetime()
        duration_seconds = None

        try:
            task = TaskModel.get_by_id(task_id)
            if task.created_at:
                end_dt = self._parse_iso_datetime(ended_at)
                start_dt = self._parse_iso_datetime(task.created_at)
                duration_seconds = round((end_dt - start_dt).total_seconds(), 3)
        except Exception as exc:
            logger.warning("Failed to compute duration for task %s: %s", task_id, exc)

        TaskModel.update(ended_at=ended_at, duration_seconds=duration_seconds).where(
            TaskModel.id == task_id
        ).execute()

    def insert_step(
        self,
        task_id: int,
        timestamp: str,
        event_type: str,
        event_data: str,
        dom_snapshot: str,
        dom_snapshot_metadata: str,
        screenshot_path: str,
    ) -> int:
        """Insert a new step and return its ID."""
        step = StepModel.create(
            task=task_id,
            timestamp=timestamp,
            event_type=event_type,
            event_data=event_data,
            dom_snapshot=dom_snapshot,
            dom_snapshot_metadata=dom_snapshot_metadata,
            screenshot_path=screenshot_path,
        )
        return step.id

    def insert_request(
        self,
        task_id: int,
        step_id: int,
        request_uid: str,
        url: str,
        method: str,
        headers: str,
        post_data: str,
        cookies: str,
        timestamp: str,
    ) -> int:
        """Insert a new request and return its ID."""
        request = RequestModel.create(
            task=task_id,
            step=step_id,
            request_uid=request_uid,
            url=url,
            method=method,
            headers=headers,
            post_data=post_data,
            cookies=cookies,
            timestamp=timestamp,
        )
        return request.id

    def insert_response(
        self,
        task_id: int,
        request_id: int,
        status: int,
        headers: str,
        body: bytes,
        timestamp: str,
    ) -> int:
        """Insert a new response and return its ID."""
        response = ResponseModel.create(
            task=task_id,
            request=request_id,
            status=status,
            headers=headers,
            body=body,
            timestamp=timestamp,
        )
        return response.id

    def save_task_video(self, task_id: int, video_path: str):
        """Update task with video path."""
        TaskModel.update(video_path=video_path).where(TaskModel.id == task_id).execute()

    def save_task_answer(self, task_id: int, answer: str):
        """Update task with answer."""
        TaskModel.update(answer=answer).where(TaskModel.id == task_id).execute()
        print(f"Answer saved for task {task_id}")
