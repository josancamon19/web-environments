import logging
from db.task import TaskManager
from browser.handlers.request_event import RequestEvent
import json
from utils.get_iso_datetime import get_iso_datetime
from db.database import Database

logger = logging.getLogger(__name__)


class ResponseEvent:
    _instance = None
    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ResponseEvent, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        if not self._initialized:
            self.task_manager = TaskManager()
            self.request_event = RequestEvent()
            self.db = Database.get_instance()
            ResponseEvent._initialized = True

    async def listen(self, response):
        try:
            req = response.request
            if req.resource_type not in ("xhr", "fetch", "document"):
                return
        except Exception:
            return

        request_id = self.request_event.request_map.get(req)
        if not request_id:
            # logger.warning(f"[RESPONSE] No matching request found for response {response.url}")
            return  # No matching request found

        # logger.info(f"[RESPONSE] Recording response for request {request_id}")

        headers = {}
        try:
            headers = response.headers
        except Exception:
            headers = {}

        body_bytes = None
        try:
            # Beware: large bodies. This is MVP; store as-is.
            body_bytes = await response.body()
        except Exception:
            body_bytes = None

        status = None
        try:
            status = response.status
        except Exception:
            status = None

        # Don't create a step - just insert into responses table
        # Get the current task
        current_task = self.task_manager.get_actual_task()
        if not current_task:
            logger.warning(
                "[RESPONSE] No active task found, skipping response recording"
            )
            return

        response_id = self.db.insert_response(
            task_id=current_task.id,
            request_id=request_id,
            status=status,
            headers=json.dumps(headers, ensure_ascii=False),
            body=body_bytes,
            timestamp=get_iso_datetime(),
        )
        logger.info(f"[RESPONSE] Saved response {response_id} to database")
