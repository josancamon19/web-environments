"""Task management using Peewee ORM."""
from typing import Optional
import logging

from db.database import Database
from db.models import TaskModel
from utils.environment_fingerprint import get_environment_fingerprint_json

logger = logging.getLogger(__name__)


class Task:
    """Task domain object."""

    def __init__(
        self,
        id: int,
        description: str,
        task_type: str = "action",
        source: str = "none",
        website: Optional[str] = None,
    ):
        self.id = id
        self.description = description
        self.task_type = task_type
        self.source = source
        self.website = website

    @classmethod
    def from_model(cls, model: TaskModel) -> "Task":
        """Create Task from Peewee model."""
        return cls(
            id=model.id,
            description=model.description,
            task_type=model.task_type,
            source=model.source,
            website=model.website,
        )


class CreateTaskDto:
    """Data transfer object for creating tasks."""

    def __init__(
        self,
        description: str,
        task_type: str = "action",
        source: str = "none",
        website: Optional[str] = None,
    ):
        self.description = description
        self.task_type = task_type
        self.source = source
        self.website = website


class TaskManager:
    """Singleton TaskManager class for managing tasks."""

    _instance: Optional["TaskManager"] = None
    _initialized: bool = False

    def __new__(cls) -> "TaskManager":
        """Create singleton instance."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        """Initialize the singleton (only once)."""
        if not self._initialized:
            self.tasks = None
            self.last_task_path = None
            self.task_repository = TaskRepository()
            TaskManager._initialized = True

    @classmethod
    def get_instance(cls) -> "TaskManager":
        """Get the singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def get_actual_task(self) -> Task:
        """Get the current active task."""
        return self.tasks

    def set_actual_task(self, task: Task):
        """Set the current active task."""
        self.tasks = task

    def get_last_task_path(self) -> str:
        """Get the path of the last task."""
        return self.last_task_path

    def set_last_task_path(self, path: str):
        """Set the path of the last task."""
        self.last_task_path = path

    def end_actual_task(self):
        """End the current active task."""
        if self.tasks:
            self.task_repository.update_task_ended_at(self.tasks.id)
        else:
            logger.warning("No active task to end")

    def save_task(self, task: CreateTaskDto) -> int:
        """Save a new task and return its ID."""
        return self.task_repository.save(task)

    def save_task_video(self, video_path: str):
        """Save video path for the current task."""
        if self.tasks:
            self.task_repository.save_task_video(self.tasks.id, video_path)
        else:
            logger.warning("No active task to save video for")

    def save_task_answer(self, answer: str):
        """Save answer for the current task."""
        if self.tasks:
            self.task_repository.save_task_answer(self.tasks.id, answer)
        else:
            logger.warning("No active task to save answer for")


class TaskRepository:
    """Repository for task persistence using Peewee ORM."""

    def __init__(self):
        # Ensure database is initialized
        Database.get_instance()

    def save(self, task: CreateTaskDto) -> int:
        """Create a new task and return its ID."""
        fingerprint = get_environment_fingerprint_json()
        task_model = TaskModel.create(
            description=task.description,
            task_type=task.task_type,
            source=task.source,
            website=task.website,
            environment_fingerprint=fingerprint,
            created_at=self._get_iso_datetime(),
        )
        return task_model.id

    def update_task_ended_at(self, task_id: int):
        """Update task end time and calculate duration."""
        from db.database import Database

        db = Database.get_instance()
        db.end_task(task_id)

    def save_task_video(self, task_id: int, video_path: str):
        """Update task with video path."""
        TaskModel.update(video_path=video_path).where(TaskModel.id == task_id).execute()

    def save_task_answer(self, task_id: int, answer: str):
        """Update task with answer."""
        TaskModel.update(answer=answer).where(TaskModel.id == task_id).execute()
        print(f"Answer saved for task {task_id}")

    @staticmethod
    def _get_iso_datetime() -> str:
        """Get current ISO datetime."""
        from utils.get_iso_datetime import get_iso_datetime

        return get_iso_datetime()
