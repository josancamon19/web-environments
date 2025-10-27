"""Task management using Peewee ORM."""

from typing import Optional
import logging

from db.database import Database
from db.models import TaskModel
from utils.environment_fingerprint import get_environment_fingerprint_json
from utils.get_iso_datetime import get_iso_datetime

logger = logging.getLogger(__name__)


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
            self.current_task = None
            self.last_task_path = None
            # Ensure database is initialized
            Database.get_instance()
            TaskManager._initialized = True

    @classmethod
    def get_instance(cls) -> "TaskManager":
        """Get the singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def get_current_task(self) -> Optional[TaskModel]:
        return self.current_task

    def set_current_task(self, task: TaskModel):
        self.current_task = task

    def set_current_task_video_path(self, video_path: str):
        """Set video path for the current task."""
        if self.current_task:
            TaskModel.update(video_path=video_path).where(
                TaskModel.id == self.current_task.id
            ).execute()
        else:
            logger.warning("No active task to save video for")

    def set_current_task_answer(self, answer: str):
        """Set answer for the current task."""
        if self.current_task:
            TaskModel.update(answer=answer).where(
                TaskModel.id == self.current_task.id
            ).execute()
            print(f"Answer saved for task {self.current_task.id}")
        else:
            logger.warning("No active task to save answer for")

    def get_last_task_path(self) -> str:
        return self.last_task_path

    def set_last_task_path(self, path: str):
        self.last_task_path = path

    def end_current_task(self):
        if self.current_task:
            db = Database.get_instance()
            db.end_task(self.current_task.id)
        else:
            logger.warning("No active task to end")

    def create_task(self, task: CreateTaskDto) -> int:
        """Save a new task and return its ID."""
        fingerprint = get_environment_fingerprint_json()
        task_model = TaskModel.create(
            description=task.description,
            task_type=task.task_type,
            source=task.source,
            website=task.website,
            environment_fingerprint=fingerprint,
            created_at=get_iso_datetime(),
        )
        return task_model.id
