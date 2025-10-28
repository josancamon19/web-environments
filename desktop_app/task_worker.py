"""Background worker process used by the desktop task collector GUI."""

from __future__ import annotations

import asyncio
import logging
from multiprocessing.connection import Connection
from typing import Any, Dict, Optional

from browser.browser import StealthBrowser
from browser.recorder import get_video_path
from config.start import InitialTasks
from db.task import CreateTaskDto, TaskManager
from db.models import TaskModel

logger = logging.getLogger(__name__)


def _send_safe(pipe: Connection, payload: Dict[str, Any]) -> None:
    """Send a message to the GUI process, ignoring broken pipes."""
    try:
        pipe.send(payload)
    except (BrokenPipeError, EOFError):
        logger.debug("Pipe closed; could not deliver message: %s", payload.get("type"))


async def _wait_for_command(pipe: Connection) -> Dict[str, Any]:
    """Wait for the GUI to send a completion or cancel command."""
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(None, pipe.recv)
    except (BrokenPipeError, EOFError):
        return {"type": "cancel"}


def run_task_worker(
    pipe: Connection,
    description: str,
    task_type: str,
    source: str,
    website: Optional[str] = None,
) -> None:
    """Entry point executed inside a separate process to run Playwright safely."""

    async def runner() -> None:
        stealth_browser: Optional[StealthBrowser] = None
        task_manager: Optional[TaskManager] = None
        answer_to_save: Optional[str] = None

        try:
            _send_safe(pipe, {"type": "log", "message": "Running initial setup…"})
            InitialTasks().run()
            _send_safe(pipe, {"type": "log", "message": "✔️ Initial setup complete."})

            task_manager = TaskManager.get_instance()
            new_task = CreateTaskDto(description, task_type, source, website)
            task_id = task_manager.create_task(new_task)
            # Get the task we just created
            task_model = TaskModel.get_by_id(task_id)
            task_manager.set_current_task(task_model)
            _send_safe(pipe, {"type": "task_started", "task_id": task_id})
            _send_safe(
                pipe,
                {
                    "type": "log",
                    "message": f"Task stored with ID {task_id}. Launching stealth browser…",
                },
            )

            stealth_browser = StealthBrowser()
            await stealth_browser.launch()

            _send_safe(pipe, {"type": "browser_ready"})
            _send_safe(
                pipe,
                {
                    "type": "log",
                    "message": "🌐 Browser launched. Complete the task, then click 'Complete Task'.",
                },
            )

            command = await _wait_for_command(pipe)
            command_type = command.get("type")
            if command_type != "complete":
                raise RuntimeError("Task cancelled before completion.")

            answer_to_save = command.get("answer")

        except Exception as exc:  # pylint: disable=broad-except
            logger.exception("Worker process encountered an error")
            _send_safe(pipe, {"type": "finished", "success": False, "error": str(exc)})
            return
        finally:
            try:
                if task_manager and answer_to_save is not None:
                    task_manager.set_current_task_answer(answer_to_save)
            except Exception as answer_error:  # pylint: disable=broad-except
                logger.warning("Failed to persist task answer: %s", answer_error)

            try:
                if task_manager:
                    current_task = task_manager.get_current_task()
                    if current_task:
                        # Save video path to database
                        task_manager.set_current_task_video_path(
                            get_video_path(current_task.id)
                        )
                    task_manager.end_current_task()
                    task_manager.set_current_task(None)  # type: ignore[arg-type]
            except Exception as mgr_error:  # pylint: disable=broad-except
                logger.warning("Failed to finalise task metadata: %s", mgr_error)

            try:
                if stealth_browser:
                    await stealth_browser.close()
            except Exception as close_error:  # pylint: disable=broad-except
                logger.warning("Failed to close browser cleanly: %s", close_error)

        _send_safe(pipe, {"type": "finished", "success": True})

    asyncio.run(runner())
    try:
        pipe.close()
    except Exception:  # pylint: disable=broad-except
        pass
