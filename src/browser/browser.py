import logging
import os
import sys
import asyncio
from playwright.async_api import async_playwright
from config.browser_config import BROWSER_ARGS, CONTEXT_CONFIG
from config.browser_scripts import STEALTH_SCRIPT, PAGE_EVENT_LISTENER_SCRIPT
from browser.recorder import Recorder
from browser.page import ActualPage
from browser.handlers.new_page_event import NewPageEvent
from utils.get_tasks_video_path import get_tasks_video_path
from db.task import TaskManager
from browser.handlers.request_event import RequestEvent
from browser.handlers.response_event import ResponseEvent
from environments.capture import OfflineCaptureManager
from config.storage import DATA_DIR

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

        # Lifecycle state
        self._closed = False
        self._ctx_closed = False
        self._capture_stopped = False
        self._browser_closed = False
        self._har_attempted = False
        self._shutting_down = False
        self._shutdown_lock = asyncio.Lock()

        # Page tracking
        self._user_pages = set()
        self._last_tab_close_task = None  # debounce

        # HAR
        self.har_path = None

        # Optional external hook
        self._on_shutdown = None

    # --------- Register optional shutdown callback ----------
    def set_shutdown_callback(self, fn):
        """Register a callable (no args) to run on proactive shutdown or close()."""
        self._on_shutdown = fn

    def _fire_shutdown_callback(self):
        try:
            if callable(self._on_shutdown):
                self._on_shutdown()
        except Exception as e:
            logger.debug("[HOOK] on_shutdown callback failed: %s", e)

    async def launch(self):
        """Launch stealth browser"""
        self.playwright = await async_playwright().start()

        VIDEO_TASK_PATH = get_tasks_video_path()
        TaskManager().set_last_task_path(VIDEO_TASK_PATH)

        # Ephemeral context (always) + native HAR + video
        self.context = await self.open_browser_context(VIDEO_TASK_PATH)

        # Context close hooks
        try:
            self.context.on("close", lambda: self._mark_ctx_closed())
        except Exception:
            pass

        # Offline capture
        await self.offline_capture.start(self.context)

        # Network listeners
        self.context.on("request", self.request_event.listen_for_request)
        self.context.on("response", self.response_event.listen_for_response)

        # Global bindings/scripts
        await self.setup_dom_listeners()

        # Reuse first page if one already exists
        if self.context.pages:
            self.page = self.context.pages[0]
        else:
            self.page = await self.context.new_page()

        # Track existing pages
        for p in self.context.pages:
            await self._track_user_page(p)

        # Track future pages
        self.context.on("page", lambda p: asyncio.create_task(self._on_context_page_created(p)))

        # Helpers
        actual_page = ActualPage()
        actual_page.set_page(self.page)
        self.page.on("console", lambda msg: print(f"🌐 Browser console: {msg.text}"))

        # Stealth script
        await self.apply_stealth_techniques()
        await self._initialize_page_event_script(self.page)

        return self.page

    def _mark_ctx_closed(self):
        self._ctx_closed = True
        logger.info("[CTX] BrowserContext closed event received")

    async def _track_user_page(self, page):
        # Register newly opened page
        try:
            await self.step_record.record_step(
                {
                    "event_info": {
                        "event_type": "tab_opened",
                        "event_context": "state:browser",
                        "event_data": {"url": page.url, "timestamp": page.main_frame.name},
                    },
                    "prefix_action": "state:browser",
                    "source_page": page,
                },
                omit_screenshot=True,
            )
        except Exception:
            pass

        self._user_pages.add(page)
        page.on("close", lambda: asyncio.create_task(self._on_user_page_closed(page)))

        # Attach listeners/scripts
        await self.page_event.attach_page(page)
        await self._initialize_page_event_script(page)

    async def _on_context_page_created(self, page):
        await self._track_user_page(page)

    async def _on_user_page_closed(self, page):
        if page in self._user_pages:
            self._user_pages.discard(page)

        try:
            if self.context and not self._ctx_closed and not self._shutting_down:
                remaining = list(self.context.pages)
                if len(remaining) == 0:
                    # Debounce to group near-simultaneous tab closes
                    if self._last_tab_close_task and not self._last_tab_close_task.done():
                        self._last_tab_close_task.cancel()

                    async def _debounced():
                        await asyncio.sleep(0.08)
                        await self._proactive_har_flush_once("Last user tab closed")

                    self._last_tab_close_task = asyncio.create_task(_debounced())
        except Exception as e:
            logger.debug("[PAGE CLOSE] handler error: %s", e)

    async def _proactive_har_flush_once(self, reason: str):
        async with self._shutdown_lock:
            if self._shutting_down:
                return
            self._shutting_down = True

            logger.info("[HAR] %s — proactively closing context to flush HAR", reason)

            # 1) Stop capture
            await self._stop_capture_once()

            # 2) Close context (Playwright flushes the HAR here)
            await self._close_context_once()

            # 3) Close browser
            await self._close_browser_once()

            # 4) Verify HAR file
            self._check_har_file()

            self._closed = True
            self._fire_shutdown_callback()

    async def apply_stealth_techniques(self):
        if self.page:
            await self.page.add_init_script(STEALTH_SCRIPT)

    async def setup_dom_listeners(self):
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
            try:
                async def _page_binding(source, event_info):
                    p = getattr(source, "page", None)
                    await self.page_event_handler(event_info, p)
                await page.expose_binding("onPageEvent", _page_binding)
            except Exception:
                pass

            await page.evaluate(PAGE_EVENT_LISTENER_SCRIPT)

            try:
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
        try:
            event_type = event_info.get("event_type", "unknown")
            event_context = event_info.get("event_context", "unknown")
            logger.debug(f"[PAGE_EVENT] Received: {event_context}:{event_type}")

            step_record = Recorder()
            await step_record.record_step(
                {"event_info": event_info, "prefix_action": f"{event_context}", "source_page": page}
            )
        except Exception as e:
            logger.error(f"[PAGE_EVENT] Error handling event: {e}", exc_info=True)

    # ---------- Idempotent shutdown helpers -----------

    async def _stop_capture_once(self):
        if self._capture_stopped:
            return
        try:
            await self.offline_capture.stop()
        except Exception as exc:
            logger.info("[CAPTURE] storage_state unavailable or capture stop failed: %s", exc)
        finally:
            self._capture_stopped = True

    async def _close_context_once(self):
        if self._har_attempted:
            return
        try:
            if self.context and not self._ctx_closed:
                # Close any remaining pages (safety net)
                for p in list(self.context.pages):
                    try:
                        await p.close()
                    except Exception:
                        pass
                await self.context.close()  # Playwright writes the HAR here
        except Exception as exc:
            logger.error("[CONTEXT] Error closing context: %s", exc)
        finally:
            self._har_attempted = True
            self._ctx_closed = True

    async def _close_browser_once(self):
        if self._browser_closed:
            return
        try:
            if self.browser:
                await self.browser.close()
        except Exception as exc:
            logger.error("[BROWSER] Error closing browser: %s", exc)
        finally:
            self._browser_closed = True

    def _check_har_file(self):
        try:
            if self.har_path and os.path.exists(self.har_path):
                logger.info("[HAR] Saved at %s", os.path.abspath(self.har_path))
            else:
                logger.warning("[HAR] Not found at %s.", self.har_path)
        except Exception:
            pass

    # ---------- Public close() API -----------

    async def close(self):
        if self._last_tab_close_task and not self._last_tab_close_task.done():
            self._last_tab_close_task.cancel()

        async with self._shutdown_lock:
            if self._closed:
                return
            self._shutting_down = True

            await self._stop_capture_once()
            await self._close_context_once()
            await self._close_browser_once()
            self._check_har_file()
            self._closed = True
            self._fire_shutdown_callback()

    # ---------- Ephemeral context creation ----------

    async def open_browser_context(self, video_task_path: str):
        """Create a fresh context with video/HAR recording enabled."""
        # --- 1) Resolve HAR path robustly ---
        try:
            env_har = os.environ.get("RECORDER_HAR_PATH")
            if env_har:
                self.har_path = os.path.abspath(os.path.normpath(env_har))
            else:
                # Use task id when available; otherwise fall back to timestamp
                tid = None
                try:
                    tm = TaskManager.get_instance()
                    task = getattr(tm, "get_actual_task", lambda: None)()
                    tid = getattr(task, "id", None)
                except Exception:
                    tid = None

                if not tid:
                    import time
                    tid = f"ts_{int(time.time())}"

                self.har_path = os.path.abspath(
                    os.path.join(DATA_DIR, "captures", f"task_{tid}", "requests.har")
                )

            # Ensure the destination directory exists
            har_dir = os.path.dirname(self.har_path)
            if har_dir and not os.path.exists(har_dir):
                os.makedirs(har_dir, exist_ok=True)
        except Exception as e:
            logger.error("[HAR] Error resolving har_path: %s", e)
            # Last-resort fallback so it is never None
            self.har_path = os.path.abspath(os.path.join(os.getcwd(), "requests.har"))
            try:
                os.makedirs(os.path.dirname(self.har_path), exist_ok=True)
            except Exception:
                pass

        # --- 2) Launch Chromium and create the context ---
        try:
            self.browser = await self.playwright.chromium.launch(
                headless=False,
                args=BROWSER_ARGS,
            )
            # Only pass video options when the path is provided
            new_context_kwargs = dict(
                **CONTEXT_CONFIG,
                bypass_csp=True,
                record_har_path=self.har_path,
                record_har_content="embed",   # use "omit" if you prefer smaller HARs
                record_har_mode="minimal",    # switch to "full" for exhaustive capture
                service_workers="block",
            )
            if video_task_path:
                new_context_kwargs.update(
                    record_video_dir=video_task_path,
                    record_video_size={"width": 1280, "height": 720},
                )

            self.context = await self.browser.new_context(**new_context_kwargs)
            logger.info("[HAR] Recording (ephemeral) to %s", self.har_path)
            return self.context

        except Exception as e:
            logger.error("[LAUNCH_BROWSER] Error launching browser/new_context: %s", e)
            # Attempt a best-effort cleanup to avoid dangling resources on Windows
            try:
                if self.browser:
                    await self.browser.close()
            except Exception:
                pass
            try:
                if self.playwright:
                    await self.playwright.stop()
            except Exception:
                pass
            raise e


    async def manual_browser_close(self):
        logger.info("Browser closed manually")
        TaskManager().end_actual_task()
        last_task_path = TaskManager().get_last_task_path()
        logger.info(f"Last task path: {last_task_path}")
        await self.close()
        sys.exit(0)
