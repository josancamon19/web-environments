import logging
from typing import Any, Dict, Optional
from playwright.async_api import async_playwright
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
from config.storage import DATA_DIR
from environments.capture import OfflineCaptureManager

logger = logging.getLogger(__name__)


class StealthBrowser:
    def __init__(self):
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self.request_event = RequestEvent()
        self.response_event = ResponseEvent()
        self.step_record = Recorder()
        self.page_event = NewPageEvent()
        self.offline_capture = OfflineCaptureManager()
        self._binding_registered = False
        self._page_script_registered = False

    async def launch(self):
        """Launch stealth browser"""
        self.playwright = await async_playwright().start()

        # Prefer system Chrome with a persistent user profile; reduce automation fingerprints

        VIDEO_TASK_PATH = get_tasks_video_path()
        capture_context_kwargs = self.offline_capture.prepare_session()

        task_manager = TaskManager()
        task_manager.set_last_task_path(VIDEO_TASK_PATH)

        self.context = await self.open_browser_context(
            VIDEO_TASK_PATH, capture_context_kwargs
        )

        await self.offline_capture.attach(self.context)

        self.context.on("request", self.request_event.listen_for_request)
        self.context.on("response", self.response_event.listen_for_response)

        # Ensure bindings/scripts for any subsequent pages/documents
        await self.setup_dom_listeners()

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
        self.page.on("console", lambda msg: print(f"🌐 Browser console: {msg.text}"))

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
                page = getattr(source, "page", None)
                await self.page_event_handler(event_info, page)

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
                    await self.page_event_handler(event_info, p)

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

    async def page_event_handler(self, event_info, page=None):
        """Handle page events from browser"""
        try:
            event_type = event_info.get("event_type", "unknown")
            event_context = event_info.get("event_context", "unknown")
            logger.debug(f"[PAGE_EVENT] Received: {event_context}:{event_type}")

            step_record = Recorder()
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
            await self.offline_capture.prepare_for_context_close()
            try:
                await self.context.close()
            except Exception as exc:
                logger.error("[CAPTURE] Error closing browser context: %s", exc)
        if self.browser:
            try:
                await self.browser.close()
            except Exception as exc:
                logger.error("[CAPTURE] Error closing browser: %s", exc)
        try:
            await self.offline_capture.finalize_after_context_close()
        except Exception as exc:
            logger.error("[CAPTURE] Failed to finalize offline capture: %s", exc)
        if self.playwright:
            await self.playwright.stop()
        if self.page_event:
            self.page_event.detach_all_page_listeners()

    async def open_browser_context(
        self, video_task_path: str, capture_context_kwargs: Optional[Dict[str, Any]]
    ):
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
        capture_context_kwargs = capture_context_kwargs or {}
        self.offline_capture.register_launch_metadata(
            browser_channel=preferred_channel,
            browser_args=BROWSER_ARGS,
            user_data_dir=user_data_dir,
        )
        if preferred_channel:
            try:
                self.context = await self.playwright.chromium.launch_persistent_context(
                    user_data_dir=user_data_dir,
                    channel=preferred_channel,
                    headless=False,
                    args=BROWSER_ARGS,
                    ignore_default_args=ignore_default_args,
                    bypass_csp=True,
                    record_video_dir=video_task_path,
                    record_video_size={"width": 1280, "height": 720},
                    **capture_context_kwargs,
                )
                self.offline_capture.register_context_options(
                    {
                        "mode": "persistent",
                        "channel": preferred_channel,
                        "user_data_dir": user_data_dir,
                        "record_video_dir": video_task_path,
                    }
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
                bypass_csp=True,
                record_video_dir=video_task_path,
                record_video_size={"width": 1280, "height": 720},
                **capture_context_kwargs,
            )
            self.offline_capture.register_context_options(
                {
                    "mode": "persistent",
                    "channel": preferred_channel,
                    "user_data_dir": user_data_dir,
                    "record_video_dir": video_task_path,
                }
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
            context_kwargs: Dict[str, Any] = {
                **CONTEXT_CONFIG,
                "bypass_csp": True,
                "record_video_dir": video_task_path,
                "record_video_size": {"width": 1280, "height": 720},
                **capture_context_kwargs,
            }
            self.context = await self.browser.new_context(**context_kwargs)
            self.offline_capture.register_context_options(context_kwargs)
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
