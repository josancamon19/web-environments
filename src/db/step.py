"""Step management using Peewee ORM."""
from typing import Any, Dict, Optional

from db.database import Database
from db.models import StepModel


class Step:
    """Step domain object with automatic JSON parsing for event_data."""

    def __init__(
        self,
        id: int,
        task_id: int,
        timestamp: str,
        event_type: str,
        event_data: Dict[str, Any],
        dom_snapshot: Optional[str] = None,
        dom_snapshot_metadata: Optional[str] = None,
        screenshot_path: Optional[str] = None,
    ):
        self.id = id
        self.task_id = task_id
        self.timestamp = timestamp
        self.event_type = event_type
        self.event_data = event_data  # Already parsed as dict
        self.dom_snapshot = dom_snapshot
        self.dom_snapshot_metadata = dom_snapshot_metadata
        self.screenshot_path = screenshot_path

    @classmethod
    def from_model(cls, model: StepModel) -> "Step":
        """Create Step from Peewee model with automatic JSON parsing."""
        return cls(
            id=model.id,
            task_id=model.task.id if hasattr(model.task, "id") else model.task,
            timestamp=model.timestamp,
            event_type=model.event_type,
            event_data=model.event_data_json,  # Use the property that parses JSON
            dom_snapshot=model.dom_snapshot,
            dom_snapshot_metadata=model.dom_snapshot_metadata,
            screenshot_path=model.screenshot_path,
        )


class CreateStepDto:
    """Data transfer object for creating steps."""

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
            self.step_repository = StepRepository()
            StepManager._initialized = True

    def save_step(self, step: CreateStepDto):
        """Save a new step and return its ID."""
        return self.step_repository.save(step)

    def get_current_step(self):
        """Get the current active step."""
        return self.current_step

    def get_steps_by_task_id(self, task_id: int) -> list[Step]:
        """Get all steps for a task with automatic JSON parsing."""
        return self.step_repository.get_steps_by_task_id(task_id)

    def set_actual_step(self, step: Step):
        """Set the current active step."""
        self.current_step = step

    def end_actual_step(self):
        """Clear the current active step."""
        self.current_step = None

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


class StepRepository:
    """Repository for step persistence using Peewee ORM."""

    def __init__(self):
        # Ensure database is initialized
        Database.get_instance()

    def save(self, step: CreateStepDto) -> int:
        """Create a new step and return its ID."""
        step_model = StepModel.create(
            task=step.task_id,
            timestamp=step.timestamp,
            event_type=step.event_type,
            event_data=step.event_data,
            dom_snapshot=step.dom_snapshot,
            dom_snapshot_metadata=step.dom_snapshot_metadata,
            screenshot_path=step.screenshot_path,
        )
        return step_model.id

    def get_steps_by_task_id(self, task_id: int) -> list[Step]:
        """Get all steps for a task, with event_data automatically parsed as JSON."""
        query = (
            StepModel.select().where(StepModel.task == task_id).order_by(StepModel.id)
        )

        steps = []
        for step_model in query:
            steps.append(Step.from_model(step_model))

        return steps
