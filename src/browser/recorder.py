import base64
import json
import logging
from typing import Any, Dict, Optional, Tuple

from db.database import Database
from utils.get_iso_datetime import get_iso_datetime
from db.task import TaskManager
from browser.page import ActualPage
from db.step import StepManager
from db.models import StepModel
from config.storage import SCREENSHOTS_DIR, VIDEOS_DIR
import time
import os

logger = logging.getLogger(__name__)


def get_screenshot_path(task_id: int, event_name: str):
    timestamp = int(time.time() * 1000)
    task_dir = os.path.join(SCREENSHOTS_DIR, f"task_{task_id}")
    os.makedirs(task_dir, exist_ok=True)
    filename = f"{timestamp}_{event_name}.png"
    return os.path.join(task_dir, filename)


def get_video_path(task_id: int):
    task_dir = os.path.join(VIDEOS_DIR, f"task_{task_id}")
    os.makedirs(task_dir, exist_ok=True)
    return task_dir


class Recorder:
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
        self._cdp_session = None
        self._last_screenshot_time = 0  # Throttle screenshots
        self._last_screenshot_url = ""  # Prevent duplicate URL screenshots
        self._is_closing = False  # Flag to stop recording during shutdown

    async def stop_recording(self):
        """Stop recording events (used during browser shutdown)"""
        logger.info("[RECORDER] Recording stopped for shutdown")
        self._is_closing = True

        # Clean up CDP session to prevent hanging
        if self._cdp_session:
            try:
                await self._cdp_session.detach()
                logger.info("[RECORDER] CDP session detached")
            except Exception as e:
                logger.warning(f"[RECORDER] Error detaching CDP session: {e}")
            finally:
                self._cdp_session = None

    async def record_step(self, step_info: dict, omit_screenshot: bool = False):
        # Skip recording if we're in shutdown mode
        if self._is_closing:
            return

        try:
            timestamp = get_iso_datetime()
            actual_task = self.task_manager.get_current_task()

            if not actual_task:
                logger.error("[RECORD_STEP] No active task found")
                return

            event_info = dict(step_info.get("event_info", {}))
            event_type = event_info.get("event_type", "unknown")
            context = event_info.get("event_context", "unknown")

            logger.info(f"[RECORD_STEP] {context}:{event_type}")

            context_type_action = f"{context}:{event_type}"
            context_type_action_formatted = context_type_action.replace(":", "_")

            screenshot_path = get_screenshot_path(
                actual_task.id, context_type_action_formatted
            )

            # Get current page URL for filtering
            should_screenshot = self._should_take_screenshot(event_type)

            actual_screenshot_path = ""
            if not omit_screenshot and should_screenshot:
                try:
                    await self.take_screenshot(screenshot_path)
                    actual_screenshot_path = screenshot_path
                    logger.info(f"[SCREENSHOT] {context_type_action_formatted}")
                except Exception as e:
                    logger.error(f"[SCREENSHOT] Failed: {e}")

            # Extract event data safely
            event_data = self._normalize_event_data(event_info.get("event_data", {}))
            metadata = self._parse_metadata(event_info.get("metadata"))
            source_page = step_info.get("source_page")

            should_snapshot = self._should_capture_snapshot(context, event_type)
            dom_snapshot = ""
            snapshot_metadata: Dict[str, Any] = {}

            if should_snapshot:
                page_candidate = source_page or self._safe_get_page()
                (
                    dom_snapshot,
                    snapshot_metadata,
                ) = await self._build_accessibility_snapshot(
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

            # Get the step we just created from the database
            step_model = StepModel.get_by_id(step_id)
            self.step_manager.set_current_step(step_model)

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
        Skip screenshots for rapid/continuous events, tracking URLs, and about:blank.
        """
        # Events that should NOT trigger screenshots (high frequency events)
        skip_screenshot_events = {
            "keydown",
            "input",
            "scroll",
            "mousemove",
            "mousedown",
            "mouseup",
            "pointerdown",
            "pointerup",
            "pointermove",
            "hover",
            "tab_visibility_changed",  # Tab switching events
        }

        if event_type in skip_screenshot_events:
            return False

        # Only take screenshots for significant events
        important_events = {
            "click",
            "loaded",  # Only fully loaded pages
            "back",
        }

        # Throttle screenshots to prevent duplicates (max 1 per 500ms per URL)
        current_time = time.time()
        if current_time - self._last_screenshot_time < 0.5:
            return False

        # Check if this is an important event
        if event_type in important_events:
            self._last_screenshot_time = current_time
            return True

        return False

    async def _get_cdp_session(self):
        """Get or create a reusable CDP session for the current page."""
        page = self.actual_page.get_page()

        # Create new session if none exists or if page changed
        if self._cdp_session is None:
            self._cdp_session = await page.context.new_cdp_session(page)
            logger.debug("[SCREENSHOT] Created new CDP session")

        return self._cdp_session

    async def take_screenshot(self, screenshot_path: str):
        """Take a screenshot using CDP to avoid visual flicker from Playwright's method."""
        try:
            # Reuse CDP session to avoid overhead of creating new sessions
            cdp_session = await self._get_cdp_session()
            screenshot_data = await cdp_session.send(
                "Page.captureScreenshot",
                {
                    "format": "png",
                    "captureBeyondViewport": False,
                },
            )

            # Decode and save
            with open(screenshot_path, "wb") as f:
                f.write(base64.b64decode(screenshot_data["data"]))

        except Exception as e:
            logger.warning(f"[SCREENSHOT] CDP failed, using fallback: {e}")
            # Reset CDP session on error in case it became stale
            self._cdp_session = None

            # Fallback to regular screenshot if CDP fails
            try:
                page = self.actual_page.get_page()
                await page.screenshot(path=screenshot_path, full_page=False)
            except Exception as fallback_error:
                logger.error(f"[SCREENSHOT] Fallback also failed: {fallback_error}")
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

        def process_node(
            node: Dict[str, Any], indent: int = 1, path: Optional[list] = None
        ):
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
                    node_description.append(f"[{key}={self._quote_text(clean_value)}]")

            tag = node.get("tag")
            if tag:
                attributes.append(("tag", tag))
                node_description.append(f"[tag={tag}]")

            class_name = self._clean_text(node.get("className"))
            if class_name:
                attributes.append(("className", class_name))
                node_description.append(f"[class={self._quote_text(class_name)}]")

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
                yaml_lines.append(f"  {key}: {self._format_yaml_scalar(value)}")
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
        escaped = value.replace('"', '\\"')
        return f'"{escaped}"'
