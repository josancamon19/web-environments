import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv
from browser_use import Agent, ChatOpenAI

import sys
import os

if "--prod" in sys.argv:
    DATA_DIR = os.path.join("data", "prod") 
else:
    DATA_DIR = os.path.join("data", "dev")

load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def extract_tool_calls(history: list[dict]) -> List[Dict[str, Any]]:
    """Extract tool calls from browser-use history"""
    tool_calls = []

    for step in history:
        if "model_output" in step and "action" in step["model_output"]:
            actions = step["model_output"]["action"]

            # Get interacted element info from state if available
            interacted_element = None
            if "state" in step and "interacted_element" in step["state"]:
                elements = step["state"]["interacted_element"]
                if elements and len(elements) > 0 and elements[0]:
                    interacted_element = elements[0]

            # Get click coordinates from result metadata if available
            click_coords = None
            if "result" in step:
                for result in step["result"]:
                    if "metadata" in result and "click_x" in result["metadata"]:
                        click_coords = {
                            "x": result["metadata"]["click_x"],
                            "y": result["metadata"]["click_y"],
                        }

            for action in actions:
                # Convert browser-use action format to our tool call format
                if isinstance(action, dict):
                    for action_type, params in action.items():
                        if action_type == "search_google":
                            tool_calls.append(
                                {
                                    "type": "search",
                                    "params": {"query": params.get("query", "")},
                                }
                            )
                        elif action_type == "go_to_url":
                            tool_calls.append(
                                {
                                    "type": "go_to",
                                    "params": {"url": params.get("url", "")},
                                }
                            )
                        elif (
                            action_type == "click_element"
                            or action_type == "click_element_by_index"
                        ):
                            click_params = {}

                            # Try to extract selector from interacted element
                            if interacted_element:
                                # Build selector from element attributes
                                node_name = interacted_element.get(
                                    "node_name", ""
                                ).lower()
                                attrs = interacted_element.get("attributes", {})

                                # Priority order for selectors: id > jsname > class > href > node
                                if "id" in attrs and attrs["id"]:
                                    click_params["selector"] = f"#{attrs['id']}"
                                elif "jsname" in attrs and attrs["jsname"]:
                                    # jsname is Google's custom attribute that acts like an ID
                                    click_params["selector"] = (
                                        f"[jsname='{attrs['jsname']}']"
                                    )
                                elif "class" in attrs and attrs["class"]:
                                    classes = attrs["class"].replace(" ", ".")
                                    click_params["selector"] = f"{node_name}.{classes}"
                                elif "href" in attrs:
                                    click_params["selector"] = (
                                        f"{node_name}[href='{attrs['href']}']"
                                    )
                                else:
                                    click_params["selector"] = node_name or "*"

                                # Add element details including all attributes
                                click_params["element_details"] = {
                                    "node_name": node_name,
                                    "attributes": attrs,
                                    "xpath": interacted_element.get("x_path", ""),
                                }
                            elif "selector" in params:
                                click_params["selector"] = params["selector"]
                            elif "index" in params:
                                click_params["selector"] = f"[index:{params['index']}]"

                            # Add click coordinates if available
                            if click_coords:
                                click_params["coordinates"] = click_coords

                            tool_calls.append(
                                {
                                    "type": "click",
                                    "params": click_params,
                                }
                            )
                        elif action_type == "input_text":
                            tool_calls.append(
                                {
                                    "type": "type",
                                    "params": {
                                        "selector": params.get("selector", ""),
                                        "text": params.get("text", ""),
                                    },
                                }
                            )
                        elif action_type == "scroll":
                            scroll_params = {}
                            if "down" in params:
                                scroll_params["direction"] = (
                                    "down" if params["down"] else "up"
                                )
                            if "num_pages" in params:
                                scroll_params["pages"] = params["num_pages"]
                            tool_calls.append(
                                {
                                    "type": "scroll",
                                    "params": scroll_params,
                                }
                            )
                        elif action_type == "done":
                            # Task completion marker, not a tool call
                            pass

    return tool_calls


def extract_final_answer(history: list[dict], task_type: str) -> Optional[str]:
    """Extract the final answer for information retrieval tasks"""
    if task_type != "information_retrieval":
        return None

    # Look for the final result that marks task completion
    for step in reversed(history):
        if "result" in step:
            for result in step["result"]:
                # Check if task is done and has extracted content
                if result.get("is_done") and "extracted_content" in result:
                    extracted = result["extracted_content"]
                    # Return the extracted content as the answer
                    if extracted and extracted != "None":
                        return extracted

    # If no explicit answer found in results, check the final model output's memory
    # as it might contain the collected information
    for step in reversed(history):
        if "model_output" in step and "memory" in step["model_output"]:
            memory = step["model_output"]["memory"]
            # Only return memory if it seems to contain actual information (not just status)
            if (
                memory and len(memory) > 50
            ):  # Arbitrary threshold for meaningful content
                # Check if this is likely the final answer (not intermediate status)
                status_keywords = [
                    "searching",
                    "navigating",
                    "clicking",
                    "loading",
                    "looking",
                ]
                if not any(keyword in memory.lower() for keyword in status_keywords):
                    return memory

    return None


async def run_task_with_agent(
    task: Dict[str, Any], model: str = "gpt-5-2025-08-07"
) -> Dict[str, Any]:
    """Run a single task with the Browser-Use agent and capture all data"""
    start_time = datetime.now()
    llm = ChatOpenAI(model=model, temperature=0.0)
    agent = Agent(task=task["task_description"], llm=llm, verbose=True, max_steps=20)
    history = await agent.run()
    duration = (datetime.now() - start_time).total_seconds()

    # Extract tool calls instead of full history
    history_dump = history.model_dump()["history"]
    tool_calls = extract_tool_calls(history_dump)
    task_type = task.get("task_type")
    print("task", task)
    answer = extract_final_answer(history_dump, task_type)

    return {
        "task_id": task["task_id"],
        "task_description": task["task_description"],
        "task_type": task_type,
        "success": True,
        "duration_seconds": duration,
        "action_count": len(history.model_actions()),
        "tool_calls": tool_calls,
        "answer": answer,
        "dump": history_dump,
    }


def load_completed_tasks(output_file: Path) -> set:
    """Load task IDs that have already been processed from the output file"""
    completed_task_ids = set()
    if output_file.exists():
        with open(output_file, "r") as f:
            for line in f:
                if line.strip():
                    try:
                        result = json.loads(line)
                        completed_task_ids.add(result["task_id"])
                    except json.JSONDecodeError:
                        continue
    return completed_task_ids


async def process_all_tasks(model: str):
    """Process all tasks and save to JSONL, skipping already completed ones"""
    # Load tasks from input file
    with open(Path(f"{DATA_DIR}/tasks.jsonl"), "r") as f:
        tasks = [json.loads(line) for line in f if line.strip()]

    # Setup output file path
    output_dir = Path("src/eval/results")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / f"browseruse-{model.replace('/', '-')}.jsonl"

    # Load already completed tasks
    completed_task_ids = load_completed_tasks(output_file)

    # Filter out already completed tasks
    tasks_to_process = [t for t in tasks if t["task_id"] not in completed_task_ids]

    logger.info(f"Loaded {len(tasks)} total tasks")
    logger.info(f"Already completed: {len(completed_task_ids)} tasks")
    logger.info(f"Tasks to process: {len(tasks_to_process)}")

    if not tasks_to_process:
        logger.info("All tasks already processed!")
        return output_file

    # Process remaining tasks
    for i, task in enumerate(tasks_to_process):
        logger.info(
            f"Processing task {i + 1}/{len(tasks_to_process)}: "
            f"ID={task['task_id']}, {task['task_description'][:100]}..."
        )

        try:
            result = await run_task_with_agent(task, model)

            # Append result to JSONL file immediately
            with open(output_file, "a") as f:
                f.write(json.dumps(result, default=str) + "\n")

            logger.info(
                f"Task {task['task_id']} - Success: {result['success']}, "
                f"Actions: {result['action_count']}, "
                f"Duration: {result['duration_seconds']:.2f}s"
            )
        except Exception as e:
            logger.error(f"Failed to process task {task['task_id']}: {e}")
            # Save error result
            error_result = {
                "task_id": task["task_id"],
                "task_description": task["task_description"],
                "task_type": task.get("task_type"),
                "success": False,
                "error": str(e),
                "tool_calls": [],
                "answer": None,
            }
            with open(output_file, "a") as f:
                f.write(json.dumps(error_result, default=str) + "\n")

    logger.info(f"All results saved to {output_file}")
    return output_file


async def main():
    # Process tasks with browser-use
    output_file = await process_all_tasks("o3-2025-04-16")
    print(f"\nFull data saved to: {output_file}")


if __name__ == "__main__":
    asyncio.run(main())
