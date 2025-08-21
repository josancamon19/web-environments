import asyncio
import logging
from playwright.async_api import async_playwright
from browser_config import BROWSER_ARGS, CONTEXT_CONFIG
from stealth_scripts import STEALTH_SCRIPT, PAGE_EVENT_LISTENER_SCRIPT
from stepRecord import StepRecord
from actual_page import ActualPage
from utils.get_tasks_video_path import get_tasks_video_path
from task import TaskManager
import sys
from request_event import Request_Event
from response_event import Response_Event
from new_page_event import NewPageEvent
import os
from storage_config import DATA_DIR

logger = logging.getLogger(__name__)

class StealthBrowser:
    def __init__(self):
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self.request_event = Request_Event()
        self.response_event = Response_Event()
        self.step_record = StepRecord()
        self.page_event = NewPageEvent()

    async def launch(self):
        """Launch stealth browser"""
        self.playwright = await async_playwright().start()
        
 # Prefer system Chrome with a persistent user profile; reduce automation fingerprints
        preferred_channel = (
            os.environ.get("RECORDER_BROWSER_CHANNEL", "chrome").strip() or None
        )
        user_data_dir = os.environ.get("RECORDER_USER_DATA_DIR") or os.path.join(
            DATA_DIR, "user-data"
        )
        # args = ["--disable-blink-features=AutomationControlled"]
        ignore_default_args = [
            "--enable-automation",
            "--use-mock-keychain",
            "--password-store=basic",
        ]
        VIDEO_TASK_PATH = get_tasks_video_path()\
        
        task_manager = TaskManager()
        task_manager.set_last_task_path(VIDEO_TASK_PATH)  

        if preferred_channel:
            try:
                self.context = await self.playwright.chromium.launch_persistent_context(
                    user_data_dir=user_data_dir,
                    channel=preferred_channel,
                    headless=False,
                    args=BROWSER_ARGS,
                    ignore_default_args=ignore_default_args,
                    record_video_dir=VIDEO_TASK_PATH,
                    record_video_size={"width": 1280, "height": 720},
                )
            except Exception as e:
                logger.error(f"[LAUNCH_BROWSER] Error launching browser: {e}")
        try:
            self.context = await self.playwright.chromium.launch_persistent_context(
                user_data_dir=user_data_dir,
                headless=False,
                args=BROWSER_ARGS,
                ignore_default_args=ignore_default_args,
                record_video_dir=VIDEO_TASK_PATH,
                record_video_size={"width": 1280, "height": 720},
            )
        except Exception as e:
            logger.error(f"[LAUNCH_BROWSER] Error launching browser: {e}")

        try:
            # Launch browser with stealth args
            self.browser = await self.playwright.chromium.launch(
                headless=False,
                args=BROWSER_ARGS
            )
            # self.browser.on("close", self.manual_browser_close)
            # Create context
            self.context = await self.browser.new_context(**CONTEXT_CONFIG, record_video_dir=VIDEO_TASK_PATH, record_video_size={'width': 1280, 'height': 720})

        except Exception as e:
            logger.error(f"[LAUNCH_BROWSER] Error launching browser: {e}")
            raise e

        self.context.on("request", self.request_event.listen_for_request)
        self.context.on("response", self.response_event.listen_for_response)

        self.page = await self.context.new_page()

        self.page_event.attach_page(self.page)

        self.context.on("page", self.page_event.attach_page)

        actual_page = ActualPage()
        actual_page.set_page(self.page)

        await self.step_record.record_step(
            {
                'event_info': {
                    'event_type': "navigate_start",
                    'event_context': "state:page",
                    'event_data': {"url": "https://www.google.com", "initial": True},
                    'dom_snapshot': None,
                },
                "prefix_action" : "state:page"
            }
        )
        # Listen to console messages from the browser
        # self.page.on("console", lambda msg: print(f"🌐 Browser console: {msg.text}"))
        
        await self.apply_stealth_techniques()
        await self.setup_dom_listeners()

        return self.page

    async def apply_stealth_techniques(self):
        """Apply stealth techniques to avoid detection"""
        await self.page.add_init_script(STEALTH_SCRIPT)

    async def setup_dom_listeners(self):
        """Setup DOM event listeners"""
        print("🔧 Setting up DOM listeners...")
        await self.page.expose_function("onPageEvent", self.page_event_handler)
        await self.page.add_init_script(PAGE_EVENT_LISTENER_SCRIPT)
        print("✅ DOM listeners setup complete")

    async def page_event_handler(self, event_info):
        """Log click events"""
        step_record = StepRecord()
        await step_record.record_step({
            'event_info': event_info,
            "prefix_action" : f"{event_info['event_context']}"
        })

    async def close(self):
        """Close browser"""
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
        if self.page_event:
            self.page_event.detach_all_page_listeners()
      

    async def manual_browser_close(self):
        logger.info("Browser closed manually")
        task_manager = TaskManager()
        task_manager.end_actual_task()
        last_task_path = task_manager.get_last_task_path()
        logger.info(f"Last task path: {last_task_path}")
        await self.close()
        sys.exit(0)