import argparse
import asyncio
import json
import logging
import os
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple

from playwright.async_api import Browser, BrowserContext, Route

from source_data.database import Database


logger = logging.getLogger(__name__)


@dataclass
class StepEntry:
    id: int
    event_type: str
    event_data: Dict[str, Any]
    timestamp: Optional[str] = None


class ReplayBundle:
    """Replay previously captured browsing resources."""

    def __init__(self, bundle_path: Path, log_dir: Optional[Path] = None):
        bundle_path = bundle_path.expanduser().resolve()

        if bundle_path.is_file():
            if bundle_path.name == "manifest.json":
                bundle_path = bundle_path.parent
            else:
                raise FileNotFoundError(
                    f"Bundle path points to unexpected file: {bundle_path}"
                )

        manifest_path = self._resolve_manifest(bundle_path)

        if not manifest_path.exists():
            raise FileNotFoundError(f"No manifest found at {manifest_path}")

        self.bundle_path = manifest_path.parent
        self.manifest_path = manifest_path

        self.manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        self.resources = self.manifest.get("resources", [])
        self.environment = self.manifest.get("environment", {})
        self.task_info: Dict[str, Any] = self.manifest.get("task") or {}
        self.task_id: Optional[int] = self.task_info.get("id")
        self._payloads: Dict[Tuple[str, str, str], list[Dict[str, Any]]] = defaultdict(
            list
        )
        self._payload_indices: Dict[Tuple[str, str, str], int] = defaultdict(int)

        # Set up logging for cached vs not-found URLs
        self.log_dir = log_dir
        self._cached_urls: set[str] = set()
        self._not_found_urls: set[str] = set()

        for resource in self.resources:
            key = self._resource_key(resource)
            self._payloads[key].append(resource)

        logger.info(
            "Loaded bundle %s with %s recorded resources",
            bundle_path,
            len(self.resources),
        )

    def load_steps(self) -> list[StepEntry]:
        if not self.task_id:
            logger.warning("Bundle manifest does not include a task id; skipping step replay")
            return []

        db = Database.get_instance()
        conn = db.get_connection()
        if conn is None:
            logger.error("Database connection unavailable; cannot load steps for task %s", self.task_id)
            return []

        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, event_type, event_data, timestamp FROM steps WHERE task_id = ? ORDER BY id",
            (self.task_id,),
        )

        steps: list[StepEntry] = []
        for row in cursor.fetchall():
            raw_event_data = row[2]
            parsed_event: Dict[str, Any] = {}
            if raw_event_data:
                try:
                    parsed_event = json.loads(raw_event_data)
                except json.JSONDecodeError:
                    logger.debug("Failed to decode event data for step %s", row[0])
            steps.append(
                StepEntry(
                    id=row[0],
                    event_type=row[1] or "",
                    event_data=parsed_event,
                    timestamp=row[3],
                )
            )

        logger.info("Loaded %d steps from database for task %s", len(steps), self.task_id)
        return steps

    def guess_start_url(self) -> Optional[str]:
        for resource in self.resources:
            if (
                resource.get("resource_type") == "document"
                and resource.get("status", 200) < 400
            ):
                return resource.get("url")
        return None

    async def build_context(
        self,
        browser: Browser,
        *,
        allow_network_fallback: bool = False,
    ) -> BrowserContext:
        context_config = dict(self.environment.get("context_config") or {})
        storage_state_path = self._storage_state_path()

        if storage_state_path:
            context_config["storage_state"] = str(storage_state_path)

        context = await browser.new_context(**context_config)
        await self.attach(context, allow_network_fallback=allow_network_fallback)
        return context

    async def attach(
        self,
        context: BrowserContext,
        *,
        allow_network_fallback: bool = False,
    ) -> None:
        async def _handler(route: Route):
            await self._fulfill(route, allow_network_fallback=allow_network_fallback)

        await context.route("**/*", _handler)

    async def _fulfill(self, route: Route, *, allow_network_fallback: bool) -> None:
        request = route.request
        post_data = await self._safe_post_data(request)
        key = (request.method, request.url, post_data or "")

        entries = self._payloads.get(key)
        payload: Optional[Dict[str, Any]] = None

        if entries:
            idx = self._payload_indices[key]
            if idx < len(entries):
                payload = entries[idx]
                self._payload_indices[key] = idx + 1
            elif request.method.upper() == "GET":
                payload = entries[-1]
                logger.debug(
                    "Reusing cached GET response for %s (recorded %d uses)",
                    request.url,
                    len(entries),
                )
            else:
                payload = entries[-1]
                logger.info(
                    "Reusing last response for %s %s beyond recorded count",
                    request.method,
                    request.url,
                )

        if payload:
            # Log cached URL
            if self.log_dir and request.url not in self._cached_urls:
                self._cached_urls.add(request.url)

            body_bytes = self._load_body(payload)
            headers = dict(payload.get("response_headers") or {})
            if body_bytes is not None:
                has_length = any(k.lower() == "content-length" for k in headers)
                if not has_length:
                    headers["content-length"] = str(len(body_bytes))

            status = payload.get("status") or 200
            await route.fulfill(status=status, headers=headers, body=body_bytes)
            return

        # Log not-found URL
        if self.log_dir and request.url not in self._not_found_urls:
            self._not_found_urls.add(request.url)

        if allow_network_fallback:
            await route.continue_()
            return

        message = f"Offline bundle missing resource for {request.method} {request.url}"
        logger.warning(message)
        await route.fulfill(status=504, body=message)

    def flush_logs(self) -> None:
        """Write cached and not-found URLs to log files."""
        if not self.log_dir:
            return

        self.log_dir.mkdir(parents=True, exist_ok=True)

        if self._cached_urls:
            cached_log_path = self.log_dir / "cached.log"
            with open(cached_log_path, "w", encoding="utf-8") as f:
                for url in sorted(self._cached_urls):
                    f.write(f"{url}\n")
            logger.info(
                "Wrote %d cached URLs to %s", len(self._cached_urls), cached_log_path
            )

        if self._not_found_urls:
            not_found_log_path = self.log_dir / "not-found.log"
            with open(not_found_log_path, "w", encoding="utf-8") as f:
                for url in sorted(self._not_found_urls):
                    f.write(f"{url}\n")
            logger.info(
                "Wrote %d not-found URLs to %s",
                len(self._not_found_urls),
                not_found_log_path,
            )

    def _load_body(self, payload: Dict[str, Any]) -> Optional[bytes]:
        body_path = payload.get("body_path")
        if not body_path:
            size = payload.get("body_size")
            if size:
                logger.debug(
                    "Recorded size without body path for %s", payload.get("url")
                )
            return b"" if size == 0 else None

        target = self.bundle_path / body_path
        if not target.exists():
            logger.warning("Missing body file %s", target)
            return None

        return target.read_bytes()

    def _storage_state_path(self) -> Optional[Path]:
        storage_dir = self.bundle_path / "storage"
        storage_state = storage_dir / "storage_state.json"
        return storage_state if storage_state.exists() else None

    async def _safe_post_data(self, request) -> Optional[str]:
        accessor = getattr(request, "post_data", None)
        try:
            if callable(accessor):
                try:
                    return await accessor()
                except TypeError:
                    return accessor()
            return accessor
        except Exception:
            return None

    @staticmethod
    def _resource_key(resource: Dict[str, Any]) -> Tuple[str, str, str]:
        return (
            resource.get("method") or "GET",
            resource.get("url") or "",
            resource.get("post_data") or "",
        )

    @staticmethod
    def _resolve_manifest(bundle_path: Path) -> Path:
        manifest = bundle_path / "manifest.json"
        if manifest.exists():
            return manifest

        # If this is a resources/ folder, walk up
        if bundle_path.name == "resources":
            parent_manifest = bundle_path.parent / "manifest.json"
            if parent_manifest.exists():
                return parent_manifest
            bundle_path = bundle_path.parent

        # If this directory has timestamped subdirectories, pick the newest
        candidates = sorted(
            [p for p in bundle_path.iterdir() if p.is_dir()],
            reverse=True,
        )
        for candidate in candidates:
            manifest = candidate / "manifest.json"
            if manifest.exists():
                return manifest

        return manifest  # fall back to initial attempt for error reporting


class TaskStepExecutor:
    def __init__(self, steps: Sequence[StepEntry], *, is_human_trajectory: bool = False):
        self.steps = list(steps)
        self.is_human_trajectory = is_human_trajectory
        self._initial_navigation_done = False

    async def run(self, page) -> None:
        if page.url and page.url != "about:blank":
            self._initial_navigation_done = True

        for step in self.steps:
            try:
                await self._run_step(page, step)
            except Exception as exc:
                logger.warning(
                    "Failed to execute step %s (%s): %s",
                    step.id,
                    step.event_type,
                    exc,
                    exc_info=True,
                )
            await asyncio.sleep(0.2 if self.is_human_trajectory else 0.1)

    async def _run_step(self, page, step: StepEntry) -> None:
        category, subject, action = self._split_event_type(step.event_type)

        if category == "state":
            await self._handle_state_step(page, subject, action, step.event_data)
            return

        if category == "action" and subject == "user":
            await self._handle_user_action(page, action, step.event_data)

    async def _handle_state_step(self, page, subject: str, action: str, payload: Dict[str, Any]) -> None:
        if subject == "browser" and action == "navigated":
            url = payload.get("url") if isinstance(payload, dict) else None
            if not url or url == "about:blank":
                return
            if not self._initial_navigation_done or self._urls_differ(page.url, url):
                await self._safe_goto(page, url)
                self._initial_navigation_done = True
            return

        if subject == "page":
            if action in {"domcontentloaded", "domcontentload"}:
                await self._safe_wait_for_load(page, "domcontentloaded")
            elif action in {"loaded", "load"}:
                await self._safe_wait_for_load(page, "load")

    async def _handle_user_action(self, page, action: str, payload: Dict[str, Any]) -> None:
        if action == "click":
            await self._perform_pointer_click(page, payload)
            return
        if action == "hover":
            await self._perform_pointer_move(page, payload)
            return
        if action == "scroll":
            await self._perform_scroll(page, payload)
            return
        if action == "input":
            await self._perform_input(page, payload)
            return
        if action == "keydown":
            await self._perform_keydown(page, payload)
            return
        if action == "submit":
            await self._perform_submit(page, payload)

    async def _perform_pointer_click(self, page, payload: Dict[str, Any]) -> None:
        coords = self._extract_coordinates(payload)
        if coords is None:
            selector = self._build_selector(payload)
            if selector:
                await page.click(selector, timeout=5000)
            return
        x, y = coords
        await page.mouse.move(x, y)
        await page.mouse.click(x, y)

    async def _perform_pointer_move(self, page, payload: Dict[str, Any]) -> None:
        coords = self._extract_coordinates(payload)
        if coords is None:
            return
        x, y = coords
        await page.mouse.move(x, y)

    async def _perform_scroll(self, page, payload: Dict[str, Any]) -> None:
        x = payload.get("x")
        y = payload.get("y")
        if isinstance(x, (int, float)) and isinstance(y, (int, float)):
            await page.evaluate("(coords) => window.scrollTo(coords.x, coords.y)", {"x": x, "y": y})

    async def _perform_input(self, page, payload: Dict[str, Any]) -> None:
        value = payload.get("value") if isinstance(payload, dict) else None
        if value is None:
            return
        await page.evaluate(
            "(data) => {\n                const lookup = (root) => {\n                    if (!data) return null;\n                    if (data.id) {\n                        const el = root.getElementById(data.id);\n                        if (el) return el;\n                    }\n                    if (data.className) {\n                        const classSelector = data.className\n                            .split(/\\s+/)\n                            .filter(Boolean)\n                            .map(cls => '.' + CSS.escape(cls))\n                            .join('');\n                        if (classSelector) {\n                            const tag = (data.tag || '*').toLowerCase();\n                            const found = root.querySelector(tag + classSelector);\n                            if (found) return found;\n                        }\n                    }\n                    return null;\n                };\n                let target = lookup(document);\n                if (!target) target = document.activeElement;\n                if (!target) return false;\n                if ('value' in target) {\n                    target.value = data.value;\n                    target.dispatchEvent(new Event('input', { bubbles: true }));\n                    target.dispatchEvent(new Event('change', { bubbles: true }));\n                    return true;\n                }\n                return false;\n            }",
            payload,
        )

    async def _perform_keydown(self, page, payload: Dict[str, Any]) -> None:
        key = payload.get("key") if isinstance(payload, dict) else None
        if not key:
            return
        try:
            await page.keyboard.press(key)
        except Exception:
            await page.keyboard.type(key)

    async def _perform_submit(self, page, payload: Dict[str, Any]) -> None:
        await page.evaluate(
            "(data) => {\n                const lookup = (root) => {\n                    if (!data) return null;\n                    if (data.id) {\n                        const el = root.getElementById(data.id);\n                        if (el) return el;\n                    }\n                    if (data.className) {\n                        const classSelector = data.className\n                            .split(/\\s+/)\n                            .filter(Boolean)\n                            .map(cls => '.' + CSS.escape(cls))\n                            .join('');\n                        if (classSelector) {\n                            const tag = (data.tag || 'form').toLowerCase();\n                            const el = root.querySelector(tag + classSelector);\n                            if (el) return el;\n                        }\n                    }\n                    return null;\n                };\n                let form = lookup(document);\n                if (!form) {\n                    const active = document.activeElement;\n                    if (active && active.form) form = active.form;\n                }\n                if (!form) form = document.querySelector('form');\n                if (!form) return false;\n                if (typeof form.requestSubmit === 'function') {\n                    form.requestSubmit();\n                } else {\n                    form.submit();\n                }\n                return true;\n            }",
            payload,
        )

    async def _safe_goto(self, page, url: str) -> None:
        try:
            logger.info("Navigating to %s", url)
            await page.goto(url, wait_until="domcontentloaded")
        except Exception as exc:
            logger.warning("Failed to navigate to %s: %s", url, exc)

    async def _safe_wait_for_load(self, page, state: str) -> None:
        try:
            await page.wait_for_load_state(state, timeout=15000)
        except Exception as exc:
            logger.debug("Load wait for %s skipped: %s", state, exc)

    def _extract_coordinates(self, payload: Dict[str, Any]) -> Optional[Tuple[float, float]]:
        coords = payload.get("coordinates") if isinstance(payload, dict) else None
        if isinstance(coords, dict):
            for key in ("client", "page", "offset"):
                point = coords.get(key)
                if self._is_valid_point(point):
                    return float(point["x"]), float(point["y"])
            relative = coords.get("relative")
            viewport = coords.get("viewport") or payload.get("viewport")
            if (
                self._is_valid_point(relative)
                and isinstance(viewport, dict)
                and isinstance(viewport.get("width"), (int, float))
                and isinstance(viewport.get("height"), (int, float))
            ):
                return (
                    float(relative["x"]) * float(viewport["width"]),
                    float(relative["y"]) * float(viewport["height"]),
                )
        x = payload.get("x") if isinstance(payload, dict) else None
        y = payload.get("y") if isinstance(payload, dict) else None
        if isinstance(x, (int, float)) and isinstance(y, (int, float)):
            return float(x), float(y)
        rect = payload.get("elementRect") if isinstance(payload, dict) else None
        if isinstance(rect, dict):
            left = rect.get("left")
            top = rect.get("top")
            width = rect.get("width", 0)
            height = rect.get("height", 0)
            if isinstance(left, (int, float)) and isinstance(top, (int, float)):
                return float(left + width / 2), float(top + height / 2)
        return None

    def _build_selector(self, payload: Dict[str, Any]) -> Optional[str]:
        element_id = payload.get("id") if isinstance(payload, dict) else None
        if element_id:
            return f"#{self._css_escape(str(element_id))}"
        class_name = payload.get("className") if isinstance(payload, dict) else None
        tag = payload.get("tag") if isinstance(payload, dict) else None
        if class_name:
            classes = [self._css_escape(part) for part in str(class_name).split() if part]
            if classes:
                prefix = (tag or "*").lower() if tag else "*"
                return f"{prefix}{''.join('.' + cls for cls in classes)}"
        return None

    @staticmethod
    def _is_valid_point(point: Any) -> bool:
        return (
            isinstance(point, dict)
            and isinstance(point.get("x"), (int, float))
            and isinstance(point.get("y"), (int, float))
        )

    @staticmethod
    def _css_escape(value: str) -> str:
        return "".join(CSS_ESCAPE_MAP.get(ch, ch) for ch in value)

    @staticmethod
    def _split_event_type(event_type: str) -> Tuple[str, str, str]:
        parts = (event_type or "").split(":", 2)
        if len(parts) == 1:
            return parts[0], "", ""
        if len(parts) == 2:
            return parts[0], parts[1], ""
        return parts[0], parts[1], parts[2]

    @staticmethod
    def _urls_differ(current: Optional[str], target: str) -> bool:
        if not current:
            return True
        return current.rstrip("/") != target.rstrip("/")


CSS_ESCAPE_MAP = {
    "\n": "\\A ",
    "\r": "",
    "\f": "\\C ",
    "\t": " ",
    " ": " ",
    "\"": "\\\"",
    "'": "\\'",
    "#": "\\#",
    ":": "\\:",
}


async def _cli(
    bundle_path: Path,
    *,
    headless: bool,
    allow_fallback: bool,
    is_human_trajectory: bool,
) -> None:
    from playwright.async_api import async_playwright

    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

    bundle = ReplayBundle(bundle_path)
    steps = bundle.load_steps()

    async with async_playwright() as pw:
        launch_kwargs: Dict[str, Any] = {"headless": headless}
        channel = os.environ.get("REPLAY_BROWSER_CHANNEL") or os.environ.get(
            "RECORDER_BROWSER_CHANNEL"
        )
        if channel:
            launch_kwargs["channel"] = channel

        browser = await pw.chromium.launch(**launch_kwargs)
        context = await bundle.build_context(
            browser, allow_network_fallback=allow_fallback
        )
        page = await context.new_page()
        start_url = bundle.guess_start_url() or "about:blank"
        logger.info("Opening %s", start_url)
        await page.goto(start_url)
        if steps:
            executor = TaskStepExecutor(steps, is_human_trajectory=is_human_trajectory)
            await executor.run(page)
        await asyncio.Event().wait()


def main():
    parser = argparse.ArgumentParser(
        description="Replay a captured browser bundle offline"
    )
    parser.add_argument(
        "bundle", type=Path, help="Path to the capture bundle directory"
    )
    parser.add_argument(
        "--headless", action="store_true", help="Run browser in headless mode"
    )
    parser.add_argument(
        "--allow-network-fallback",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Allow requests missing from the bundle to hit the live network",
    )
    parser.add_argument(
        "--is-human-trajectory",
        action="store_true",
        help="Replay timing with human-like pacing",
    )

    args = parser.parse_args()
    asyncio.run(
        _cli(
            args.bundle.expanduser().resolve(),
            headless=args.headless,
            allow_fallback=args.allow_network_fallback,
            is_human_trajectory=args.is_human_trajectory,
        )
    )


if __name__ == "__main__":
    main()
