import asyncio
import logging
from typing import Any, Dict, Optional, Tuple
from db.step import Step


logger = logging.getLogger(__name__)


class TaskStepExecutor:
    def __init__(self, trajectory: list[Step], *, run_human_trajectory: bool = False):
        self.trajectory = trajectory
        self.run_human_trajectory = run_human_trajectory
        self._initial_navigation_done = False

    async def run(self, page) -> None:
        if page.url and page.url != "about:blank":
            self._initial_navigation_done = True

        for step in self.trajectory:
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
            base_delay = 0.2 if self.run_human_trajectory else 0.1
            await asyncio.sleep(base_delay)

    async def _run_step(self, page, step: Step) -> None:
        category, subject, action = self._split_event_type(step.event_type)

        if category == "state":
            await self._handle_state_step(page, subject, action, step.event_data)
            return

        if category == "action" and subject == "user":
            await self._handle_user_action(page, action, step.event_data)

    async def _handle_state_step(
        self, page, subject: str, action: str, payload: Dict[str, Any]
    ) -> None:
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

    async def _handle_user_action(
        self, page, action: str, payload: Dict[str, Any]
    ) -> None:
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
            # logger.info("Scrolling to x=%s, y=%s", x, y)
            try:
                # Use instant scroll behavior and ensure we scroll both window and document
                await page.evaluate(
                    """(coords) => {
                        // Try multiple methods to ensure scroll happens
                        window.scrollTo({
                            left: coords.x,
                            top: coords.y,
                            behavior: 'instant'
                        });
                        // Fallback for older browsers
                        if (window.scrollX !== coords.x || window.scrollY !== coords.y) {
                            window.scrollTo(coords.x, coords.y);
                        }
                        // Also try scrolling document element directly
                        if (document.documentElement) {
                            document.documentElement.scrollLeft = coords.x;
                            document.documentElement.scrollTop = coords.y;
                        }
                    }""",
                    {"x": x, "y": y},
                )
            except Exception as exc:
                logger.warning("Failed to scroll to (%s, %s): %s", x, y, exc)
        else:
            logger.warning("Invalid scroll coordinates: x=%s, y=%s", x, y)

    async def _perform_input(self, page, payload: Dict[str, Any]) -> None:
        value = payload.get("value") if isinstance(payload, dict) else None
        if value is None:
            return
        await page.evaluate(
            """
            (data) => {
                const lookup = (root) => {
                    if (!data) return null;
                    if (data.id) {
                        const el = root.getElementById(data.id);
                        if (el) return el;
                    }
                    if (data.className) {
                        const classSelector = data.className
                            .split(/\\s+/)
                            .filter(Boolean)
                            .map(cls => '.' + CSS.escape(cls))
                            .join('');
                        if (classSelector) {
                            const tag = (data.tag || '*').toLowerCase();
                            const found = root.querySelector(tag + classSelector);
                            if (found) return found;
                        }
                    }
                    return null;
                };
                let target = lookup(document);
                if (!target) target = document.activeElement;
                if (!target) return false;
                if ('value' in target) {
                    target.value = data.value;
                    target.dispatchEvent(new Event('input', { bubbles: true }));
                    target.dispatchEvent(new Event('change', { bubbles: true }));
                    return true;
                }
                return false;
            }
            """,
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
            """
            (data) => {
                const lookup = (root) => {
                    if (!data) return null;
                    if (data.id) {
                        const el = root.getElementById(data.id);
                        if (el) return el;
                    }
                    if (data.className) {
                        const classSelector = data.className
                            .split(/\\s+/)
                            .filter(Boolean)
                            .map(cls => '.' + CSS.escape(cls))
                            .join('');
                        if (classSelector) {
                            const tag = (data.tag || 'form').toLowerCase();
                            const el = root.querySelector(tag + classSelector);
                            if (el) return el;
                        }
                    }
                    return null;
                };
                let form = lookup(document);
                if (!form) {
                    const active = document.activeElement;
                    if (active && active.form) form = active.form;
                }
                if (!form) form = document.querySelector('form');
                if (!form) return false;
                if (typeof form.requestSubmit === 'function') {
                    form.requestSubmit();
                } else {
                    form.submit();
                }
                return true;
            }
            """,
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

    def _extract_coordinates(
        self, payload: Dict[str, Any]
    ) -> Optional[Tuple[float, float]]:
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
            classes = [
                self._css_escape(part) for part in str(class_name).split() if part
            ]
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


# Escape characters for CSS selectors
CSS_ESCAPE_MAP = {
    "\n": "\\A ",
    "\r": "",
    "\f": "\\C ",
    "\t": " ",
    " ": " ",
    '"': '\\"',
    "'": "\\'",
    "#": "\\#",
    ":": "\\:",
}
