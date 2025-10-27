"""Main script to convert recorded task events into structured tool calls."""

import json
from typing import Dict, Any, Optional
from pathlib import Path
from datetime import datetime
from config.storage import DATA_DIR
from db.models import TaskModel, StepModel
from db.database import Database
from scripts.postprocessing.tool_calls.event_handlers import (
    handle_initial_navigation,
    handle_domain_navigation,
    handle_mouse_event,
    handle_click_event,
    handle_keydown_event,
    handle_input_event,
    find_navigation_after_step,
)


def save_dom_snapshot(
    task_id: int, step_id: int, dom_snapshot: Optional[str]
) -> Optional[str]:
    """Persist DOM snapshot to disk and return relative path."""
    if not dom_snapshot:
        return None

    snapshot_text = str(dom_snapshot)
    if not snapshot_text.strip():
        return None

    relative_path = Path("doms") / f"task_{task_id}" / f"step_{step_id}.txt"
    output_path = DATA_DIR / relative_path
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as dom_file:
            dom_file.write(snapshot_text)
        return str(relative_path)
    except OSError:
        return None


def calculate_duration(
    created_at: Optional[str],
    ended_at: Optional[str],
    duration_seconds: Optional[float],
) -> Optional[float]:
    """Calculate task duration from timestamps or use database value."""
    if created_at and ended_at:
        try:
            # Handle non-standard format: 2025-10-02T20-19-29.021Z (dashes in time)
            # Convert to standard ISO format by replacing dashes with colons in time portion
            def normalize_timestamp(ts: str) -> str:
                # Split at 'T' to separate date and time
                if "T" in ts:
                    parts = ts.split("T")
                    date_part = parts[0]
                    time_part = parts[1]
                    # Replace dashes with colons in time part (only first 2 occurrences)
                    time_normalized = time_part.replace("-", ":", 2)
                    return f"{date_part}T{time_normalized}"
                return ts

            created_normalized = normalize_timestamp(created_at).replace("Z", "+00:00")
            ended_normalized = normalize_timestamp(ended_at).replace("Z", "+00:00")

            start_dt = datetime.fromisoformat(created_normalized)
            end_dt = datetime.fromisoformat(ended_normalized)
            return round((end_dt - start_dt).total_seconds(), 3)
        except (ValueError, AttributeError):
            # Fall back to database value if timestamp parsing fails
            return duration_seconds
    else:
        # Use database value if timestamps are missing
        return duration_seconds


def process_single_task(
    task_id: int,
    task_description: str,
    task_type: str = None,
    answer: str = None,
    website: str = None,
    created_at: str = None,
    ended_at: str = None,
    duration_seconds: float = None,
) -> Dict[str, Any]:
    """
    Process a single task and convert it to tool calls.

    Args:
        task_id: The ID of the task to convert
        task_description: Description of the task
        task_type: Type of the task (e.g., "information_retrieval", "action")
        answer: Answer for information retrieval tasks
        website: Website URL associated with the task
        created_at: Task creation timestamp
        ended_at: Task end timestamp
        duration_seconds: Task duration in seconds (if available)

    Returns:
        Dictionary with task data and tool calls
    """

    # Get all steps for the task with DOM snapshots using Peewee
    steps_query = (
        StepModel.select()
        .where(StepModel.task == task_id)
        .order_by(StepModel.timestamp)
    )

    # Helper function that captures task_id
    def save_dom_fn(step_id: int, dom_snapshot: Optional[str]) -> Optional[str]:
        return save_dom_snapshot(task_id, step_id, dom_snapshot)

    tool_calls = []
    typing_buffer = None
    click_buffer = None  # Buffer to accumulate related click events
    first_navigation_handled = False  # Track if we've handled the first navigation

    # Convert steps to list for lookahead - convert to tuples like the old version
    steps_list = [
        (
            step.id,
            step.event_type,
            step.event_data,  # Already a string in DB
            step.dom_snapshot,
            step.timestamp,
        )
        for step in steps_query
    ]

    for idx, (
        step_id,
        event_type,
        event_data_str,
        dom_snapshot,
        timestamp,
    ) in enumerate(steps_list):
        if event_data_str:
            try:
                event_data = json.loads(event_data_str)
            except json.JSONDecodeError:
                continue
        else:
            event_data = {}

        # Handle navigation events
        if event_type == "state:page:navigate_start" and event_data.get("initial"):
            nav_call = handle_initial_navigation(event_data, step_id, timestamp)
            if nav_call:
                first_navigation_handled = True
                tool_calls.append(nav_call)

        # Handle the first browser navigation (often the initial page load)
        elif event_type == "state:browser:navigated" and not first_navigation_handled:
            url = event_data.get("url", "")
            if url and url != "about:blank":
                first_navigation_handled = True
                nav_call = handle_initial_navigation(event_data, step_id, timestamp)
                if nav_call:
                    tool_calls.append(nav_call)

        # Also handle direct navigation to a new domain (not initial)
        elif event_type == "state:browser:navigated" and first_navigation_handled:
            nav_call = handle_domain_navigation(
                event_data, step_id, timestamp, tool_calls
            )
            if nav_call:
                # Flush any pending buffers first
                if click_buffer:
                    tool_calls.append(click_buffer)
                    click_buffer = None
                if typing_buffer:
                    tool_calls.append(typing_buffer)
                    typing_buffer = None
                tool_calls.append(nav_call)

        # Handle mouse/pointer events that lead to clicks
        elif event_type in [
            "action:user:pointerdown",
            "action:user:mousedown",
            "action:user:pointerup",
            "action:user:mouseup",
        ]:
            click_buffer = handle_mouse_event(
                event_data, step_id, timestamp, dom_snapshot, click_buffer, save_dom_fn
            )

        # Handle the actual click event
        elif event_type == "action:user:click":
            # Save any pending typing before the click
            if typing_buffer:
                # Typing interrupted by click, so no Enter was pressed
                if "submit" not in typing_buffer.params:
                    typing_buffer.params["submit"] = False
                tool_calls.append(typing_buffer)
                typing_buffer = None

            new_click_buffer = handle_click_event(
                event_data,
                step_id,
                timestamp,
                dom_snapshot,
                click_buffer,
                tool_calls,
                steps_list,
                idx,
                save_dom_fn,
            )
            # If None was returned, it means the last tool call was updated
            if new_click_buffer is not None:
                click_buffer = new_click_buffer

        # Handle typing events - accumulate keydown/input events
        elif event_type == "action:user:keydown":
            # Flush click buffer if we're starting to type
            if click_buffer:
                tool_calls.append(click_buffer)
                click_buffer = None

            typing_buffer = handle_keydown_event(
                event_data,
                step_id,
                timestamp,
                typing_buffer,
                tool_calls,
                steps_list,
                idx,
            )

        elif event_type == "action:user:input":
            typing_buffer = handle_input_event(
                event_data,
                step_id,
                dom_snapshot,
                typing_buffer,
                tool_calls,
                save_dom_fn,
            )

    # Flush any pending buffers at the end
    if typing_buffer:
        # If typing buffer wasn't submitted with Enter, mark submit as False
        if "submit" not in typing_buffer.params:
            typing_buffer.params["submit"] = False
        tool_calls.append(typing_buffer)

    if click_buffer:
        # Check if the last click buffer has a navigation
        if click_buffer.step_ids:
            last_step_idx = None
            for i, (sid, _, _, _, _) in enumerate(steps_list):
                if sid == click_buffer.step_ids[-1]:
                    last_step_idx = i
                    break
            if last_step_idx is not None:
                nav_url = find_navigation_after_step(steps_list, last_step_idx)
                if nav_url and "navigates_to" not in click_buffer.params:
                    click_buffer.params["navigates_to"] = nav_url
        tool_calls.append(click_buffer)

    # Calculate duration
    calculated_duration = calculate_duration(created_at, ended_at, duration_seconds)

    # Build result
    result = {
        "task_id": task_id,
        "task_description": task_description,
        "task_type": task_type,
        "website_url": website,
        "num_steps": len(tool_calls),
        "duration_seconds": calculated_duration,
        "tool_calls": [tc.to_dict() for tc in tool_calls],
        "answer": answer if task_type == "information_retrieval" and answer else None,
    }

    return result


def parse(
    db_path: str = f"{DATA_DIR}/tasks.db",
    output_path: str = f"{DATA_DIR}/tasks.jsonl",
):
    """
    Convert all tasks from the database into tool calls and write to JSONL file.

    Args:
        db_path: Path to the SQLite database
        output_path: Path to the output JSONL file
    """
    # Initialize database
    Database.get_instance(db_path)

    # Get all tasks with task_type, answer, website, and timing info using Peewee
    tasks = TaskModel.select().order_by(TaskModel.id)

    if not tasks.exists():
        print("No tasks found in database")
        return

    all_results = []

    for task in tasks:
        print(f"Processing task {task.id}: {task.description}")
        result = process_single_task(
            task.id,
            task.description,
            task.task_type,
            task.answer,
            task.website,
            task.created_at,
            task.ended_at,
            task.duration_seconds,
        )
        all_results.append(result)
        print(f"  Found {len(result['tool_calls'])} tool calls")
        if task.task_type == "information_retrieval":
            answer_preview = task.answer[:50] if task.answer else "None"
            print(f"  Task type: {task.task_type}, Answer: {answer_preview}...")

    # Write all results to file at once (not append)
    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    with open(output_file, "w") as f:
        for result in all_results:
            f.write(json.dumps(result) + "\n")

    print(f"\nSuccessfully processed {len(all_results)} tasks")
    print(f"Results written to {output_path}")


if __name__ == "__main__":
    parse()
