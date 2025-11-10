import asyncio
import logging
from typing import Optional

import typer

from browser.browser import StealthBrowser
from browser.recorder import get_video_path
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


async def main(
    dev_mode: bool = False,
    dev_url: Optional[str] = None,
    log_browser_console: bool = True,
):
    """Main async function"""
    initial_tasks = InitialTasks()

    initial_tasks.run()
    print("Initial tasks completed")

    # Get task source, type, description and website from user
    if dev_mode:
        # Dev mode: use default values
        source = "mind2web"  # option 6
        task_type = "action"  # option 1
        task_description = "test task"
        website = dev_url if dev_url else "url"

        print("🔧 DEV MODE ENABLED")
        print("=" * 60)
        print(f"Source: {source}")
        print(f"Task Type: {task_type}")
        print(f"Description: {task_description}")
        print(f"Website: {website}")
        print("=" * 60 + "\n")
    else:
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
        stealth_browser = StealthBrowser(log_browser_console=log_browser_console)
        page = await stealth_browser.launch()

        print("Browser launched successfully!")

        # Navigate to URL if dev mode and URL provided
        if dev_mode and dev_url:
            print(f"🌐 Navigating to {dev_url}...")
            try:
                await page.goto(dev_url, wait_until="domcontentloaded", timeout=30000)
                print(f"✅ Navigated to {dev_url}")
            except Exception as e:
                print(f"⚠️  Warning: Could not navigate to {dev_url}: {e}")

        print("\n" + "=" * 60)
        print("You can now navigate to any page and interact with it.")
        if log_browser_console:
            print("Page events will be logged to the console.")
        else:
            print("Browser console logging is disabled.")
        print("=" * 60)
        print("\n💡 When you're done, press ENTER here to complete the task")
        print("=" * 60 + "\n")

        # Simple blocking wait - no async complexity
        await asyncio.to_thread(input)

        print(f'\n🛑 Task completed: "{task_description}"')
        print("🔄 Closing browser and stopping recording...")

        # Close browser cleanly - this will capture storage state and stop logging
        await stealth_browser.close()

        # Save video path to database
        task = task_manager.get_current_task()
        task_manager.set_current_task_video_path(get_video_path(task.id))
        task_manager.end_current_task()

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


def _cli(
    dev: bool = typer.Option(
        False,
        "--dev",
        help="Enable development mode with predefined task inputs.",
    ),
    dev_url: Optional[str] = typer.Option(
        None,
        "--dev-url",
        help="URL to open automatically when running in development mode.",
    ),
    log_browser_console: bool = typer.Option(
        True,
        "--browser-console/--no-browser-console",
        help="Toggle browser console logging in the terminal.",
    ),
) -> None:
    """CLI implementation executed via Typer."""
    if dev_url and not dev:
        typer.secho(
            "--dev-url requires --dev to be set.", fg=typer.colors.RED, err=True
        )
        raise typer.Exit(code=2)

    asyncio.run(
        main(dev_mode=dev, dev_url=dev_url, log_browser_console=log_browser_console)
    )


def cli():
    """CLI entry point for the web-envs command."""
    typer.run(_cli)


if __name__ == "__main__":
    # Run the async main function
    cli()
