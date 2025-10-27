import asyncio
import logging
from browser.browser import StealthBrowser
from config.start import InitialTasks
from db.task import TaskManager, CreateTaskDto
from db.models import TaskModel
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
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("recorder_debug.log"),
    ],
)

# Suppress verbose Peewee SQL debug logs
logging.getLogger("peewee").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


async def main():
    """Main async function"""
    initial_tasks = InitialTasks()

    initial_tasks.run()
    print("Initial tasks completed")

    # Get task source, type, description and website from user
    source = get_source_from_user()
    task_type = get_task_type_from_user()
    task_description = get_task_description_from_user()
    website = get_website_from_user()

    try:
        # Get TaskManager singleton instance
        task_manager = TaskManager.get_instance()

        new_task = CreateTaskDto(task_description, task_type, source, website)
        task_id = task_manager.create_task(new_task)
        # Get the task we just created
        task_model = TaskModel.get_by_id(task_id)
        task_manager.set_current_task(task_model)

        logger.info(f"Task saved: {task_id}")

        print(f'🚀 Launching stealth browser for task: "{task_description}"...')
        stealth_browser = StealthBrowser()
        await stealth_browser.launch()

        print("Browser launched successfully!")

        print("\n" + "=" * 60)
        print("You can now navigate to any page and interact with it.")
        print("Page events will be logged to the console.")
        print("=" * 60)
        print("\n💡 When you're done, press ENTER here to complete the task")
        print("=" * 60 + "\n")

        # Simple blocking wait - no async complexity
        await asyncio.to_thread(input)

        print(f'\n🛑 Task completed: "{task_description}"')
        print("🔄 Closing browser and stopping recording...")

        # Close browser cleanly - this will capture storage state and stop logging
        await stealth_browser.close()

        task_manager.end_current_task()
        task_manager.set_current_task_video_path(task_manager.get_last_task_path())

        print("✅ Browser closed and recording saved")

        # Now ask for answer AFTER everything is closed and quiet
        if task_type == "information_retrieval":
            print("\n" + "=" * 60)
            answer = await asyncio.to_thread(get_answer_from_user)
            task_manager.set_current_task_answer(answer)
            print("=" * 60)

    except Exception as error:
        print(f"Error while executing task {error}")
        await stealth_browser.close()


def cli():
    """CLI entry point for the web-envs command."""
    asyncio.run(main())


if __name__ == "__main__":
    # Run the async main function
    cli()
