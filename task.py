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
            self.last_task_path = None
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
    
    def get_last_task_path(self) -> str:
        return self.last_task_path
    
    def set_last_task_path(self, path: str):
        self.last_task_path = path
    
    def end_actual_task(self):
        self.task_repository.update_task_ended_at(self.tasks.id)

    def save_task(self, task: CreateTaskDto) -> int:
        return self.task_repository.save(task)

    def save_task_video(self, video_path: str):
        self.task_repository.save_task_video(self.tasks.id, video_path)

class TaskRepository:
    def __init__(self):
        self.db = Database.get_instance()

    def save(self, task: CreateTaskDto) -> int:
        task_id = self.db.start_task(task.description)
        return task_id
    
    def update_task_ended_at(self, task_id: int):
        self.db.end_task(task_id)

    def save_task_video(self, task_id: int, video_path: str):
        self.db.save_task_video(task_id, video_path)

