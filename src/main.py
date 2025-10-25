import asyncio
import signal
import logging
import atexit

from browser.browser import StealthBrowser
from config.start import InitialTasks
from db.task import TaskManager, CreateTaskDto, Task
from utils.get_task_description import (
    get_task_description_from_user,
    get_task_type_from_user,
    get_answer_from_user,
    get_source_from_user,
    get_website_from_user,
)

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("recorder_debug.log")],
)

logger = logging.getLogger(__name__)


async def main():
    """Main async function"""
    initial_tasks = InitialTasks()
    initial_tasks.run()
    print("Initial tasks completed")

    source = get_source_from_user()
    task_type = get_task_type_from_user()
    task_description = get_task_description_from_user()
    website = get_website_from_user()

    stealth_browser = None
    shutdown_started = False
    shutdown_complete = None

    async def shutdown(task_manager: TaskManager):
        nonlocal shutdown_started
        if shutdown_started:
            return
        shutdown_started = True
        try:
            print(f'\n🛑 Task completed: "{task_description}"')

            if task_type == "information_retrieval":
                answer = await asyncio.to_thread(get_answer_from_user)
                task_manager.save_task_answer(answer)

            print("🔄 Closing browser...")
            task_manager.end_actual_task()
            task_manager.save_task_video(task_manager.get_last_task_path())

            if stealth_browser:
                await stealth_browser.close()
        except Exception as e:
            logger.exception("[SHUTDOWN] Error during shutdown: %s", e)
        finally:
            if shutdown_complete:
                shutdown_complete.set()

    try:
        task_manager = TaskManager.get_instance()
        new_task = CreateTaskDto(task_description, task_type, source, website)
        task_id = task_manager.save_task(new_task)
        task_manager.set_actual_task(Task(task_id, task_description, task_type, source, website))
        logger.info(f"Task saved: {task_id}")

        print(f'🚀 Launching stealth browser for task: "{task_description}"...')
        stealth_browser = StealthBrowser()
        _ = await stealth_browser.launch()
        print("Browser launched successfully!")

        print("You can now navigate to any page and interact with it.")
        print("Page events will be logged to the console.")
        print("Press Ctrl+C to exit")

        loop = asyncio.get_running_loop()
        shutdown_complete = asyncio.Event()

        # Signals (Unix/Windows compatible)
        def _schedule_shutdown():
            task = asyncio.create_task(shutdown(task_manager))
            task.add_done_callback(
                lambda t: t.exception() and logger.exception("[SHUTDOWN TASK] %s", t.exception())
            )

        for sig in (getattr(signal, "SIGINT", None),
                    getattr(signal, "SIGTERM", None),
                    getattr(signal, "SIGHUP", None)):
            if sig is None:
                continue
            try:
                loop.add_signal_handler(sig, _schedule_shutdown)
            except (NotImplementedError, RuntimeError):
                # Windows or environments without add_signal_handler support
                signal.signal(sig, lambda s, f: _schedule_shutdown())

        # Fallback atexit
        def _close_on_exit():
            if stealth_browser and not getattr(stealth_browser, "_closed", False):
                try:
                    new_loop = asyncio.new_event_loop()
                    try:
                        new_loop.run_until_complete(stealth_browser.close())
                    finally:
                        new_loop.close()
                except Exception:
                    pass

        atexit.register(_close_on_exit)

        await shutdown_complete.wait()

    except Exception as error:
        print(f"An error occurred while executing the task: {error}")
        logger.exception("[MAIN] Unhandled error: %s", error)
    finally:
        try:
            if stealth_browser and not getattr(stealth_browser, "_closed", False):
                await stealth_browser.close()
        except Exception:
            pass


def cli():
    """CLI entry point for the web-envs command."""
    asyncio.run(main())


if __name__ == "__main__":
    cli()
