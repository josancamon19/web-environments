import asyncio
import logging
import os
import sys
from rebrowser_playwright.async_api import BrowserContext, async_playwright
from config.browser_config import BROWSER_ARGS, CONTEXT_CONFIG, IGNORE_DEFAULT_ARGS
from config.browser_scripts import STEALTH_SCRIPT, PAGE_EVENT_LISTENER_SCRIPT
from browser.recorder import Recorder, get_video_path
from browser.page import ActualPage
from browser.handlers.new_page_event import (
    PlaywrightPageEvent,
    should_ignore_recording_url,
)
from db.task import TaskManager
from browser.handlers.request_event import RequestEvent
from browser.handlers.response_event import ResponseEvent
from environments.capture import OfflineCaptureManager
# from playwright_stealth import Stealth

logger = logging.getLogger(__name__)


class StealthBrowser:
    def __init__(self, log_browser_console: bool = True):
        self.playwright = None
        self.context = None
        self.page = None
        self.recorder = Recorder()
        self.environment_capturer = OfflineCaptureManager()

        self.request_event_handler = RequestEvent()
        self.response_event_handler = ResponseEvent()
        # Handles non DOM change events, like page load, tab opened, tab closed, etc.
        self.playwright_page_handler = PlaywrightPageEvent()

        self._binding_registered = False
        self._page_script_registered = False
        self.log_browser_console = log_browser_console
        # self.stealth = Stealth(
        # script_logging=True,
        # navigator_languages_override=("fr-FR", "fr")
        # )

    async def launch(self):
        """Launch stealth browser"""
        self.playwright = await async_playwright().start()

        task_manager = TaskManager()
        task = task_manager.get_current_task()

        self.context = await self.launch_browser(task.id)
        # Apply stealth to the entire context - all pages created will have stealth applied
        # await self.stealth.apply_stealth_async(self.context)

        await self.environment_capturer.start(self.context)
        # Paused: Already collecting HAR, no need to save requests/responses to database
        # self.context.on("request", self.request_event_handler.listen)
        # self.context.on("response", self.response_event_handler.listen)

        # Ensure bindings/scripts for any subsequent pages/documents
        await self.setup_context_dom_listeners()
        self.page = await self.context.new_page()
        await self.playwright_page_handler.attach(self.page)

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
            await self.playwright_page_handler.attach(page)
            await self.setup_per_page_dom_listeners(page)

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
            if "Failed to load resource: net::ERR_NAME_NOT_RESOLVED" in text:
                return
            print(f"🌐 Browser console: {text}")

        if self.log_browser_console:
            self.page.on("console", console_handler)

        # print(f"languages = {await self.page.evaluate('navigator.languages')}")

        await self.setup_per_page_dom_listeners(self.page)

        return self.page

    async def apply_stealth_techniques(self):
        """Apply stealth techniques to avoid detection"""
        await self.page.add_init_script(STEALTH_SCRIPT)

    async def setup_context_dom_listeners(self):
        """Setup context-level DOM event listeners"""
        print("🔧 Setting up DOM listeners...")

        if not self._binding_registered:

            async def _on_page_event(source, event_info):
                try:
                    # logger.info(
                    #     f"[BINDING] _on_page_event called with event_type: {event_info.get('event_type', 'unknown')}"
                    # )
                    page = getattr(source, "page", None)
                    await self.handle_dom_change_event(event_info, page)
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

    async def setup_per_page_dom_listeners(self, page):
        """Fallback to page-level binding if context-level binding is not available"""
        if not page:
            return
        try:
            # Expose at page-level as a fallback (useful for certain CSP/isolated worlds)
            try:

                async def _page_binding(source, event_info):
                    p = getattr(source, "page", None)
                    await self.handle_dom_change_event(event_info, p)

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

    async def handle_dom_change_event(self, event_info, page=None):
        """Handle page events from browser"""
        try:
            event_type = event_info.get("event_type", "unknown")
            event_context = event_info.get("event_context", "unknown")

            # Filter out tracking/analytics iframe events
            if (event_type == "load" and event_context == "state:page") or (
                event_type == "tab_visibility_changed"
                and event_context == "state:browser"
            ):
                event_data = event_info.get("event_data", {})
                url = event_data.get("url", "")
                if should_ignore_recording_url(url):
                    # logger.debug(f"[PAGE_EVENT] Skipping tracking URL from JS: {url}")
                    return

            # logger.debug(f"[PAGE_EVENT] Received: {event_context}:{event_type}")
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
        logger.info("[CLOSE] Starting browser close sequence...")

        # Stop recording first to prevent analytics/tracking events during shutdown
        await self.recorder.stop_recording()

        self.playwright_page_handler.detach_all_page_listeners()
        # self.context.remove_listener("request", self.request_event_handler.listen)
        # self.context.remove_listener("response", self.response_event_handler.listen)

        for page in self.context.pages:
            # await page.close()
            try:
                await page.goto("about:blank", timeout=1000, wait_until="commit")
            except Exception:
                pass

        await asyncio.sleep(0.5)
        await self.environment_capturer.stop()
        await self.context.close()
        await self.playwright.stop()

        logger.info("[CLOSE] Browser close sequence completed")

    async def launch_browser(self, task_id: int) -> BrowserContext:
        """Open browser context"""
        preferred_channel = (
            os.environ.get("RECORDER_BROWSER_CHANNEL", "chrome").strip() or None
        )

        browser = await self.playwright.chromium.launch(
            channel=preferred_channel,
            headless=False,
            args=BROWSER_ARGS,
            ignore_default_args=IGNORE_DEFAULT_ARGS,
        )

        self.context = await browser.new_context(
            **CONTEXT_CONFIG,
            bypass_csp=True,
            record_video_dir=get_video_path(task_id),
            record_video_size={"width": 1280, "height": 720},
            record_har_path=self.environment_capturer.get_har_path(task_id),
            record_har_mode="full",
        )
        return self.context

    async def manual_browser_close(self):
        logger.info("Browser closed manually")
        task_manager = TaskManager()
        task = task_manager.get_current_task()
        task_manager.set_current_task_video_path(get_video_path(task.id))
        task_manager.end_current_task()
        await self.close()
        sys.exit(0)
