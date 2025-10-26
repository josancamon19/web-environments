import atexit
import asyncio
import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional, Set
from urllib.parse import urlsplit

from playwright.async_api import BrowserContext, Request, Response

from config.browser_config import CONTEXT_CONFIG
from config.storage import DATA_DIR
from db.task import TaskManager, Task
from utils.get_iso_datetime import get_iso_datetime

logger = logging.getLogger(__name__)


class OfflineCaptureManager:
    """Collect all artifacts required to replay a browsing session offline."""

    def __init__(self):
        self._lock = asyncio.Lock()
        self._active = False
        self._context: Optional[BrowserContext] = None
        self._task: Optional[Task] = None

        self._session_path: Optional[Path] = None
        self._resources_path: Optional[Path] = None
        self._storage_path: Optional[Path] = None
        self._manifest_path: Optional[Path] = None
        self._requests_log_path: Optional[Path] = None
        self._failures_log_path: Optional[Path] = None

        self._resource_counter = 0
        self._body_map: Dict[str, str] = {}
        self._resources: list[Dict[str, Any]] = []
        self._request_failures: list[Dict[str, Any]] = []
        self._origins: Set[str] = set()

        self._started_at: Optional[str] = None
        self._environment: Dict[str, Any] = {}
        self._atexit_registered = False

    async def start(self, context: BrowserContext) -> None:
        """Initialize capture directories and register listeners."""

        task_manager = TaskManager.get_instance()
        task = task_manager.get_actual_task()
        if self._active:
            logger.debug("[CAPTURE] Session already active")
            return

        self._context = context
        self._task = task
        self._started_at = get_iso_datetime()

        base_path = Path(DATA_DIR) / "captures"
        timestamp_slug = (
            self._started_at.replace(":", "-") if self._started_at else "session"
        )
        self._session_path = base_path / f"task_{task.id}" / timestamp_slug
        self._resources_path = self._session_path / "resources"
        self._storage_path = self._session_path / "storage"
        self._manifest_path = self._session_path / "manifest.json"
        self._requests_log_path = self._session_path / "requests.jsonl"
        self._failures_log_path = self._session_path / "request_failures.jsonl"

        for path in (self._session_path, self._resources_path, self._storage_path):
            path.mkdir(parents=True, exist_ok=True)

        self._environment = {
            "context_config": CONTEXT_CONFIG,
            "started_at": self._started_at,
        }

        # Register listeners
        context.on(
            "response",
            lambda response: asyncio.create_task(self._handle_response(response)),
        )
        context.on(
            "requestfailed",
            lambda request: asyncio.create_task(self._handle_request_failed(request)),
        )

        if not self._atexit_registered:
            atexit.register(self._finalize_sync)
            self._atexit_registered = True

        self._active = True
        logger.info(
            "[CAPTURE] Offline capture session started at %s", self._session_path
        )

    async def stop(self) -> None:
        """Finalize capture - must be called BEFORE context closes."""
        if not self._active:
            return

        if not self._storage_path or not self._context:
            raise ValueError("Storage path or context not set")

        state = await self._context.storage_state()
        (self._storage_path / "storage_state.json").write_text(
            json.dumps(state, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        self._finalize_manifest_sync()
        self._active = False
        logger.info("[CAPTURE] Offline capture session finalized")

    async def _handle_request_failed(self, request: Request) -> None:
        if not self._active:
            return

        failure = request.failure
        timestamp = get_iso_datetime()
        entry = {
            "url": request.url,
            "method": request.method,
            "resource_type": request.resource_type,
            "timestamp": timestamp,
            "error_text": failure if failure else None,
        }
        self._request_failures.append(entry)
        if self._failures_log_path:
            await asyncio.to_thread(self._append_jsonl, self._failures_log_path, entry)

    async def _handle_response(self, response: Response) -> None:
        if not self._active:
            return

        request = response.request
        url = request.url
        self._origins.add(self._origin_from_url(url))

        try:
            headers = await request.all_headers()
        except Exception:
            headers = dict(getattr(request, "headers", {}))

        # Safely capture POST data; prefer binary buffer and base64-encode if needed
        post_data = None
        try:
            post_data_buffer_accessor = getattr(request, "post_data_buffer", None)
            if callable(post_data_buffer_accessor):
                try:
                    data_bytes = await post_data_buffer_accessor()
                except TypeError:
                    data_bytes = post_data_buffer_accessor()
                if data_bytes:
                    try:
                        # Try utf-8 first
                        post_data = data_bytes.decode("utf-8")
                    except UnicodeDecodeError:
                        import base64

                        post_data = base64.b64encode(data_bytes).decode("ascii")
                else:
                    post_data = None
            else:
                # Fallback to post_data (string) if buffer not available
                post_accessor = getattr(request, "post_data", None)
                if callable(post_accessor):
                    try:
                        post_data = await post_accessor()
                    except TypeError:
                        post_data = post_accessor()
                else:
                    post_data = post_accessor
        except Exception:
            post_data = None

        try:
            response_headers = await response.all_headers()
        except Exception:
            response_headers = dict(getattr(response, "headers", {}))

        status = response.status

        body_bytes: Optional[bytes] = None
        body_hash: Optional[str] = None
        body_path: Optional[str] = None
        body_size: Optional[int] = None
        body_error: Optional[str] = None

        try:
            body_bytes = await response.body()
        except Exception as exc:
            body_error = str(exc)

        if body_bytes is not None:
            body_size = len(body_bytes)
            if body_size > 0:
                body_hash = hashlib.sha256(body_bytes).hexdigest()
                if body_hash not in self._body_map and self._resources_path:
                    target_path = self._resources_path / f"{body_hash}.bin"
                    await asyncio.to_thread(target_path.write_bytes, body_bytes)
                    self._body_map[body_hash] = target_path.relative_to(
                        self._session_path
                    ).as_posix()

                body_path = self._body_map.get(body_hash)

        async with self._lock:
            self._resource_counter += 1
            resource_id = f"res_{self._resource_counter:05d}"

        initiator = (
            (headers.get("referer") or headers.get("Referer")) if headers else None
        )
        entry = {
            "id": resource_id,
            "timestamp": get_iso_datetime(),
            "url": url,
            "method": request.method,
            "resource_type": request.resource_type,
            "frame_url": request.frame.url if request.frame else None,
            "initiator": initiator,
            "status": status,
            "request_headers": headers,
            "response_headers": response_headers,
            "post_data": post_data,
            "body_path": body_path,
            "body_hash": body_hash,
            "body_size": body_size,
            "body_error": body_error,
        }

        self._resources.append(entry)
        if self._requests_log_path:
            await asyncio.to_thread(self._append_jsonl, self._requests_log_path, entry)

    def _finalize_manifest_sync(self) -> None:
        """Write manifest synchronously."""
        if not self._manifest_path:
            return

        manifest = {
            "task": {
                "id": self._task.id,
                "description": self._task.description,
                "task_type": self._task.task_type,
                "source": self._task.source,
            },
            "started_at": self._started_at,
            "finished_at": get_iso_datetime(),
            "environment": self._environment,
            "resources": self._resources,
            "request_failures": self._request_failures,
            "origins": sorted(o for o in self._origins if o),
        }

        self._manifest_path.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    @staticmethod
    def _append_jsonl(path: Path, entry: Dict[str, Any]) -> None:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    @staticmethod
    def _origin_from_url(url: str) -> Optional[str]:
        if not url:
            return None
        parts = urlsplit(url)
        if not parts.scheme or not parts.netloc:
            return None
        return f"{parts.scheme}://{parts.netloc}"

    def _finalize_sync(self) -> None:
        """Emergency cleanup called by atexit - only writes manifest."""
        if not self._active:
            return
        logger.warning("[CAPTURE] Atexit handler called - writing manifest only")
        self._finalize_manifest_sync()
        self._active = False
