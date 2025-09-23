import json
import logging
from typing import Any, Dict, Optional, Tuple

from src.source_data.database import Database
from src.utils.get_iso_datetime import get_iso_datetime
from src.tasks.task import TaskManager
from src.page.actual_page import ActualPage
from src.steps.step import StepManager
from src.utils.get_screenshot_path import get_screenshot_path
from src.steps.step import Step

logger = logging.getLogger(__name__)


class StepRecord:
    _SNAPSHOT_TRIGGERS = {
        ("state:page", "load"),
        ("state:page", "domcontentloaded"),
        ("state:page", "loaded"),
        ("state:browser", "navigated"),
        ("state:browser", "navigate_start"),
        ("state:browser", "back"),
        ("action:user", "click"),
        ("action:user", "input"),
        ("action:user", "contextmenu"),
        ("action:user", "submit"),
    }
    _MAX_SNAPSHOT_NODES = 400

    def __init__(self):
        self.db = Database.get_instance()
        self.task_manager = TaskManager()
        self.actual_page = ActualPage()
        self.step_manager = StepManager()

    async def record_step(self, step_info: dict, omit_screenshot: bool = False):
        try:
            timestamp = get_iso_datetime()
            actual_task = self.task_manager.get_actual_task()

            if not actual_task:
                logger.error("[RECORD_STEP] No active task found")
                return

            event_info = dict(step_info.get("event_info", {}))
            event_type = event_info.get("event_type", "unknown")
            context = event_info.get("event_context", "unknown")

            logger.info(f"[RECORD_STEP] Recording: {context}:{event_type}")

            context_type_action = f"{context}:{event_type}"
            context_type_action_formatted = context_type_action.replace(":", "_")

            screenshot_path = get_screenshot_path(
                actual_task.id, context_type_action_formatted
            )

            # Determine if we should take a screenshot based on event type
            should_screenshot = self._should_take_screenshot(event_type)
            logger.debug(
                f"[RECORD_STEP] Event: {event_type}, Should screenshot: {should_screenshot}, Omit: {omit_screenshot}"
            )

            actual_screenshot_path = ""
            if not omit_screenshot and should_screenshot:
                logger.info(f"[RECORD_STEP] Taking screenshot for {event_type}")
                try:
                    await self.take_screenshot(screenshot_path)
                    actual_screenshot_path = screenshot_path
                    logger.info(f"[RECORD_STEP] Screenshot saved to {screenshot_path}")
                except Exception as e:
                    logger.error(f"[RECORD_STEP] Screenshot failed: {e}")

            # Extract event data safely
            event_data = self._normalize_event_data(event_info.get("event_data", {}))
            metadata = self._parse_metadata(event_info.get("metadata"))
            source_page = step_info.get("source_page")

            should_snapshot = self._should_capture_snapshot(context, event_type)
            dom_snapshot = ""
            snapshot_metadata: Dict[str, Any] = {}

            if should_snapshot:
                page_candidate = source_page or self._safe_get_page()
                dom_snapshot, snapshot_metadata = await self._build_accessibility_snapshot(
                    page_candidate,
                    context,
                    event_type,
                )

            dom_snapshot_metadata = {}
            if metadata:
                dom_snapshot_metadata["event_metadata"] = metadata
            if snapshot_metadata:
                dom_snapshot_metadata.update(snapshot_metadata)

            dom_snapshot_metadata_json = json.dumps(
                dom_snapshot_metadata if dom_snapshot_metadata else {},
                ensure_ascii=False,
            )

            event_data_json = json.dumps(event_data, ensure_ascii=False)

            # Save to database
            step_id = self.db.insert_step(
                task_id=actual_task.id,
                timestamp=timestamp,
                event_type=context_type_action,
                event_data=event_data_json,
                dom_snapshot=dom_snapshot,
                dom_snapshot_metadata=dom_snapshot_metadata_json,
                screenshot_path=actual_screenshot_path,
            )

            self.step_manager.set_actual_step(
                Step(
                    id=step_id,
                    task_id=actual_task.id,
                    timestamp=timestamp,
                    event_type=context_type_action,
                    event_data=event_data_json,
                    dom_snapshot=dom_snapshot,
                    dom_snapshot_metadata=dom_snapshot_metadata_json,
                    screenshot_path=actual_screenshot_path,
                )
            )

        except Exception as e:
            logger.error(f"[RECORD_STEP] Failed to record step: {e}", exc_info=True)

    def _normalize_event_data(self, data: Any) -> Dict[str, Any]:
        if isinstance(data, dict):
            return data
        if data is None:
            return {}
        return {"value": data}

    def _should_take_screenshot(self, event_type: str) -> bool:
        """
        Determine if a screenshot should be taken for this event type.
        Skip screenshots for rapid/continuous events to prevent browser jumping.
        """
        # Events that should NOT trigger screenshots (high frequency events)
        skip_screenshot_events = {
            "keydown",  # Individual key presses
            "input",  # Text input events
            "scroll",  # Scrolling events
            "mousemove",  # Mouse movement
            "mousedown",  # Mouse button down
            "mouseup",  # Mouse button up
            "pointerdown",  # Pointer down
            "pointerup",  # Pointer up
            "pointermove",  # Pointer movement
            "hover",  # Hover events (too frequent, would cause performance issues)
        }

        # Only take screenshots for significant events
        important_events = {
            "click",  # User clicks
            "load",  # Page load
            "navigate_start",  # Navigation start
            "navigated",  # Navigation complete
            "domcontentloaded",  # DOM ready
            "contextmenu",  # Right-click menu
            "loaded",  # Page fully loaded
            "back",  # Browser back/forward navigation
        }

        # Check if this is an event we should screenshot
        if event_type in important_events:
            return True
        elif event_type in skip_screenshot_events:
            return False
        else:
            # Default to false for unknown events to be safe
            return False

    async def take_screenshot(self, screenshot_path: str):
        """Take a screenshot - using regular method for stability."""
        try:
            logger.debug(f"[SCREENSHOT] Starting screenshot capture")
            page = self.actual_page.get_page()

            # For now, use the regular screenshot method for stability
            # We can optimize later once we identify the crash cause
            page.waitForTimeout(500)
            await page.screenshot(path=screenshot_path, full_page=False)
            logger.debug(f"[SCREENSHOT] Screenshot captured successfully")

        except Exception as e:
            logger.error(f"[SCREENSHOT] Failed to take screenshot: {e}")
            raise

    def _parse_metadata(self, metadata: Any) -> Dict[str, Any]:
        if not metadata:
            return {}
        if isinstance(metadata, dict):
            return metadata
        if isinstance(metadata, str):
            stripped = metadata.strip()
            if not stripped:
                return {}
            try:
                return json.loads(stripped)
            except json.JSONDecodeError:
                logger.debug("[RECORD_STEP] Failed to decode metadata JSON")
                return {"raw": stripped}
        return {}

    def _should_capture_snapshot(self, context: str, event_type: str) -> bool:
        context_key = (context or "").lower()
        event_key = (event_type or "").lower()
        return (context_key, event_key) in self._SNAPSHOT_TRIGGERS

    def _safe_get_page(self):
        try:
            return self.actual_page.get_page()
        except Exception:
            return None

    async def _build_accessibility_snapshot(
        self,
        page,
        context: str,
        event_type: str,
    ) -> Tuple[str, Dict[str, Any]]:
        if not page:
            return "", {}

        try:
            url = page.url
        except Exception:
            url = ""

        try:
            title = await page.title()
        except Exception:
            title = ""

        viewport = getattr(page, "viewport_size", None) or {}

        try:
            accessibility_snapshot = await page.accessibility.snapshot()
        except Exception as exc:
            logger.warning(
                "[RECORD_STEP] Failed to obtain accessibility snapshot: %s", exc
            )
            accessibility_snapshot = None

        yaml_lines = [
            f"url: {self._format_yaml_scalar(url)}",
            f"title: {self._format_yaml_scalar(title)}",
        ]

        if viewport:
            yaml_lines.append("viewport:")
            yaml_lines.append(
                f"  width: {self._format_yaml_scalar(viewport.get('width'))}"
            )
            yaml_lines.append(
                f"  height: {self._format_yaml_scalar(viewport.get('height'))}"
            )
        else:
            yaml_lines.append("viewport: null")

        yaml_lines.append("elements:")

        ref_counter = 1
        truncated = False

        def process_node(node: Dict[str, Any], indent: int = 1, path: Optional[list] = None):
            nonlocal ref_counter, truncated

            if not node:
                return

            children = node.get("children") or []
            role = node.get("role")

            if not role:
                for child in children:
                    process_node(child, indent, path)
                return

            if ref_counter > self._MAX_SNAPSHOT_NODES:
                if not truncated:
                    yaml_lines.append(f"{'  ' * indent}- [truncated]")
                    truncated = True
                return

            node_description = [role]
            attributes = [("role", role)]

            name = self._clean_text(node.get("name"))
            if name:
                node_description.append(self._quote_text(name))
                attributes.append(("name", name))

            for attr in ("value", "description", "placeholder"):
                value = self._clean_text(node.get(attr))
                if value:
                    attributes.append((attr, value))
                    node_description.append(f"[{attr}={self._quote_text(value)}]")

            for state in (
                "checked",
                "selected",
                "disabled",
                "required",
                "readonly",
                "expanded",
                "pressed",
                "busy",
            ):
                if node.get(state):
                    attributes.append((state, True))
                    node_description.append(f"[{state}]")

            for key, value in sorted(node.items()):
                if key.startswith("aria-") and value:
                    clean_value = self._clean_text(value)
                    attributes.append((key, clean_value))
                    node_description.append(
                        f"[{key}={self._quote_text(clean_value)}]"
                    )

            tag = node.get("tag")
            if tag:
                attributes.append(("tag", tag))
                node_description.append(f"[tag={tag}]")

            class_name = self._clean_text(node.get("className"))
            if class_name:
                attributes.append(("className", class_name))
                node_description.append(
                    f"[class={self._quote_text(class_name)}]"
                )

            ref_id = f"e{ref_counter}"
            ref_counter += 1
            attributes.append(("ref", ref_id))
            node_description.append(f"[ref={ref_id}]")

            current_path = (path or []) + [role]
            path_str = " > ".join(current_path)
            attributes.append(("path", path_str))

            is_interactive = role in {
                "button",
                "link",
                "textbox",
                "checkbox",
                "radio",
                "combobox",
                "listbox",
                "menuitem",
                "menuitemcheckbox",
                "menuitemradio",
                "option",
                "switch",
                "tab",
            }
            if is_interactive:
                attributes.append(("interactive", True))
                node_description.append("[interactive]")

            prefix = "  " * indent
            yaml_lines.append(f"{prefix}- {' '.join(node_description)}")

            attr_prefix = "  " * (indent + 1)
            for key, value in attributes:
                yaml_lines.append(
                    f"{attr_prefix}{key}: {self._format_yaml_scalar(value)}"
                )

            if children:
                yaml_lines.append(f"{attr_prefix}children:")
                for child in children:
                    process_node(child, indent + 2, current_path)

        if accessibility_snapshot:
            process_node(accessibility_snapshot)
        else:
            yaml_lines.append("  - No accessibility content available")

        focused_element = None
        try:
            focused_element = await page.evaluate(
                "() => { const el = document.activeElement; if (!el) return null; return { tagName: el.tagName, id: el.id, className: el.className }; }"
            )
        except Exception:
            focused_element = None

        text_word_count: Optional[int] = None
        try:
            text_word_count = await page.evaluate(
                "() => document.body ? document.body.innerText.split(/\\s+/).filter(Boolean).length : 0"
            )
        except Exception:
            text_word_count = None

        if focused_element:
            yaml_lines.append("focused_element:")
            for key, value in focused_element.items():
                yaml_lines.append(
                    f"  {key}: {self._format_yaml_scalar(value)}"
                )
        if text_word_count is not None:
            yaml_lines.append(
                f"text_word_count: {self._format_yaml_scalar(text_word_count)}"
            )

        snapshot_metadata = {
            "snapshot_type": "accessibility_tree",
            "page_url": url,
            "page_title": title,
            "viewport": viewport,
            "element_count": max(ref_counter - 1, 0),
            "truncated": truncated,
            "event_context": context,
            "event_type": event_type,
        }
        if focused_element:
            snapshot_metadata["focused_element"] = focused_element
        if text_word_count is not None:
            snapshot_metadata["text_word_count"] = text_word_count
        return "\n".join(yaml_lines), snapshot_metadata

    def _format_yaml_scalar(self, value: Any) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        if value is None:
            return "null"
        if isinstance(value, (int, float)):
            return str(value)
        return json.dumps(value, ensure_ascii=False)

    def _clean_text(self, value: Optional[str], max_length: int = 120) -> str:
        if not value:
            return ""
        normalized = " ".join(str(value).split())
        if len(normalized) > max_length:
            return normalized[: max_length - 3] + "..."
        return normalized

    def _quote_text(self, value: str) -> str:
        escaped = value.replace("\"", "\\\"")
        return f'"{escaped}"'
