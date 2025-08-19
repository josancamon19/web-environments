import os
import sqlite3


SCHEMA_SQL = r"""
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    description TEXT NOT NULL,
    created_at TEXT NOT NULL,
    ended_at TEXT
);

CREATE TABLE IF NOT EXISTS steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    timestamp TEXT NOT NULL,
    event_type TEXT NOT NULL,
    event_data TEXT,
    dom_snapshot TEXT,
    screenshot_path TEXT
);

CREATE TABLE IF NOT EXISTS requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    step_id INTEGER REFERENCES steps(id) ON DELETE SET NULL,
    request_uid TEXT,
    url TEXT,
    method TEXT,
    headers TEXT,
    post_data TEXT,
    cookies TEXT,
    timestamp TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS responses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    request_id INTEGER REFERENCES requests(id) ON DELETE SET NULL,
    status INTEGER,
    headers TEXT,
    body BLOB,
    timestamp TEXT NOT NULL
);
"""


class Database:
    def __init__(self, db_path: str):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        # Allow access from multiple threads; guarded by higher-level locks in recorder
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.execute("PRAGMA foreign_keys = ON;")
        self._ensure_schema()

    def _ensure_schema(self):
        cur = self.conn.cursor()
        cur.executescript(SCHEMA_SQL)
        self.conn.commit()

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass

    def start_task(self, description: str) -> int:
        from datetime import datetime

        created_at = datetime.utcnow().isoformat(timespec="milliseconds") + "Z"
        cur = self.conn.cursor()
        cur.execute(
            "INSERT INTO tasks(description, created_at) VALUES (?, ?)",
            (description, created_at),
        )
        self.conn.commit()
        return cur.lastrowid

    def end_task(self, task_id: int):
        from datetime import datetime

        ended_at = datetime.utcnow().isoformat(timespec="milliseconds") + "Z"
        self.conn.execute(
            "UPDATE tasks SET ended_at = ? WHERE id = ?", (ended_at, task_id)
        )
        self.conn.commit()

    def insert_step(
        self,
        task_id: int,
        timestamp: str,
        event_type: str,
        event_data: str,
        dom_snapshot: str,
        screenshot_path: str,
    ) -> int:
        cur = self.conn.cursor()
        cur.execute(
            "INSERT INTO steps(task_id, timestamp, event_type, event_data, dom_snapshot, screenshot_path) VALUES (?, ?, ?, ?, ?, ?)",
            (task_id, timestamp, event_type, event_data, dom_snapshot, screenshot_path),
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
