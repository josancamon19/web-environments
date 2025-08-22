from enum import Enum
import sqlite3
import json
from dataclasses import dataclass
from typing import List, Dict, Any, Optional
from pathlib import Path
from html.parser import HTMLParser


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


def process_single_task(cursor, task_id: int, task_description: str) -> Dict[str, Any]:
    """
    Process a single task and convert it to tool calls.

    Args:
        cursor: Database cursor
        task_id: The ID of the task to convert
        task_description: Description of the task

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

    tool_calls = []
    typing_buffer = None
    click_buffer = None  # Buffer to accumulate related click events

    for step_id, event_type, event_data_str, dom_snapshot in steps:
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
                params = {"selector": selector}
                if context:
                    params["selector_details"] = context
                click_buffer = ToolCallData(
                    type=ToolCall.CLICK.value,
                    params=params,
                    step_ids=[step_id],
                )
            else:
                click_buffer.step_ids.append(step_id)

        # Handle the actual click event
        elif event_type == "action:user:click":
            # Save any pending typing before the click
            if typing_buffer:
                tool_calls.append(typing_buffer)
                typing_buffer = None

            selector = create_selector(event_data)
            context = extract_element_context(dom_snapshot, event_data)

            # If we have a click buffer and it's for the same element, add to it
            if click_buffer and click_buffer.params.get("selector") == selector:
                click_buffer.step_ids.append(step_id)
                # Update context if we have better info
                if context and "selector_details" not in click_buffer.params:
                    click_buffer.params["selector_details"] = context
            elif click_buffer:
                # Different element, save the old buffer and start new
                tool_calls.append(click_buffer)
                params = {"selector": selector}
                if context:
                    params["selector_details"] = context
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
                else:
                    params = {"selector": selector}
                    if context:
                        params["selector_details"] = context
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
        tool_calls.append(typing_buffer)
    if click_buffer:
        tool_calls.append(click_buffer)

    # Return output data
    return {
        "task_id": task_id,
        "task_description": task_description,
        "tool_calls": [tc.to_dict() for tc in tool_calls],
    }


def parse(db_path: str = "data/tasks.db", output_path: str = "data/tasks.jsonl"):
    """
    Convert all tasks from the database into tool calls and write to JSONL file.

    Args:
        db_path: Path to the SQLite database
        output_path: Path to the output JSONL file
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Get all tasks
    cursor.execute("SELECT id, description FROM tasks ORDER BY id")
    tasks = cursor.fetchall()

    if not tasks:
        print("No tasks found in database")
        return

    all_results = []

    for task_id, task_description in tasks:
        try:
            print(f"Processing task {task_id}: {task_description}")
            result = process_single_task(cursor, task_id, task_description)
            all_results.append(result)
            print(f"  Found {len(result['tool_calls'])} tool calls")
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
