import os
import sqlite3
from datetime import datetime
from typing import Optional
from utils.get_iso_datetime import get_iso_datetime
from source_data.schema import SCHEMA_SQL
from config.storage_config import DB_PATH
import logging

logger = logging.getLogger(__name__)


class Database:
    """
    Singleton Database class for managing SQLite connections and operations
    """

    _instance: Optional["Database"] = None
    _initialized: bool = False

    def __new__(cls, db_path: str = None) -> "Database":
        """Create singleton instance"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, db_path: str = None):
        """Initialize the singleton (only once)"""
        if not self._initialized:
            # Use provided path or default
            self.db_path = db_path or DB_PATH
            os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
            # Allow access from multiple threads; guarded by higher-level locks in recorder
            self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self.conn.execute("PRAGMA foreign_keys = ON;")
            self._ensure_schema()
            Database._initialized = True

    @classmethod
    def get_instance(cls, db_path: str = None) -> "Database":
        """Get the singleton instance"""
        if cls._instance is None:
            cls._instance = cls(db_path or DB_PATH)
        return cls._instance

    def _ensure_schema(self):
        cur = self.conn.cursor()
        cur.executescript(SCHEMA_SQL)
        self.conn.commit()

    def close(self):
        """Close database connection"""
        try:
            if hasattr(self, "conn"):
                self.conn.close()
        except Exception:
            pass

    def get_connection(self):
        """Get the database connection"""
        return self.conn if hasattr(self, "conn") else None

    def is_initialized(self) -> bool:
        """Check if database is initialized"""
        return self._initialized

    def get_db_path(self) -> str:
        """Get database file path"""
        return self.db_path if hasattr(self, "db_path") else ""

    def start_task(
        self,
        description: str,
        task_type: str = "action",
        source: str = "none",
        environment_fingerprint: Optional[str] = None,
    ) -> int:
        created_at = get_iso_datetime()
        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT INTO tasks(
                description,
                task_type,
                source,
                created_at,
                environment_fingerprint
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (description, task_type, source, created_at, environment_fingerprint),
        )
        self.conn.commit()
        print(f"Task started: {cur.lastrowid} (Type: {task_type}, Source: {source})")
        return cur.lastrowid

    def end_task(self, task_id: int):
        ended_at = get_iso_datetime()
        duration_seconds = None

        try:
            cur = self.conn.cursor()
            cur.execute("SELECT created_at FROM tasks WHERE id = ?", (task_id,))
            row = cur.fetchone()
            if row and row[0]:
                start_raw = row[0]
                end_dt = datetime.fromisoformat(ended_at.replace("Z", "+00:00"))
                start_dt = datetime.fromisoformat(start_raw.replace("Z", "+00:00"))
                duration_seconds = round((end_dt - start_dt).total_seconds(), 3)
        except Exception as exc:
            logger.warning("Failed to compute duration for task %s: %s", task_id, exc)

        self.conn.execute(
            "UPDATE tasks SET ended_at = ?, duration_seconds = ? WHERE id = ?",
            (ended_at, duration_seconds, task_id),
        )
        self.conn.commit()

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
        cur = self.conn.cursor()
        cur.execute(
            "INSERT INTO steps(task_id, timestamp, event_type, event_data, dom_snapshot, dom_snapshot_metadata, screenshot_path) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                task_id,
                timestamp,
                event_type,
                event_data,
                dom_snapshot,
                dom_snapshot_metadata,
                screenshot_path,
            ),
        )
        self.conn.commit()
        return cur.lastrowid

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
        cur = self.conn.cursor()
        cur.execute(
            "INSERT INTO requests(task_id, step_id, request_uid, url, method, headers, post_data, cookies, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                task_id,
                step_id,
                request_uid,
                url,
                method,
                headers,
                post_data,
                cookies,
                timestamp,
            ),
        )
        self.conn.commit()
        return cur.lastrowid

    def insert_response(
        self,
        task_id: int,
        request_id: int,
        status: int,
        headers: str,
        body: bytes,
        timestamp: str,
    ) -> int:
        cur = self.conn.cursor()
        cur.execute(
            "INSERT INTO responses(task_id, request_id, status, headers, body, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
            (task_id, request_id, status, headers, body, timestamp),
        )
        self.conn.commit()
        return cur.lastrowid

    def save_task_video(self, task_id: int, video_path: str):
        cur = self.conn.cursor()
        cur.execute(
            "UPDATE tasks SET video_path = ? WHERE id = ?", (video_path, task_id)
        )
        self.conn.commit()

    def save_task_answer(self, task_id: int, answer: str):
        cur = self.conn.cursor()
        cur.execute("UPDATE tasks SET answer = ? WHERE id = ?", (answer, task_id))
        self.conn.commit()
        print(f"Answer saved for task {task_id}")
