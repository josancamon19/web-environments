import asyncio
import signal
import logging
from browser.stealth_browser import StealthBrowser
from config.initial_tasks import InitialTasks
from tasks.task import TaskManager, CreateTaskDto, Task
from utils.get_task_description import (
    get_task_description_from_user,
    get_task_type_from_user,
    get_answer_from_user,
    get_source_from_user,
)

logging.basicConfig(
    level=logging.DEBUG,  # Changed to DEBUG to see all logs
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("recorder_debug.log"),  # Also save to file for analysis
    ],
)

logger = logging.getLogger(__name__)


async def main():
    """Main async function"""
    initial_tasks = InitialTasks()

    initial_tasks.run()
    print("Initial tasks completed")

    # Get task source, type and description from user
    source = get_source_from_user()
    task_type = get_task_type_from_user()
    task_description = get_task_description_from_user()

    try:
        # Get TaskManager singleton instance
        task_manager = TaskManager.get_instance()

        new_task = CreateTaskDto(task_description, task_type, source)
        task_id = task_manager.save_task(new_task)
        task_manager.set_actual_task(Task(task_id, task_description, task_type, source))

        logger.info(f"Task saved: {task_id}")

        print(f'🚀 Launching stealth browser for task: "{task_description}"...')
        stealth_browser = StealthBrowser()
        await stealth_browser.launch()

        print("Browser launched successfully!")

        print("You can now navigate to any page and interact with it.")
        print("Page events will be logged to the console.")
        print("Press Ctrl+C to exit")

        loop = asyncio.get_running_loop()
        shutdown_complete = asyncio.Event()
        shutdown_started = False

        async def shutdown():
            print(f'\n🛑 Task completed: "{task_description}"')

            if task_type == "information_retrieval":
                answer = await asyncio.to_thread(get_answer_from_user)
                task_manager.save_task_answer(answer)

            print("🔄 Closing browser...")
            task_manager.end_actual_task()
            task_manager.save_task_video(task_manager.get_last_task_path())
            await stealth_browser.close()
            shutdown_complete.set()

        def signal_handler(signum, frame):
            nonlocal shutdown_started
            if not shutdown_started:
                shutdown_started = True
                loop.create_task(shutdown())

        signal.signal(signal.SIGINT, signal_handler)

        # Leave it on about:blank
        # await stealth_browser.page.goto("https://www.google.com")
        # await stealth_browser.page.wait_for_load_state("domcontentloaded")

        await shutdown_complete.wait()

    except Exception as error:
        print(f"Ha ocurrido un error al ejecutar la tarea: {error}")
        await stealth_browser.close()


def cli():
    """CLI entry point for the web-envs command."""
    asyncio.run(main())


if __name__ == "__main__":
    # Run the async main function
    cli()
