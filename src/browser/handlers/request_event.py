import logging
from db.step import StepManager
import json
from utils.get_iso_datetime import get_iso_datetime
from db.task import TaskManager
from db.database import Database

logger = logging.getLogger(__name__)


class RequestEvent:
    _instance = None
    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(RequestEvent, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        if not self._initialized:
            self.step_manager = StepManager()
            self.task_manager = TaskManager()
            self.request_step_counter = 0
            self.request_map = {}
            self.db = Database.get_instance()
            RequestEvent._initialized = True

    def _safe_get_post_data(self, request) -> str | None:
        """Safely extract POST data, handling both text and binary payloads."""
        try:
            # Try post_data_buffer first (returns bytes)
            buffer_accessor = getattr(request, "post_data_buffer", None)
            if buffer_accessor and callable(buffer_accessor):
                data_bytes = buffer_accessor()
                if data_bytes:
                    try:
                        # Try UTF-8 decode for text data
                        return data_bytes.decode("utf-8")
                    except UnicodeDecodeError:
                        # For binary data, base64 encode it
                        import base64

                        return base64.b64encode(data_bytes).decode("ascii")
                return None

            # Fallback: try accessing post_data property (may fail on binary)
            try:
                return request.post_data
            except (UnicodeDecodeError, AttributeError):
                return None
        except Exception:
            return None

    def listen(self, request):
        if not request or request.resource_type not in ("xhr", "fetch", "document"):
            return

        self.request_step_counter += 1
        request_uid = f"req_{self.request_step_counter}"

        # Safely extract headers
        try:
            headers = request.headers
        except Exception:
            headers = {}

        # Safely extract POST data (handle both text and binary)
        post_data = self._safe_get_post_data(request)
        url = request.url

        step = self.step_manager.get_current_step()
        step_id = step.id if step else None

        # Get the current task
        current_task = self.task_manager.get_current_task()
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
            cookies="[]",
            timestamp=get_iso_datetime(),
        )
        self.request_map[request] = request_id
        logger.info(f"[REQUEST] Saved request {request_id} to database")
