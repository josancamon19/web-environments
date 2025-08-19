import os
import json
import threading
import time
from datetime import datetime
from urllib.parse import urlparse, parse_qsl

from playwright.sync_api import sync_playwright

from database import Database


DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
SCREENSHOTS_DIR = os.path.join(DATA_DIR, "screenshots")
DB_PATH = os.path.join(DATA_DIR, "tasks.db")


def ensure_data_dirs():
    os.makedirs(SCREENSHOTS_DIR, exist_ok=True)


def iso_now() -> str:
    return datetime.utcnow().isoformat(timespec="milliseconds") + "Z"


def build_event_injection_script() -> str:
    # Minimal client-side recorder for user actions. Throttles scroll events.
    return r"""
(() => {
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
      window.__record_event(type, JSON.stringify({ ts: Date.now(), ...payload }));
    } catch (e) {
      // ignore
    }
  }

  let scrollArmed = true;
  window.addEventListener('scroll', () => {
    if (!scrollArmed) return;
    scrollArmed = false;
    send('scroll', { x: window.scrollX, y: window.scrollY });
    setTimeout(() => { scrollArmed = true; }, 500);
  }, { capture: true, passive: true });

  window.addEventListener('click', (e) => {
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

  window.addEventListener('contextmenu', (e) => {
    const selector = safeSelector(e.target);
    send('contextmenu', { x: e.clientX, y: e.clientY, selector });
  }, { capture: true });

  window.addEventListener('input', (e) => {
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

  window.addEventListener('keydown', (e) => {
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
        self.request_counter = 0
        self.request_map = {}
        self._db_lock = threading.Lock()

        self.playwright = sync_playwright().start()
        self.context = self._create_context()
        self.browser = getattr(self.context, "browser", None)
        self.page = self.context.new_page()

        # Bindings and scripts
        # Expose for all pages in context
        self.context.expose_binding("__record_event", self._on_user_event)
        self.context.add_init_script(build_event_injection_script())
        self.context.add_init_script(build_stealth_script())

        # Network listeners (context-wide)
        self.context.on("request", self._on_request)
        self.context.on("response", self._on_response)

        # Lifecycle listeners
        self._attach_page(self.page)
        self.context.on("page", self._attach_page)

    def _screenshot_path(self, prefix: str) -> str:
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")
        filename = f"task{self.task_id}_{prefix}_{timestamp}.png"
        return os.path.join(SCREENSHOTS_DIR, filename)

    def _create_context(self):
        # Prefer system Chrome with a persistent user profile; reduce automation fingerprints
        preferred_channel = os.environ.get("RECORDER_BROWSER_CHANNEL", "chrome").strip() or None
        user_data_dir = os.environ.get("RECORDER_USER_DATA_DIR") or os.path.join(DATA_DIR, "user-data")
        args = ["--disable-blink-features=AutomationControlled"]
        ignore_default_args = ["--enable-automation", "--use-mock-keychain", "--password-store=basic"]

        last_error = None
        if preferred_channel:
            try:
                return self.playwright.chromium.launch_persistent_context(
                    user_data_dir=user_data_dir,
                    channel=preferred_channel,
                    headless=False,
                    args=args,
                    ignore_default_args=ignore_default_args,
                )
            except Exception as e:
                last_error = e
        try:
            return self.playwright.chromium.launch_persistent_context(
                user_data_dir=user_data_dir,
                headless=False,
                args=args,
                ignore_default_args=ignore_default_args,
            )
        except Exception as e:
            last_error = e
        # Final fallback: ephemeral context
        try:
            browser = self.playwright.chromium.launch(headless=False, args=args, ignore_default_args=ignore_default_args)
            return browser.new_context()
        except Exception:
            raise last_error

    def _attach_page(self, page):
        try:
            page.on("domcontentloaded", lambda: self._record_state_change("domcontentloaded", {"url": page.url}))
            page.on("load", lambda: self._record_state_change("load", {"url": page.url}))
            page.on("framenavigated", lambda frame: self._record_state_change("framenavigated", {"url": frame.url}))
        except Exception:
            pass

    def _capture_dom_and_screenshot(self, prefix: str):
        dom_html = ""
        screenshot_path = None
        try:
            dom_html = self.page.content()
        except Exception:
            pass
        try:
            screenshot_path = self._screenshot_path(prefix)
            self.page.screenshot(path=screenshot_path, full_page=True)
        except Exception:
            screenshot_path = None
        return dom_html, screenshot_path

    def _record_step(self, event_type: str, event_data: dict, prefix: str) -> int:
        dom_html, screenshot_path = self._capture_dom_and_screenshot(prefix)
        with self._db_lock:
            step_id = self.db.insert_step(
                task_id=self.task_id,
                timestamp=iso_now(),
                event_type=event_type,
                event_data=json.dumps(event_data, ensure_ascii=False),
                dom_snapshot=dom_html,
                screenshot_path=screenshot_path,
            )
        return step_id

    # User actions coming from page binding
    def _on_user_event(self, source, event_type: str, payload_json: str):
        try:
            payload = json.loads(payload_json) if isinstance(payload_json, str) else payload_json
        except Exception:
            payload = {"raw": str(payload_json)}
        self._record_step(event_type=f"action:{event_type}", event_data=payload, prefix=f"action_{event_type}")

    # Network request/response
    def _on_request(self, request):
        try:
            if request.resource_type not in ("xhr", "fetch"):
                return
        except Exception:
            return

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
        parsed = urlparse(url)
        query_params = dict(parse_qsl(parsed.query))

        # Capture cookies at time of request
        cookies_json = []
        try:
            cookies_json = self.context.cookies()
        except Exception:
            cookies_json = []

        event_data = {
            "uid": request_uid,
            "url": url,
            "method": request.method,
            "resource_type": request.resource_type,
            "headers": headers,
            "query": query_params,
            "post_data": post_data,
            "cookies": cookies_json,
        }

        step_id = self._record_step(event_type="request", event_data=event_data, prefix="request")

        with self._db_lock:
            request_id = self.db.insert_request(
                task_id=self.task_id,
                step_id=step_id,
                request_uid=request_uid,
                url=url,
                method=request.method,
                headers=json.dumps(headers, ensure_ascii=False),
                post_data=post_data,
                cookies=json.dumps(cookies_json, ensure_ascii=False),
                timestamp=iso_now(),
            )
            self.request_map[request] = request_id

    def _on_response(self, response):
        try:
            req = response.request
            if req.resource_type not in ("xhr", "fetch"):
                return
        except Exception:
            return

        request_id = self.request_map.get(req)

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

        try:
            from_sw = getattr(response, "from_service_worker", False)
        except Exception:
            from_sw = False

        event_data = {
            "url": req.url,
            "status": status,
            "headers": headers,
            "from_service_worker": bool(from_sw),
        }

        step_id = self._record_step(event_type="response", event_data=event_data, prefix="response")

        with self._db_lock:
            self.db.insert_response(
                task_id=self.task_id,
                request_id=request_id,
                status=status,
                headers=json.dumps(headers, ensure_ascii=False),
                body=body_bytes,
                timestamp=iso_now(),
            )

    def _record_state_change(self, name: str, details: dict):
        self._record_step(event_type=f"state:{name}", event_data=details, prefix=name)

    def run(self):
        print("\nTask Recorder - Minimal MVP")
        print("- A Chromium browser will open. Interact to perform the task.")
        print('- When finished, return to this terminal and type "save" then Enter.')
        print('- Optional: type "shot" anytime to force a screenshot step.')

        # Navigate after scripts are ready
        self.page.goto("about:blank")

        # Input thread for commands
        def stdin_listener():
            while not self.stop_event.is_set():
                try:
                    cmd = input().strip().lower()
                except EOFError:
                    break
                if cmd == "save":
                    try:
                        confirm = input("Confirm save and close? (y/N): ").strip().lower()
                    except EOFError:
                        confirm = "n"
                    if confirm in ("y", "yes"): 
                        self.stop_event.set()
                        break
                    else:
                        print("Canceled. Continue recording. Type 'save' to end.")
                if cmd == "shot":
                    self._record_step(event_type="manual:screenshot", event_data={"reason": "manual"}, prefix="manual")
                if cmd in ("quit", "exit"):
                    print("Use 'save' to end the task so it is persisted.")

        t = threading.Thread(target=stdin_listener, daemon=True)
        t.start()

        # Wait until save
        self.stop_event.wait()
        self.shutdown()

    def shutdown(self):
        try:
            with self._db_lock:
                self.db.end_task(self.task_id)
        finally:
            try:
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
        print(f"Saved task #{self.task_id} to {DB_PATH}")


def main():
    print("Enter a short description for this task (e.g., 'Buy me a coffee in DoorDash'):")
    description = input("> ").strip()
    if not description:
        description = f"Task started {iso_now()}"

    recorder = TaskRecorder(description)
    recorder.run()


if __name__ == "__main__":
    main()


