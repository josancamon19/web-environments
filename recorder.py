import os
import json
import threading
import time
import logging
from datetime import datetime
from urllib.parse import urlparse, parse_qsl
import sys

from playwright.sync_api import sync_playwright

from source_data.database import Database

class ImmediateFlushStreamHandler(logging.StreamHandler):
    def emit(self, record):
        super().emit(record)
        sys.stdout.flush()  # Forzar flush automático después de cada log

# Configuración básica del logger
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] - %(message)s",
    handlers=[ImmediateFlushStreamHandler(sys.stdout),  logging.FileHandler("recorder.log")]  # Usar el handler personalizado
)
logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
SCREENSHOTS_DIR = os.path.join(DATA_DIR, "screenshots")
VIDEOS_DIR = os.path.join(DATA_DIR, "videos")
DB_PATH = os.path.join(DATA_DIR, "tasks.db")


def ensure_data_dirs():
    os.makedirs(SCREENSHOTS_DIR, exist_ok=True)
    os.makedirs(VIDEOS_DIR, exist_ok=True)


def iso_now() -> str:
    return datetime.utcnow().isoformat(timespec="milliseconds") + "Z"


def build_event_injection_script() -> str:
    # Minimal client-side recorder for user actions. Throttles scroll events.
    return r"""
(() => {
  console.log('[RECORDER] Event injection script loaded');
  function safeSelector(el) {
    if (!el || !el.nodeType || el.nodeType !== 1) return null;
    if (el.id) return '#' + CSS.escape(el.id);
    const parts = [];
    let node = el;
    let depth = 0;
    while (node && depth < 5) {
      let part = node.nodeName.toLowerCase();
      if (node.classList && node.classList.length) {
        part += '.' + Array.from(node.classList).slice(0, 3).map(c => CSS.escape(c)).join('.');
      }
      parts.unshift(part);
      node = node.parentElement;
      depth++;
    }
    return parts.join('>');
  }

  function send(type, payload) {
    try {
      console.log('[RECORDER] Sending event:', type, payload);
      window.__record_event(type, JSON.stringify({ ts: Date.now(), ...payload, dom: document.documentElement.outerHTML }));
    } catch (e) {
      console.error('[RECORDER] Failed to send event:', e);
    }
  }

  let scrollArmed = true;
  window.addEventListener('scroll', () => {
    if (!scrollArmed) return;
    scrollArmed = false;
    send('scroll', { x: window.scrollX, y: window.scrollY });
    setTimeout(() => { scrollArmed = true; }, 500);
  }, { capture: true, passive: true });

  // Prefer document-level listeners for robust bubbling
  document.addEventListener('click', (e) => {
    const target = e.target;
    const selector = safeSelector(target);
    send('click', {
      x: e.clientX,
      y: e.clientY,
      button: e.button,
      selector,
      text: (target && 'innerText' in target) ? (target.innerText || '') : ''
    });
  }, { capture: true });

  document.addEventListener('mousedown', (e) => {
    const selector = safeSelector(e.target);
    send('mousedown', { x: e.clientX, y: e.clientY, button: e.button, selector });
  }, { capture: true });

  document.addEventListener('mouseup', (e) => {
    const selector = safeSelector(e.target);
    send('mouseup', { x: e.clientX, y: e.clientY, button: e.button, selector });
  }, { capture: true });

  document.addEventListener('pointerdown', (e) => {
    const selector = safeSelector(e.target);
    send('pointerdown', { x: e.clientX, y: e.clientY, button: e.button, selector, pointerType: e.pointerType });
  }, { capture: true });

  document.addEventListener('pointerup', (e) => {
    const selector = safeSelector(e.target);
    send('pointerup', { x: e.clientX, y: e.clientY, button: e.button, selector, pointerType: e.pointerType });
  }, { capture: true });

  document.addEventListener('contextmenu', (e) => {
    const selector = safeSelector(e.target);
    send('contextmenu', { x: e.clientX, y: e.clientY, selector });
  }, { capture: true });

  document.addEventListener('input', (e) => {
    try {
      const target = e.target;
      const selector = safeSelector(target);
      let value = null;
      if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA')) {
        value = target.value;
      }
      send('input', { selector, value });
    } catch (_) {}
  }, { capture: true });

  document.addEventListener('keydown', (e) => {
    send('keydown', { key: e.key, code: e.code, ctrl: e.ctrlKey, meta: e.metaKey, alt: e.altKey, shift: e.shiftKey });
  }, { capture: true });
})();
"""


def build_stealth_script() -> str:
    # Minimal stealth tweaks: navigator.webdriver false, plugins, languages, permissions
    return r"""
(() => {
  try {
    Object.defineProperty(navigator, 'webdriver', { get: () => false });
  } catch {}
  try {
    // Fake plugins
    Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3] });
  } catch {}
  try {
    Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
  } catch {}
  try {
    const getParameter = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function(param) {
      if (param === 37445) return 'Intel Inc.'; // UNMASKED_VENDOR_WEBGL
      if (param === 37446) return 'Apple M1';   // UNMASKED_RENDERER_WEBGL
      return getParameter.call(this, param);
    };
  } catch {}
})();
"""


class TaskRecorder:
    def __init__(self, task_description: str):
        ensure_data_dirs()
        self.db = Database(DB_PATH)
        self.task_id = self.db.start_task(task_description)
        self.stop_event = threading.Event()
        self._shutting_down = False
        self.request_counter = 0
        self.request_map = {}
        self._db_lock = threading.Lock()
        self._page_event_handlers = {}
        self._last_action_step_id = None  # Track last user action to link requests

        self.playwright = sync_playwright().start()
        self.context = self._create_context()
        self.browser = getattr(self.context, "browser", None)

        # Bindings and scripts
        # Expose for all pages in context
        logger.info("Exposing __record_event binding")
        self.context.expose_binding("__record_event", self._on_user_event)
        logger.info("Adding event injection script")
        self.context.add_init_script(build_event_injection_script())
        logger.info("Adding stealth script")
        self.context.add_init_script(build_stealth_script())

        # Network listeners (context-wide)
        self.context.on("request", self._on_request)
        self.context.on("response", self._on_response)

        # Lifecycle listeners
        self.page = self.context.new_page()
        self.page.on("console", self.handle_console_message)
        self._attach_page(self.page)
        self.context.on("page", self._attach_page)

    def handle_console_message(self, message):
        logger.debug(f"Console message: {message.text}")

    def _screenshot_path(self, prefix: str) -> str:
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")
        filename = f"task{self.task_id}_{prefix}_{timestamp}.png"
        return os.path.join(SCREENSHOTS_DIR, filename)

    def _create_context(self):
        # Video recording settings
        video_path = os.path.join(VIDEOS_DIR, f"task_{self.task_id}")
        os.makedirs(video_path, exist_ok=True)
        
        # Prefer system Chrome with a persistent user profile; reduce automation fingerprints
        preferred_channel = (
            os.environ.get("RECORDER_BROWSER_CHANNEL", "chrome").strip() or None
        )
        user_data_dir = os.environ.get("RECORDER_USER_DATA_DIR") or os.path.join(
            DATA_DIR, "user-data"
        )
        args = ["--disable-blink-features=AutomationControlled"]
        ignore_default_args = [
            "--enable-automation",
            "--use-mock-keychain",
            "--password-store=basic",
        ]

        last_error = None
        if preferred_channel:
            try:
                return self.playwright.chromium.launch_persistent_context(
                    user_data_dir=user_data_dir,
                    channel=preferred_channel,
                    headless=False,
                    args=args,
                    ignore_default_args=ignore_default_args,
                    record_video_dir=video_path,
                    record_video_size={"width": 1280, "height": 720}
                )
            except Exception as e:
                last_error = e
        try:
            return self.playwright.chromium.launch_persistent_context(
                user_data_dir=user_data_dir,
                headless=False,
                args=args,
                ignore_default_args=ignore_default_args,
                record_video_dir=video_path,
                record_video_size={"width": 1280, "height": 720}
            )
        except Exception as e:
            last_error = e
        # Final fallback: ephemeral context
        try:
            browser = self.playwright.chromium.launch(
                headless=False, args=args, ignore_default_args=ignore_default_args
            )
            return browser.new_context(
                record_video_dir=video_path,
                record_video_size={"width": 1280, "height": 720}
            )
        except Exception:
            raise last_error

    def _attach_page(self, page):
        print("Attach page: ", page)
        # Treat the most recently seen page as the active page for screenshots/DOM
        self.page = page
        try:
            # Ensure binding exists on the page level as well (defensive)
            page.expose_binding("__record_event", self._on_user_event)

            # Bind page at definition time to avoid late-binding issues
            def _on_domcontentloaded(p=page):
                self._record_state_change("domcontentloaded", {"url": p.url}, page=p)

            def _on_load(p=page):
                # Record page load as a high-level event
                step_id = self._record_step(
                    event_type="page:loaded",
                    event_data={"url": p.url},
                    prefix="page_loaded",
                    page=p,
                    dom=p.content()
                )
                # This becomes the context for subsequent requests
                with self._db_lock:
                    self._last_action_step_id = step_id

            def _on_framenavigated(frame, p=page):
                if frame == p.main_frame:  # Only track main frame navigation
                    step_id = self._record_step(
                        event_type="page:navigated",
                        event_data={"url": frame.url},
                        prefix="page_navigated",
                        page=p,
                        dom=p.content()
                    )
                    with self._db_lock:
                        self._last_action_step_id = step_id

            handlers = [
                ("domcontentloaded", _on_domcontentloaded),
                ("load", _on_load),
                ("framenavigated", _on_framenavigated),
            ]
            self._page_event_handlers[page] = handlers
            for event_name, handler in handlers:
                page.on(event_name, handler)
        except Exception:
            pass

    def _detach_page_listeners(self, page):
        try:
            handlers = self._page_event_handlers.pop(page, [])
            for event_name, handler in handlers:
                try:
                    page.off(event_name, handler)
                except Exception:
                    pass
        except Exception:
            pass

    def _capture_dom_and_screenshot(self, prefix: str, page=None):
        # Skip capture if we're shutting down
        if self._shutting_down:
            logger.debug("Skipping DOM/screenshot during shutdown")
            return "", None
            
        # Capture from the provided page if available; otherwise fall back to the last active page
        active_page = page or self.page
        logger.info(f"Active page: {active_page.url}")
        dom_html = ""
        screenshot_path = None
        
        # Capture DOM
        try:
            if active_page and not active_page.is_closed():
                logger.debug(f"Capturing DOM from {active_page}")
                dom_html = active_page.content()
                logger.debug(f"DOM: {dom_html}")
                logger.debug(f"Captured {len(dom_html)} chars of DOM")
        except Exception as e:
            logger.error(f"Failed to capture DOM: {e}")
            dom_html = ""
        
        # Capture screenshot
        try:
            if active_page and not active_page.is_closed():
                screenshot_path = self._screenshot_path(prefix)
                try:
                    active_page.screenshot(path=screenshot_path, full_page=True)
                    logger.debug(f"Screenshot saved to {screenshot_path}")
                except Exception:
                    # Fallback to viewport-only screenshot if full page fails
                    try:
                        active_page.screenshot(path=screenshot_path, full_page=False)
                        logger.debug(f"Viewport screenshot saved to {screenshot_path}")
                    except:
                        screenshot_path = None
        except Exception as e:
            logger.error(f"Failed to capture screenshot: {e}")
            screenshot_path = None
        
        return dom_html, screenshot_path

    def _record_step(
        self, event_type: str, event_data: dict, prefix: str, page=None, dom=None
    ) -> int:
        timestamp = iso_now()
        logger.info(f"Recording {event_type} at {timestamp}")
        logger.info(f"Page: {page.url}")
        # Always capture DOM and screenshot for user events
        dom_html, screenshot_path = self._capture_dom_and_screenshot(
            prefix, page=page
        )
        with self._db_lock:
            step_id = self.db.insert_step(
                task_id=self.task_id,
                timestamp=timestamp,
                event_type=event_type,
                event_data=json.dumps(event_data, ensure_ascii=False),
                dom_snapshot=dom,
                # dom_snapshot=dom_html,
                screenshot_path=screenshot_path,
            )
            logger.info(f"Saved step {step_id} to database")
        return step_id

    # User actions coming from page binding
    def _on_user_event(self, source, event_type: str, payload_json: str, dom: str):
        logger.info(f"Hola entre evento, {repr(source)}")

        # Skip low-level mouse events - we only care about the high-level ones
        if event_type in ("pointerdown", "pointerup", "mousedown", "mouseup"):
            return  # Skip these, we'll capture 'click' instead
        
        logger.info(f"Received user event: {event_type}")
        
        # Always process user events - they're important!
        try:
            payload = (
                json.loads(payload_json)
                if isinstance(payload_json, str)
                else payload_json
            )
        except Exception:
            payload = {"raw": str(payload_json)}
        
        # Record against the originating page if available
        page = getattr(source, "page", None)
        logger.info(f"Recording user event: {event_type}")
        step_id = self._record_step(
            event_type=f"action:{event_type}",
            event_data=payload,
            prefix=f"action_{event_type}",
            page=page,
            dom=dom
        )
        
        # Remember this action for linking subsequent requests
        with self._db_lock:
            self._last_action_step_id = step_id

    # Network request/response
    def _on_request(self, request):
        logger.debug(f"Request: {request}")
        if self.stop_event.is_set() or self._shutting_down:
            return
        try:
            # Also record top-level navigation (document) requests
            if request.resource_type not in ("xhr", "fetch", "document"):
                return
        except Exception:
            return
        
        logger.info(f"Recording {request.method} {request.url[:50]}... triggered by step {self._last_action_step_id}")
        
        self.request_counter += 1
        request_uid = f"req_{self.request_counter}"

        headers = {}
        try:
            headers = request.headers
        except Exception:
            headers = {}

        post_data = None
        try:
            post_data = request.post_data
        except Exception:
            post_data = None

        url = request.url
        
        # Capture cookies at time of request
        cookies_json = []
        try:
            cookies_json = self.context.cookies()
        except Exception:
            cookies_json = []

        # Don't create a step - just insert into requests table
        with self._db_lock:
            request_id = self.db.insert_request(
                task_id=self.task_id,
                step_id=self._last_action_step_id,  # Link to the action that triggered this
                request_uid=request_uid,
                url=url,
                method=request.method,
                headers=json.dumps(headers, ensure_ascii=False),
                post_data=post_data,
                cookies=json.dumps(cookies_json, ensure_ascii=False),
                timestamp=iso_now(),
            )
            self.request_map[request] = request_id
            logger.debug(f"Saved request {request_id} to database")

    def _on_response(self, response):
        if self.stop_event.is_set() or self._shutting_down:
            return
        try:
            req = response.request
            if req.resource_type not in ("xhr", "fetch", "document"):
                return
        except Exception:
            return

        request_id = self.request_map.get(req)
        if not request_id:
            return  # No matching request found
        
        logger.info(f"Recording response for request {request_id}")

        headers = {}
        try:
            headers = response.headers
        except Exception:
            headers = {}

        body_bytes = None
        try:
            # Beware: large bodies. This is MVP; store as-is.
            body_bytes = response.body()
        except Exception:
            body_bytes = None

        status = None
        try:
            status = response.status
        except Exception:
            status = None

        # Don't create a step - just insert into responses table
        with self._db_lock:
            response_id = self.db.insert_response(
                task_id=self.task_id,
                request_id=request_id,
                status=status,
                headers=json.dumps(headers, ensure_ascii=False),
                body=body_bytes,
                timestamp=iso_now(),
            )
            logger.debug(f"Saved response {response_id} to database")

    def _record_state_change(self, name: str, details: dict, page=None, dom=None):
        if self.stop_event.is_set() or self._shutting_down:
            return
        self._record_step(
            event_type=f"state:{name}", event_data=details, prefix=name, page=page, dom=dom
        )

    def run(self):
        logger.info("Task Recorder - Starting")
        logger.info("A Chromium browser will open. Interact to perform the task.")
        logger.info('When finished, return to this terminal and type "stop" then Enter.')
        # Screenshots/DOM are captured automatically for every recorded step

        # First go to blank page to initialize
        self.page.goto("about:blank")
        
        # Create initial navigation step (this will be used for all initial requests)
        logger.info("Creating initial navigation step")
        initial_step_id = self._record_step(
            event_type="page:navigate_start",
            event_data={"url": "https://www.google.com", "initial": True},
            prefix="initial_navigation",
            page=self.page,
            dom=self.page.content()
        )
        with self._db_lock:
            self._last_action_step_id = initial_step_id
        
        # Now navigate to the actual page
        logger.info("Navigating to initial page")
        self.page.goto("https://www.google.com")

        # Input thread for commands
        def stdin_listener():
            while not self.stop_event.is_set():
                try:
                    cmd = input().strip().lower()
                except EOFError:
                    break
                if cmd in ("stop", "quit", "exit", "done"):
                    logger.info("Stopping recording...")
                    # Mark as shutting down to prevent hanging on DOM capture
                    # self._shutting_down = True
                    # Give browser time to send any final events
                    time.sleep(0.5)
                    # Then stop
                    self.stop_event.set()
                    break
                else:
                    logger.warning(f"Unknown command '{cmd}'. Type 'stop' to end recording.")

        t = threading.Thread(target=stdin_listener, daemon=True)
        t.start()

        # Wait until save
        self.stop_event.wait()
        self.shutdown()

    def _begin_shutdown(self):
        print("Begin shutdown")
        self._shutting_down = True
        # Detach context-level listeners
        try:
            self.context.off("request", self._on_request)
        except Exception:
            pass
        try:
            self.context.off("response", self._on_response)
        except Exception:
            pass
        try:
            self.context.off("page", self._attach_page)
        except Exception:
            pass
        # Detach page-level listeners
        try:
            for page in list(self._page_event_handlers.keys()):
                self._detach_page_listeners(page)
        except Exception:
            pass

    def shutdown(self):
        # Ensure listeners are removed and shutdown gating is on
        self._begin_shutdown()
        
        # Save video path
        video_files = []
        try:
            video_dir = os.path.join(VIDEOS_DIR, f"task_{self.task_id}")
            if os.path.exists(video_dir):
                video_files = [f for f in os.listdir(video_dir) if f.endswith('.webm')]
                if video_files:
                    logger.info(f"Video recording saved to {os.path.join(video_dir, video_files[0])}")
        except Exception as e:
            logger.error(f"Error checking video: {e}")
        
        try:
            with self._db_lock:
                self.db.end_task(self.task_id)
        finally:
            try:
                # Prefer closing the context if available to avoid browser hang
                if hasattr(self, "context") and self.context:
                    self.context.close()
                elif hasattr(self, "browser") and self.browser:
                    self.browser.close()
            except Exception:
                pass
            try:
                self.playwright.stop()
            except Exception:
                pass
            try:
                self.db.close()
            except Exception:
                pass
        logger.info(f"Saved task #{self.task_id} to {DB_PATH}")


def main():
    print("Enter a short description for this task (e.g., 'Buy me a coffee in DoorDash'):")
    description = input("> ").strip()
    if not description:
        description = f"Task started {iso_now()}"

    logger.info(f"Starting task: {description}")
    recorder = TaskRecorder(description)
    recorder.run()


if __name__ == "__main__":
    main()
