import logging
from playwright.async_api import async_playwright
from config.browser_config import BROWSER_ARGS, CONTEXT_CONFIG
from config.stealth_scripts import STEALTH_SCRIPT, PAGE_EVENT_LISTENER_SCRIPT
from steps.step_record import StepRecord
from browser.page import ActualPage, NewPageEvent
from utils.get_tasks_video_path import get_tasks_video_path
from tasks.task import TaskManager
import sys
from browser.request_event import Request_Event
from browser.response_event import Response_Event
import os
from config.storage_config import DATA_DIR
from capture.offline_capture import OfflineCaptureManager

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
        self.offline_capture = OfflineCaptureManager()
        self._binding_registered = False
        self._page_script_registered = False

    async def launch(self):
        """Launch stealth browser"""
        self.playwright = await async_playwright().start()

        # Prefer system Chrome with a persistent user profile; reduce automation fingerprints

        VIDEO_TASK_PATH = get_tasks_video_path()

        task_manager = TaskManager()
        task_manager.set_last_task_path(VIDEO_TASK_PATH)

        self.context = await self.open_browser_context(VIDEO_TASK_PATH)

        await self.offline_capture.start(self.context)

        self.context.on("request", self.request_event.listen_for_request)
        self.context.on("response", self.response_event.listen_for_response)

        self.page = await self.context.new_page()

        await self.page_event.attach_page(self.page)

        # Track new tab/page creation
        async def on_page_created(page):
            await self.step_record.record_step(
                {
                    "event_info": {
                        "event_type": "tab_opened",
                        "event_context": "state:browser",
                        "event_data": {
                            "url": page.url,
                            "timestamp": page.main_frame.name,
                        },
                    },
                    "prefix_action": "state:browser",
                    "source_page": page,
                },
                omit_screenshot=True,
            )
            await self.page_event.attach_page(page)
            await self._initialize_page_event_script(page)

        self.context.on("page", on_page_created)

        actual_page = ActualPage()
        actual_page.set_page(self.page)

        # await self.step_record.record_step(
        #     {
        #         "event_info": {
        #             "event_type": "navigate_start",
        #             "event_context": "state:page",
        #             "event_data": {"url": "https://www.google.com", "initial": True},
        #             "dom_snapshot": None,
        #         },
        #         "prefix_action": "state:page",
        #     }
        # )
        # Listen to console messages from the browser
        self.page.on("console", lambda msg: print(f"🌐 Browser console: {msg.text}"))

        await self.apply_stealth_techniques()
        await self.setup_dom_listeners()
        await self._initialize_page_event_script(self.page)

        return self.page

    async def apply_stealth_techniques(self):
        """Apply stealth techniques to avoid detection"""
        await self.page.add_init_script(STEALTH_SCRIPT)

    async def setup_dom_listeners(self):
        """Setup DOM event listeners"""
        print("🔧 Setting up DOM listeners...")

        if not self._binding_registered:

            async def _on_page_event(source, event_info):
                page = getattr(source, "page", None)
                await self.page_event_handler(event_info, page)

            await self.context.expose_binding("onPageEvent", _on_page_event)
            self._binding_registered = True

        if not self._page_script_registered:
            await self.context.add_init_script(PAGE_EVENT_LISTENER_SCRIPT)
            self._page_script_registered = True

        print("✅ DOM listeners setup complete")

    async def _initialize_page_event_script(self, page):
        if not page:
            return
        try:
            await page.evaluate(PAGE_EVENT_LISTENER_SCRIPT)
        except Exception as exc:
            logger.error("[PAGE_EVENT] Failed to initialize listener script: %s", exc)

    async def page_event_handler(self, event_info, page=None):
        """Handle page events from browser"""
        try:
            event_type = event_info.get("event_type", "unknown")
            event_context = event_info.get("event_context", "unknown")
            logger.debug(f"[PAGE_EVENT] Received: {event_context}:{event_type}")

            step_record = StepRecord()
            await step_record.record_step(
                {
                    "event_info": event_info,
                    "prefix_action": f"{event_context}",
                    "source_page": page,
                }
            )
        except Exception as e:
            logger.error(f"[PAGE_EVENT] Error handling event: {e}", exc_info=True)

    async def close(self):
        """Close browser"""
        if self.context:
            try:
                await self.offline_capture.stop()
            except Exception as exc:
                logger.error("[CAPTURE] Failed to finalize offline capture: %s", exc)
            await self.context.close()
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
        if self.page_event:
            self.page_event.detach_all_page_listeners()

    async def open_browser_context(self, video_task_path: str):
        """Open browser context"""
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
        if preferred_channel:
            try:
                self.context = await self.playwright.chromium.launch_persistent_context(
                    user_data_dir=user_data_dir,
                    channel=preferred_channel,
                    headless=False,
                    args=BROWSER_ARGS,
                    ignore_default_args=ignore_default_args,
                    record_video_dir=video_task_path,
                    record_video_size={"width": 1280, "height": 720},
                )
                return self.context
            except Exception as e:
                logger.error(f"[LAUNCH_BROWSER] Error launching browser: {e}")

        try:
            self.context = await self.playwright.chromium.launch_persistent_context(
                user_data_dir=user_data_dir,
                headless=False,
                args=BROWSER_ARGS,
                ignore_default_args=ignore_default_args,
                record_video_dir=video_task_path,
                record_video_size={"width": 1280, "height": 720},
            )
            return self.context

        except Exception as e:
            logger.error(f"[LAUNCH_BROWSER] Error launching browser: {e}")

        try:
            # Launch browser with stealth args
            self.browser = await self.playwright.chromium.launch(
                headless=False, args=BROWSER_ARGS
            )
            # self.browser.on("close", self.manual_browser_close)
            # Create context
            self.context = await self.browser.new_context(
                **CONTEXT_CONFIG,
                record_video_dir=video_task_path,
                record_video_size={"width": 1280, "height": 720},
            )
            return self.context
        except Exception as e:
            logger.error(f"[LAUNCH_BROWSER] Error launching browser: {e}")
            raise e

    async def manual_browser_close(self):
        logger.info("Browser closed manually")
        task_manager = TaskManager()
        task_manager.end_actual_task()
        last_task_path = task_manager.get_last_task_path()
        logger.info(f"Last task path: {last_task_path}")
        await self.close()
        sys.exit(0)
