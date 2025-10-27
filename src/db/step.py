import json
from typing import Any, Dict

from db.database import Database


class Step:
    def __init__(
        self,
        id: int,
        task_id: int,
        timestamp: str,
        event_type: str,
        event_data: str,
        dom_snapshot: str,
        dom_snapshot_metadata: str,
        screenshot_path: str,
    ):
        self.id = id
        self.task_id = task_id
        self.timestamp = timestamp
        self.event_type = event_type
        self.event_data = event_data
        self.dom_snapshot = dom_snapshot
        self.dom_snapshot_metadata = dom_snapshot_metadata
        self.screenshot_path = screenshot_path


class CreateStepDto:
    def __init__(
        self,
        task_id: int,
        timestamp: str,
        event_type: str,
        event_data: str,
        dom_snapshot: str,
        dom_snapshot_metadata: str,
        screenshot_path: str,
    ):
        self.task_id = task_id
        self.timestamp = timestamp
        self.event_type = event_type
        self.event_data = event_data
        self.dom_snapshot = dom_snapshot
        self.dom_snapshot_metadata = dom_snapshot_metadata
        self.screenshot_path = screenshot_path


class StepManager:
    _instance = None
    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(StepManager, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        if not self._initialized:
            self.current_step = None
            self.step_repository = StepRepository()
            StepManager._initialized = True

    def save_step(self, step: CreateStepDto):
        return self.step_repository.save(step)

    def get_current_step(self):
        return self.current_step

    def get_steps_by_task_id(self, task_id: int) -> list[Step]:
        return self.step_repository.get_steps_by_task_id(task_id)

    def set_actual_step(self, step: Step):
        self.current_step = step

    def end_actual_step(self):
        self.current_step = None

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls):
        cls._instance = None
        cls._initialized = False


class StepRepository:
    def __init__(self):
        self.db = Database.get_instance()

    def save(self, step: CreateStepDto):
        step_id = self.db.insert_step(
            step.task_id,
            step.timestamp,
            step.event_type,
            step.event_data,
            step.dom_snapshot,
            step.dom_snapshot_metadata,
            step.screenshot_path,
        )
        return step_id

    def get_steps_by_task_id(self, task_id: int) -> list[Step]:
        db = Database.get_instance()
        conn = db.get_connection()
        assert conn is not None, "Database connection unavailable"

        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, event_type, event_data, timestamp FROM steps WHERE task_id = ? ORDER BY id",
            (task_id,),
        )

        steps: list[Step] = []
        for row in cursor.fetchall():
            raw_event_data = row[2]
            parsed_event: Dict[str, Any] = {}
            if raw_event_data:
                try:
                    parsed_event = json.loads(raw_event_data)
                except json.JSONDecodeError:
                    pass
            steps.append(
                Step(
                    id=row[0],
                    task_id=task_id,
                    event_type=row[1] or "",
                    event_data=parsed_event,
                    timestamp=row[3],
                    dom_snapshot=None,
                    dom_snapshot_metadata=None,
                    screenshot_path=None,
                )
            )

        return steps
