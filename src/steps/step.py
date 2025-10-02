from source_data.database import Database


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
            self.actual_step = None
            self.step_repository = StepRepository()
            StepManager._initialized = True

    def save_step(self, step: CreateStepDto):
        return self.step_repository.save(step)
        
    def get_actual_step(self):
        return self.actual_step
    
    def set_actual_step(self, step: Step):
        self.actual_step = step

    def end_actual_step(self):
        self.actual_step = None

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
