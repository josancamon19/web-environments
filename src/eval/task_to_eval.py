from enum import Enum
import sqlite3
import json
from dataclasses import dataclass
from typing import List, Dict, Any
from pathlib import Path


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


def task_to_eval(
    task_id: int, db_path: str = "data/tasks.db", output_path: str = "data/tasks.jsonl"
):
    """
    Convert a task from the database into a list of tool calls.

    Args:
        task_id: The ID of the task to convert
        db_path: Path to the SQLite database
        output_path: Path to the output JSONL file
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Get task description
    cursor.execute("SELECT description FROM tasks WHERE id = ?", (task_id,))
    task_result = cursor.fetchone()
    if not task_result:
        raise ValueError(f"Task with id {task_id} not found")

    task_description = task_result[0]

    # Get all steps for the task
    cursor.execute(
        """
        SELECT id, event_type, event_data 
        FROM steps 
        WHERE task_id = ? 
        ORDER BY id
    """,
        (task_id,),
    )

    steps = cursor.fetchall()
    conn.close()

    tool_calls = []
    typing_buffer = None
    click_buffer = None  # Buffer to accumulate related click events

    for step_id, event_type, event_data_str in steps:
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
                click_buffer = ToolCallData(
                    type=ToolCall.CLICK.value,
                    params={"selector": selector},
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

            # If we have a click buffer and it's for the same element, add to it
            if click_buffer and click_buffer.params.get("selector") == selector:
                click_buffer.step_ids.append(step_id)
            elif click_buffer:
                # Different element, save the old buffer and start new
                tool_calls.append(click_buffer)
                click_buffer = ToolCallData(
                    type=ToolCall.CLICK.value,
                    params={"selector": selector},
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
                else:
                    click_buffer = ToolCallData(
                        type=ToolCall.CLICK.value,
                        params={"selector": selector},
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

                # Also update selector if we have better info
                selector = create_selector(event_data)
                if selector != "*":
                    typing_buffer.params["selector"] = selector

    # Don't forget any pending buffers at the end
    if typing_buffer:
        tool_calls.append(typing_buffer)
    if click_buffer:
        tool_calls.append(click_buffer)

    # Prepare output data
    output_data = {
        "task_id": task_id,
        "task_description": task_description,
        "tool_calls": [tc.to_dict() for tc in tool_calls],
    }

    # Append to JSONL file
    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    with open(output_file, "a") as f:
        f.write(json.dumps(output_data) + "\n")

    return output_data


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python task_to_eval.py <task_id>")
        sys.exit(1)

    task_id = int(sys.argv[1])
    result = task_to_eval(task_id)
    print(f"Converted task {task_id} to evaluation format")
    print(f"Found {len(result['tool_calls'])} tool calls")
