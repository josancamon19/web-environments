import logging
from playwright.async_api import BrowserContext, async_playwright
from config.browser_config import BROWSER_ARGS, CONTEXT_CONFIG
from config.browser_scripts import STEALTH_SCRIPT, PAGE_EVENT_LISTENER_SCRIPT
from browser.recorder import Recorder
from browser.page import ActualPage
from browser.handlers.new_page_event import NewPageEvent
from utils.get_tasks_video_path import get_tasks_video_path
from db.task import TaskManager
import sys
from browser.handlers.request_event import RequestEvent
from browser.handlers.response_event import ResponseEvent
import os
from environments.capture import OfflineCaptureManager

logger = logging.getLogger(__name__)


class StealthBrowser:
    def __init__(self):
        self.playwright = None
        self.context = None
        self.page = None
        self.recorder = Recorder()
        self.environment_capturer = OfflineCaptureManager()

        self.request_event_handler = RequestEvent()
        self.response_event_handler = ResponseEvent()
        self.page_event_handler = NewPageEvent()

        self._binding_registered = False
        self._page_script_registered = False

    async def launch(self):
        """Launch stealth browser"""
        self.playwright = await async_playwright().start()

        VIDEO_TASK_PATH = get_tasks_video_path()

        task_manager = TaskManager()
        task_manager.set_last_task_path(VIDEO_TASK_PATH)

        # Get HAR path for this task before starting capture
        task = task_manager.get_actual_task()
        har_path = self.environment_capturer.get_har_path(task.id)

        self.context = await self.launch_browser(VIDEO_TASK_PATH, har_path)
        await self.environment_capturer.start(self.context)
        self.context.on("request", self.request_event_handler.listen)
        self.context.on("response", self.response_event_handler.listen)

        # Ensure bindings/scripts for any subsequent pages/documents
        await self.setup_dom_listeners()
        self.page = await self.context.new_page()
        await self.page_event_handler.attach_page(self.page)

        # Track new tab/page creation
        async def on_page_created(page):
            await self.recorder.record_step(
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
            await self.page_event_handler.attach_page(page)
            await self._initialize_page_event_script(page)

        self.context.on("page", on_page_created)

        actual_page = ActualPage()
        actual_page.set_page(self.page)

        async def console_handler(msg):
            # Skip common noisy warnings
            text = msg.text
            if "Blocked script execution in 'about:blank'" in text:
                return
            if "Failed to execute 'postMessage'" in text:
                return
            if "Blocked script execution in" in text:
                return
            print(f"🌐 Browser console: {text}")

        self.page.on("console", console_handler)

        await self.apply_stealth_techniques()
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
                try:
                    logger.info(
                        f"[BINDING] _on_page_event called with event_type: {event_info.get('event_type', 'unknown')}"
                    )
                    page = getattr(source, "page", None)
                    await self.handle_page_event(event_info, page)
                except Exception as e:
                    logger.error(
                        f"[BINDING] Error in _on_page_event: {e}", exc_info=True
                    )

            # Expose at context-level
            await self.context.expose_binding("onPageEvent", _on_page_event)
            self._binding_registered = True

        if not self._page_script_registered:
            # Ensure scripts initialize before any content
            await self.context.add_init_script(PAGE_EVENT_LISTENER_SCRIPT)
            self._page_script_registered = True

        print("✅ DOM listeners setup complete")

    async def _initialize_page_event_script(self, page):
        if not page:
            return
        try:
            # Expose at page-level as a fallback (useful for certain CSP/isolated worlds)
            try:

                async def _page_binding(source, event_info):
                    p = getattr(source, "page", None)
                    await self.handle_page_event(event_info, p)

                await page.expose_binding("onPageEvent", _page_binding)
            except Exception:
                pass
            await page.evaluate(PAGE_EVENT_LISTENER_SCRIPT)
            try:
                # Also try to install on existing frames
                for frame in page.frames:
                    try:
                        await frame.evaluate(PAGE_EVENT_LISTENER_SCRIPT)
                    except Exception:
                        pass
            except Exception:
                pass
        except Exception as exc:
            logger.error("[PAGE_EVENT] Failed to initialize listener script: %s", exc)

    async def handle_page_event(self, event_info, page=None):
        """Handle page events from browser"""
        try:
            event_type = event_info.get("event_type", "unknown")
            event_context = event_info.get("event_context", "unknown")
            logger.debug(f"[PAGE_EVENT] Received: {event_context}:{event_type}")

            await self.recorder.record_step(
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
        await self.environment_capturer.stop()
        await self.context.close()
        await self.playwright.stop()
        self.page_event_handler.detach_all_page_listeners()

    async def launch_browser(
        self, video_task_path: str, har_path: str
    ) -> BrowserContext:
        """Open browser context"""
        preferred_channel = (
            os.environ.get("RECORDER_BROWSER_CHANNEL", "chrome").strip() or None
        )
        # args = ["--disable-blink-features=AutomationControlled"]
        ignore_default_args = [
            "--enable-automation",
            "--use-mock-keychain",
            "--password-store=basic",
        ]
        # user_data_dir = os.path.join(DATA_DIR, "user-data") # Doesn't seem to be needed for anything right now
        # self.context = await self.playwright.chromium.launch_persistent_context(
        #     user_data_dir=user_data_dir,
        #     channel=preferred_channel,
        #     headless=False,
        #     args=BROWSER_ARGS,
        #     ignore_default_args=ignore_default_args,
        #     bypass_csp=True,
        #     record_video_dir=video_task_path,
        #     record_video_size={"width": 1280, "height": 720},
        # )

        # TODO: check a website that uses indexedDB, see if it's being collected
        # TODO: should an env be launched with the storage state generated from capture? no right? that would avoid the need for signing in, it should probably launch clean
        # TODO: post data obsfucation, to handle edge case matching or LM selection for POST requests?
        # - Normalize JSON (remove volatile fields; sort keys) and hash; tolerate multipart boundary changes; ignore known nonce/timestamp params.
        # TODO: collect a couple of tasks this way
        # TODO: refactor to use peewee ORM for db, to avoid random parsing and select bs everywhere

        # TODO: does the agent launch works?
        # TODO: does the agent when evaluated works on the environment?
        # TODO: improve launching and running the environment

        # TODO: SPA based changes pages work?
        # TODO: difference between page event handler and handle page event here

        # ====== once this works well ======

        # TODO: collect env with further n steps depth

        browser = await self.playwright.chromium.launch(
            channel=preferred_channel,
            headless=False,
            args=BROWSER_ARGS,
            ignore_default_args=ignore_default_args,
        )

        self.context = await browser.new_context(
            **CONTEXT_CONFIG,
            bypass_csp=True,
            record_video_dir=video_task_path,
            record_video_size={"width": 1280, "height": 720},
            record_har_path=har_path,
            record_har_mode="full",
        )
        return self.context

    async def manual_browser_close(self):
        logger.info("Browser closed manually")
        task_manager = TaskManager()
        task_manager.end_actual_task()
        last_task_path = task_manager.get_last_task_path()
        logger.info(f"Last task path: {last_task_path}")
        await self.close()
        sys.exit(0)
