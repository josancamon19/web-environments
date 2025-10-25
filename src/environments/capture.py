import atexit
import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from playwright.async_api import BrowserContext

from config.storage import DATA_DIR
from db.task import Task, TaskManager
from utils.get_iso_datetime import get_iso_datetime


logger = logging.getLogger(__name__)


class OfflineCaptureManager:
    """Minimal HAR-based capture pipeline for offline replay."""

    def __init__(self) -> None:
        self._active = False
        self._context: Optional[BrowserContext] = None
        self._task: Optional[Task] = None

        self._session_path: Optional[Path] = None
        self._manifest_path: Optional[Path] = None
        self._har_path: Optional[Path] = None
        self._storage_state_path: Optional[Path] = None

        self._started_at: Optional[str] = None
        self._launch_metadata: Dict[str, Any] = {}
        self._context_options: Dict[str, Any] = {}

        self._atexit_registered = False
        self._pre_close_completed = False

    def prepare_session(self) -> Dict[str, Any]:
        """Create the session directory and return context kwargs for HAR capture."""

        if self._session_path:
            return {}

        task_manager = TaskManager.get_instance()
        task = task_manager.get_actual_task()
        if not task:
            logger.warning("[CAPTURE] No active task; offline capture disabled")
            return {}

        self._task = task
        self._started_at = get_iso_datetime()

        timestamp_slug = (
            self._started_at.replace(":", "-") if self._started_at else "session"
        )
        base_path = Path(DATA_DIR) / "captures"
        self._session_path = base_path / f"task_{task.id}" / timestamp_slug
        self._session_path.mkdir(parents=True, exist_ok=True)

        self._manifest_path = self._session_path / "manifest.json"
        self._har_path = self._session_path / "session.har"
        self._storage_state_path = self._session_path / "storage_state.json"

        logger.info("[CAPTURE] Prepared session directory at %s", self._session_path)
        logger.info("[CAPTURE] HAR will be saved to: %s", self._har_path)
        logger.info(
            "[CAPTURE] Storage state will be saved to: %s", self._storage_state_path
        )

        self._pre_close_completed = False

        har_kwargs = {
            "record_har_path": str(self._har_path),
            "record_har_content": "embed",
        }
        logger.debug("[CAPTURE] HAR recording config: %s", har_kwargs)
        return har_kwargs

    def register_launch_metadata(
        self,
        *,
        browser_channel: Optional[str],
        browser_args: Optional[list[str]],
        user_data_dir: Optional[str],
    ) -> None:
        self._launch_metadata = {
            "browser_channel": browser_channel,
            "browser_args": list(browser_args or []),
            "user_data_dir": user_data_dir,
        }

    def register_context_options(self, options: Dict[str, Any]) -> None:
        sanitized = dict(options)
        sanitized.pop("record_har_path", None)
        sanitized.pop("record_har_content", None)
        self._context_options = sanitized

    async def attach(self, context: BrowserContext) -> None:
        if not self._session_path:
            logger.debug("[CAPTURE] Session not prepared; skipping attach")
            return

        if self._active:
            logger.debug("[CAPTURE] Session already active")
            return

        self._context = context
        self._active = True

        if not self._atexit_registered:
            atexit.register(self._finalize_sync)
            self._atexit_registered = True

        logger.info("[CAPTURE] Offline capture session started")

    async def stop(self) -> None:
        """Legacy stop hook maintained for backward compatibility."""
        await self.prepare_for_context_close()

    async def prepare_for_context_close(self) -> None:
        if not self._active or not self._context:
            return

        if self._pre_close_completed:
            return

        await self._capture_storage_state()
        self._pre_close_completed = True

    async def finalize_after_context_close(self) -> None:
        """Finalize capture after context is closed - wait for HAR and write manifest."""
        if not self._active:
            logger.debug("[CAPTURE] No active session to finalize")
            return

        logger.info("[CAPTURE] Finalizing capture session...")
        await self._wait_for_har_materialization()
        await self._finalize_manifest()

        self._active = False
        self._context = None
        logger.info("[CAPTURE] Offline capture session finalized successfully")

    async def _capture_storage_state(self) -> None:
        if not self._context or not self._storage_state_path:
            return
        try:
            await self._context.storage_state(path=str(self._storage_state_path))
            logger.info("[CAPTURE] Storage state saved to %s", self._storage_state_path)
        except Exception as exc:
            logger.error("[CAPTURE] Failed to capture storage state: %s", exc)

    async def _finalize_manifest(self) -> None:
        if not self._manifest_path:
            return

        manifest = {
            "task": self._serialize_task(self._task),
            "started_at": self._started_at,
            "finished_at": get_iso_datetime(),
            "har_path": self._relative_path(self._har_path),
            "storage_state": self._relative_path(self._storage_state_path),
            "context": {
                "options": self._context_options,
                "launch": self._launch_metadata,
            },
        }

        self._manifest_path.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info("[CAPTURE] Manifest written to %s", self._manifest_path)

    def _finalize_sync(self) -> None:
        """Called by atexit to ensure HAR is saved on program exit."""
        if not self._active:
            return

        try:
            asyncio.run(self._atexit_async())
        except RuntimeError:
            # Event loop already running, create a new one
            loop = asyncio.new_event_loop()
            loop.run_until_complete(self._atexit_async())
            loop.close()

    async def _atexit_async(self) -> None:
        """Async cleanup - ensures storage state and HAR are saved."""
        await self.prepare_for_context_close()

        if self._context:
            await self._context.close()
            logger.info("[CAPTURE] Context closed for HAR finalization")

        await self.finalize_after_context_close()

    @staticmethod
    def _relative_path(path: Optional[Path]) -> Optional[str]:
        if not path or not path.exists():
            return None
        return path.name if path.parent else str(path)

    @staticmethod
    def _serialize_task(task: Optional[Task]) -> Optional[Dict[str, Any]]:
        if not task:
            return None
        return {
            "id": task.id,
            "description": task.description,
            "task_type": task.task_type,
            "source": task.source,
        }

    async def _wait_for_har_materialization(self) -> None:
        """Wait for Playwright to write the HAR file after context close."""
        if not self._har_path:
            logger.warning("[CAPTURE] No HAR path configured")
            return

        max_wait_seconds = 20
        check_interval = 0.1
        max_attempts = int(max_wait_seconds / check_interval)

        for attempt in range(max_attempts):
            if self._har_path.exists():
                file_size = self._har_path.stat().st_size
                logger.info(
                    "[CAPTURE] HAR file saved successfully: %s (%d bytes)",
                    self._har_path,
                    file_size,
                )
                return
            await asyncio.sleep(check_interval)

        logger.error(
            "[CAPTURE] HAR file not found at %s after %d seconds. "
            "Offline replay will be unavailable. "
            "This usually means the context was not properly closed.",
            self._har_path,
            max_wait_seconds,
        )
