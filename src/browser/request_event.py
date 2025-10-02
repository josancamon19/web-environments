import logging
from tasks.task import TaskManager
from steps.step import StepManager
import json
from utils.get_iso_datetime import get_iso_datetime
from source_data.database import Database

logger = logging.getLogger(__name__)


class Request_Event:
    _instance = None
    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(Request_Event, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        if not self._initialized:
            self.stepManager = StepManager()
            self.taskManager = TaskManager()
            self.request_step_counter = 0
            self.request_map = {}
            self.db = Database.get_instance()
            Request_Event._initialized = True

    def listen_for_request(self, request):
        try:
            # Also record top-level navigation (document) requests
            if request.resource_type not in ("xhr", "fetch", "document"):
                return
        except Exception:
            return
        # logger.info(f"StepManager: {self.stepManager.get_actual_step()}")
        # logger.info(
        #     f"[REQUEST] Recording {request.method} {request.url[:50]}... triggered by step {self.stepManager.get_actual_step().id}"
        # )

        self.request_step_counter += 1
        request_uid = f"req_{self.request_step_counter}"

        headers = {}
        try:
            headers = request.headers
        except Exception:
            headers = {}

        post_data = None
        try:
            post_data = request.post_data
        except Exception:
            post_data = None

        url = request.url

        # Capture cookies at time of request
        cookies_json = []
        try:
            cookies_json = self.context.cookies()
        except Exception:
            cookies_json = []

        # Don't create a step - just insert into requests table
        # Get the current step if it exists, otherwise use None
        current_step = self.stepManager.get_actual_step()
        step_id = current_step.id if current_step else None

        # Get the current task
        current_task = self.taskManager.get_actual_task()
        if not current_task:
            logger.warning("[REQUEST] No active task found, skipping request recording")
            return

        request_id = self.db.insert_request(
            task_id=current_task.id,
            step_id=step_id,  # Link to the action that triggered this (if any)
            request_uid=request_uid,
            url=url,
            method=request.method,
            headers=json.dumps(headers, ensure_ascii=False),
            post_data=post_data,
            cookies=json.dumps(cookies_json, ensure_ascii=False),
            timestamp=get_iso_datetime(),
        )
        self.request_map[request] = request_id
        # logger.info(f"[REQUEST] Saved request {request_id} to database")
