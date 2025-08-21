import asyncio
import signal
import sys
import logging
from stealth_browser import StealthBrowser
from initial_tasks import InitialTasks
from task import TaskManager, CreateTaskDto  , Task          
from utils.get_task_description import get_task_description_from_user

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)

async def main():
    """Main async function"""
    initial_tasks = InitialTasks()

    initial_tasks.run()
    print("Initial tasks completed")
    
    # Get task description from user
    task_description = get_task_description_from_user()

    try:
        # Get TaskManager singleton instance
        task_manager = TaskManager.get_instance()

        new_task = CreateTaskDto(task_description)
        task_id = task_manager.save_task(new_task)
        task_manager.set_actual_task(Task(task_id, task_description))

        logger.info(f'Task saved: {task_id}')

        print(f'🚀 Launching stealth browser for task: "{task_description}"...')
        stealth_browser = StealthBrowser()  
        await stealth_browser.launch()

        print('Browser launched successfully!')

        print('You can now navigate to any page and interact with it.')
        print('Page events will be logged to the console.')
        print('Press Ctrl+C to exit')
        
        def signal_handler(signum, frame):
            print(f'\n🛑 Task completed: "{task_description}"')
            print('🔄 Closing browser...')
            asyncio.create_task(stealth_browser.close())
            sys.exit(0)
        
        signal.signal(signal.SIGINT, signal_handler)
        
        # Navigate to a page to start
        await stealth_browser.page.goto('https://www.google.com')
        
        await stealth_browser.page.wait_for_load_state('domcontentloaded')
        
        await asyncio.Event().wait()
        
    except Exception as error:
        print(f'Error: {error}')
        await stealth_browser.close()

if __name__ == "__main__":
    # Run the async main function
    asyncio.run(main())