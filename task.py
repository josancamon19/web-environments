from typing import Optional
from utils.get_iso_datetime import get_iso_datetime
from source_data.database import Database

class Task:
    def __init__(self, id: int, description: str):
        self.id = id
        self.description = description

class CreateTaskDto:
    def __init__(self, description: str):
        self.description = description

class TaskManager:
    """
    Singleton TaskManager class for managing tasks
    """
    _instance: Optional['TaskManager'] = None
    _initialized: bool = False
    
    def __new__(cls) -> 'TaskManager':
        """Create singleton instance"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        """Initialize the singleton (only once)"""
        if not self._initialized:
            self.tasks = None
            self.task_repository = TaskRepository()
            TaskManager._initialized = True
    
    @classmethod
    def get_instance(cls) -> 'TaskManager':
        """Get the singleton instance"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def get_actual_task(self) -> Task:
        return self.tasks

    def set_actual_task(self, task: Task):
        self.tasks = task

    def end_actual_task(self):
        self.tasks = None

    def save_task(self, task: CreateTaskDto) -> int:
        return self.task_repository.save(task)

class TaskRepository:
    def __init__(self):
        self.db = Database.get_instance()

    def save(self, task: CreateTaskDto) -> int:
        task_id = self.db.start_task(task.description)
        return task_id

