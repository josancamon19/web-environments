"""Step management using Peewee ORM."""

from typing import Optional

from db.database import Database
from db.models import StepModel


class StepManager:
    """Singleton StepManager class for managing steps."""

    _instance: Optional["StepManager"] = None
    _initialized: bool = False

    def __new__(cls):
        """Create singleton instance."""
        if cls._instance is None:
            cls._instance = super(StepManager, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        """Initialize the singleton (only once)."""
        if not self._initialized:
            self.current_step = None
            # Ensure database is initialized
            Database.get_instance()
            StepManager._initialized = True

    def get_current_step(self) -> Optional[StepModel]:
        return self.current_step

    def set_current_step(self, step: StepModel):
        self.current_step = step

    def end_current_step(self):
        self.current_step = None

    def get_steps_by_task_id(self, task_id: int) -> list[StepModel]:
        """Get all steps for a task. Use step.event_data_json to get parsed JSON."""
        query = (
            StepModel.select().where(StepModel.task == task_id).order_by(StepModel.id)
        )
        return list(query)

    @classmethod
    def get_instance(cls):
        """Get the singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls):
        """Reset the singleton instance."""
        cls._instance = None
        cls._initialized = False
