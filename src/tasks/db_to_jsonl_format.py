from enum import Enum
import sqlite3
import json
import urllib.parse
from dataclasses import dataclass
from typing import List, Dict, Any, Optional
from pathlib import Path
from html.parser import HTMLParser
import sys
import os


if "--prod" in sys.argv:
    DATA_DIR = os.path.join("data", "prod")
else:
    DATA_DIR = os.path.join("data", "dev")


class ToolCall(Enum):
    CLICK = "click"  # params (selector: str)
    TYPE = "type"  # params (selector: str, text: str)
    GO_TO = "go_to"  # params (url: str)


@dataclass
class ToolCallData:
    type: str
    params: Dict[str, Any]
    step_ids: List[int]

    def to_dict(self):
        return {"type": self.type, "params": self.params, "step_ids": self.step_ids}


class ElementExtractor(HTMLParser):
    """Extract element attributes from DOM HTML."""

    def __init__(self, target_id: str = None, target_classes: List[str] = None):
        super().__init__()
        self.target_id = target_id
        self.target_classes = set(target_classes) if target_classes else set()
        self.found_element = None
        self.current_attrs = {}

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        element_id = attrs_dict.get("id", "")
        element_classes = set(attrs_dict.get("class", "").split())

        # Check if this is our target element
        if (self.target_id and element_id == self.target_id) or (
            self.target_classes and self.target_classes.issubset(element_classes)
        ):
            self.found_element = {
                "tag": tag,
                "id": element_id,
                "class": attrs_dict.get("class", ""),
                "name": attrs_dict.get("name", ""),
                "type": attrs_dict.get("type", ""),
                "role": attrs_dict.get("role", ""),
                "aria-label": attrs_dict.get("aria-label", ""),
                "placeholder": attrs_dict.get("placeholder", ""),
                "title": attrs_dict.get("title", ""),
                "value": attrs_dict.get("value", ""),
                "href": attrs_dict.get("href", ""),
                "text": attrs_dict.get("text", ""),
            }
            # Remove empty attributes
            self.found_element = {k: v for k, v in self.found_element.items() if v}


def extract_element_context(
    dom_snapshot: str, event_data: Dict[str, Any]
) -> Dict[str, Any]:
    """Extract rich context about an element from DOM snapshot."""
    if not dom_snapshot:
        return {}

    if not dom_snapshot.lstrip().startswith("<"):
        return {}

    element_id = event_data.get("id", "")
    class_name = event_data.get("className", "")

    if not element_id and not class_name:
        return {}

    classes = class_name.split() if class_name else []

    try:
        parser = ElementExtractor(target_id=element_id, target_classes=classes)
        parser.feed(dom_snapshot)

        if parser.found_element:
            return parser.found_element
    except Exception:
        pass

    return {}


def create_selector(event_data: Dict[str, Any]) -> str:
    """Create a CSS selector from event data."""
    tag = event_data.get("tag", "").lower()
    element_id = event_data.get("id", "")
    class_name = event_data.get("className", "")

    if element_id:
        return f"#{element_id}"
    elif class_name:
        classes = class_name.strip().split()
        if classes:
            return f"{tag}.{'.'.join(classes)}"
    return tag if tag else "*"


def find_navigation_after_step(steps_list, current_idx, max_lookahead=10):
    """Find navigation URL after a click or Enter key event."""
    for i in range(
        current_idx + 1, min(current_idx + max_lookahead + 1, len(steps_list))
    ):
        _, event_type, event_data_str, _ = steps_list[i]
        if event_type in [
            "state:browser:navigated",
            "state:page:navigate_start",
            "state:page:load",
            "state:page:loaded",
        ]:
            if event_data_str:
                try:
                    event_data = json.loads(event_data_str)
                    url = event_data.get("url", "")
                    if url and url != "about:blank":
                        return url
                except json.JSONDecodeError:
                    pass
    return None


def process_single_task(
    cursor,
    task_id: int,
    task_description: str,
    task_type: str = None,
    answer: str = None,
) -> Dict[str, Any]:
    """
    Process a single task and convert it to tool calls.

    Args:
        cursor: Database cursor
        task_id: The ID of the task to convert
        task_description: Description of the task
        task_type: Type of the task (e.g., "information_retrieval", "action")
        answer: Answer for information retrieval tasks

    Returns:
        Dictionary with task data and tool calls
    """

    # Get all steps for the task with DOM snapshots
    cursor.execute(
        """
        SELECT id, event_type, event_data, dom_snapshot 
        FROM steps 
        WHERE task_id = ? 
        ORDER BY id
    """,
        (task_id,),
    )

    steps = cursor.fetchall()

    dom_output_root = Path(DATA_DIR) / "doms"
    dom_output_root.mkdir(parents=True, exist_ok=True)

    def save_dom_snapshot(step_id: int, dom_snapshot: Optional[str]) -> Optional[str]:
        """Persist DOM snapshot to disk and return relative path."""
        if not dom_snapshot:
            return None

        snapshot_text = str(dom_snapshot)
        if not snapshot_text.strip():
            return None

        relative_path = Path("doms") / f"task_{task_id}" / f"step_{step_id}.txt"
        output_path = Path(DATA_DIR) / relative_path
        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "w", encoding="utf-8") as dom_file:
                dom_file.write(snapshot_text)
            return str(relative_path)
        except OSError:
            return None

    tool_calls = []
    typing_buffer = None
    click_buffer = None  # Buffer to accumulate related click events
    first_navigation_handled = False  # Track if we've handled the first navigation

    # Convert steps to list for lookahead
    steps_list = list(steps)

    for idx, (step_id, event_type, event_data_str, dom_snapshot) in enumerate(
        steps_list
    ):
        if event_data_str:
            try:
                event_data = json.loads(event_data_str)
            except json.JSONDecodeError:
                continue
        else:
            event_data = {}

        # Handle navigation events
        if event_type == "state:page:navigate_start" and event_data.get("initial"):
            url = event_data.get("url", "")
            if url:
                first_navigation_handled = True
                tool_calls.append(
                    ToolCallData(
                        type=ToolCall.GO_TO.value,
                        params={"url": url},
                        step_ids=[step_id],
                    )
                )
        # Handle the first browser navigation (often the initial page load)
        elif event_type == "state:browser:navigated" and not first_navigation_handled:
            url = event_data.get("url", "")
            if url and url != "about:blank":
                first_navigation_handled = True
                tool_calls.append(
                    ToolCallData(
                        type=ToolCall.GO_TO.value,
                        params={"url": url},
                        step_ids=[step_id],
                    )
                )
        # Also handle direct navigation to a new domain (not initial)
        elif event_type == "state:browser:navigated" and first_navigation_handled:
            url = event_data.get("url", "")
            # Check if this is a significant navigation (new domain)
            if url and tool_calls:
                # Get the last recorded URL
                last_url = None
                for tc in reversed(tool_calls):
                    if tc.type == ToolCall.GO_TO.value:
                        last_url = tc.params.get("url", "")
                        break

                # Extract domain from URLs
                if last_url:
                    last_domain = urllib.parse.urlparse(last_url).netloc
                    new_domain = urllib.parse.urlparse(url).netloc

                    # If navigating to a different domain, record it as a GO_TO
                    if last_domain != new_domain and new_domain:
                        # Flush any pending buffers first
                        if click_buffer:
                            tool_calls.append(click_buffer)
                            click_buffer = None
                        if typing_buffer:
                            tool_calls.append(typing_buffer)
                            typing_buffer = None

                        tool_calls.append(
                            ToolCallData(
                                type=ToolCall.GO_TO.value,
                                params={"url": url},
                                step_ids=[step_id],
                            )
                        )

        # Handle mouse/pointer events that lead to clicks
        elif event_type in [
            "action:user:pointerdown",
            "action:user:mousedown",
            "action:user:pointerup",
            "action:user:mouseup",
        ]:
            # Start or continue accumulating click-related events
            if click_buffer is None:
                selector = create_selector(event_data)
                context = extract_element_context(dom_snapshot, event_data)
                dom_state_path = save_dom_snapshot(step_id, dom_snapshot)
                params = {"selector": selector}
                if context:
                    params["selector_details"] = context
                if dom_state_path:
                    params["dom_state"] = dom_state_path
                click_buffer = ToolCallData(
                    type=ToolCall.CLICK.value,
                    params=params,
                    step_ids=[step_id],
                )
            else:
                click_buffer.step_ids.append(step_id)
                if "dom_state" not in click_buffer.params:
                    dom_state_path = save_dom_snapshot(step_id, dom_snapshot)
                    if dom_state_path:
                        click_buffer.params["dom_state"] = dom_state_path

        # Handle the actual click event
        elif event_type == "action:user:click":
            # Save any pending typing before the click
            if typing_buffer:
                # Typing interrupted by click, so no Enter was pressed
                if "submit" not in typing_buffer.params:
                    typing_buffer.params["submit"] = False
                tool_calls.append(typing_buffer)
                typing_buffer = None

            selector = create_selector(event_data)
            context = extract_element_context(dom_snapshot, event_data)
            dom_state_path = save_dom_snapshot(step_id, dom_snapshot)

            # Check for navigation after this click
            nav_url = find_navigation_after_step(steps_list, idx)

            # If we have a click buffer and it's for the same element, add to it
            if click_buffer and click_buffer.params.get("selector") == selector:
                click_buffer.step_ids.append(step_id)
                # Update context if we have better info
                if context and "selector_details" not in click_buffer.params:
                    click_buffer.params["selector_details"] = context
                # Add navigation URL if found
                if nav_url and "navigates_to" not in click_buffer.params:
                    click_buffer.params["navigates_to"] = nav_url
                if dom_state_path and "dom_state" not in click_buffer.params:
                    click_buffer.params["dom_state"] = dom_state_path
            elif click_buffer:
                # Different element, save the old buffer and start new
                tool_calls.append(click_buffer)
                params = {"selector": selector}
                if context:
                    params["selector_details"] = context
                if nav_url:
                    params["navigates_to"] = nav_url
                if dom_state_path:
                    params["dom_state"] = dom_state_path
                click_buffer = ToolCallData(
                    type=ToolCall.CLICK.value,
                    params=params,
                    step_ids=[step_id],
                )
            else:
                # No buffer, check if last tool call was same click
                if (
                    tool_calls
                    and tool_calls[-1].type == ToolCall.CLICK.value
                    and tool_calls[-1].params.get("selector") == selector
                ):
                    tool_calls[-1].step_ids.append(step_id)
                    # Update context if we have better info
                    if context and "selector_details" not in tool_calls[-1].params:
                        tool_calls[-1].params["selector_details"] = context
                    # Add navigation URL if found
                    if nav_url and "navigates_to" not in tool_calls[-1].params:
                        tool_calls[-1].params["navigates_to"] = nav_url
                    if dom_state_path and "dom_state" not in tool_calls[-1].params:
                        tool_calls[-1].params["dom_state"] = dom_state_path
                else:
                    params = {"selector": selector}
                    if context:
                        params["selector_details"] = context
                    if nav_url:
                        params["navigates_to"] = nav_url
                    if dom_state_path:
                        params["dom_state"] = dom_state_path
                    click_buffer = ToolCallData(
                        type=ToolCall.CLICK.value,
                        params=params,
                        step_ids=[step_id],
                    )

        # Handle typing events - accumulate keydown/input events
        elif event_type == "action:user:keydown":
            # Flush click buffer if we're starting to type
            if click_buffer:
                tool_calls.append(click_buffer)
                click_buffer = None

            key = event_data.get("key", "")

            # Enter key typically submits, so save the buffer first
            if key == "Enter":
                if typing_buffer:
                    # Mark that this typing was submitted with Enter
                    typing_buffer.params["submit"] = True
                    # Check for navigation after Enter key
                    nav_url = find_navigation_after_step(steps_list, idx)
                    if nav_url:
                        typing_buffer.params["navigates_to"] = nav_url
                    tool_calls.append(typing_buffer)
                    typing_buffer = None
            else:
                # Start accumulating if not already
                if typing_buffer is None:
                    # Look for the previous click to get the selector
                    prev_selector = None
                    for tc in reversed(tool_calls):
                        if tc.type == ToolCall.CLICK.value:
                            prev_selector = tc.params.get("selector")
                            break

                    if not prev_selector:
                        # Try to find selector from a nearby input event
                        prev_selector = "*"

                    typing_buffer = ToolCallData(
                        type=ToolCall.TYPE.value,
                        params={"selector": prev_selector, "text": ""},
                        step_ids=[],
                    )

                typing_buffer.step_ids.append(step_id)

        elif event_type == "action:user:input":
            # Update the accumulated text from the input value
            if typing_buffer:
                typing_buffer.params["text"] = event_data.get("value", "")
                typing_buffer.step_ids.append(step_id)

                # Also update selector and context if we have better info
                selector = create_selector(event_data)
                if selector != "*":
                    typing_buffer.params["selector"] = selector

                # Add element context if not already present
                if "selector_details" not in typing_buffer.params:
                    context = extract_element_context(dom_snapshot, event_data)
                    if context:
                        typing_buffer.params["selector_details"] = context
                    dom_state_path = save_dom_snapshot(step_id, dom_snapshot)
                    if dom_state_path and "dom_state" not in typing_buffer.params:
                        typing_buffer.params["dom_state"] = dom_state_path
                    # If still no context, try to get it from the previous click
                    elif tool_calls:
                        for tc in reversed(tool_calls):
                            if (
                                tc.type == ToolCall.CLICK.value
                                and tc.params.get("selector") == selector
                            ):
                                if "selector_details" in tc.params:
                                    typing_buffer.params["selector_details"] = (
                                        tc.params["selector_details"]
                                    )
                                break

    # Don't forget any pending buffers at the end
    if typing_buffer:
        # If typing buffer wasn't submitted with Enter, mark submit as False
        if "submit" not in typing_buffer.params:
            typing_buffer.params["submit"] = False
        tool_calls.append(typing_buffer)
    if click_buffer:
        # Check if the last click buffer has a navigation
        if click_buffer.step_ids:
            last_step_idx = None
            for i, (sid, _, _, _) in enumerate(steps_list):
                if sid == click_buffer.step_ids[-1]:
                    last_step_idx = i
                    break
            if last_step_idx is not None:
                nav_url = find_navigation_after_step(steps_list, last_step_idx)
                if nav_url and "navigates_to" not in click_buffer.params:
                    click_buffer.params["navigates_to"] = nav_url
        tool_calls.append(click_buffer)

    # Return output data
    result = {
        "task_id": task_id,
        "task_description": task_description,
        "task_type": task_type,  # Include task type
        "tool_calls": [tc.to_dict() for tc in tool_calls],
    }

    # Add answer field for information retrieval tasks
    if task_type == "information_retrieval" and answer:
        result["answer"] = answer
    else:
        result["answer"] = None

    return result


def parse(
    db_path: str = f"{DATA_DIR}/tasks.db", output_path: str = f"{DATA_DIR}/tasks.jsonl"
):
    """
    Convert all tasks from the database into tool calls and write to JSONL file.

    Args:
        db_path: Path to the SQLite database
        output_path: Path to the output JSONL file
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Get all tasks with task_type and answer
    cursor.execute("SELECT id, description, task_type, answer FROM tasks ORDER BY id")
    tasks = cursor.fetchall()

    if not tasks:
        print("No tasks found in database")
        return

    all_results = []

    for task_id, task_description, task_type, answer in tasks:
        try:
            print(f"Processing task {task_id}: {task_description}")
            result = process_single_task(
                cursor, task_id, task_description, task_type, answer
            )
            all_results.append(result)
            print(f"  Found {len(result['tool_calls'])} tool calls")
            if task_type == "information_retrieval":
                print(
                    f"  Task type: {task_type}, Answer: {answer[:50] if answer else 'None'}..."
                )
        except Exception as e:
            print(f"  Error processing task {task_id}: {e}")
            continue

    conn.close()

    # Write all results to file at once (not append)
    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    with open(output_file, "w") as f:
        for result in all_results:
            f.write(json.dumps(result) + "\n")

    print(f"\nSuccessfully processed {len(all_results)} tasks")
    print(f"Results written to {output_path}")

    return all_results


if __name__ == "__main__":
    parse()
