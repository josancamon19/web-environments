import argparse
import asyncio
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from browser_use import Agent, Browser, ChatOpenAI
from kernel import Kernel

import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from src.capture.sandbox import SandboxEnvironment, resolve_recorded_bundle
from src.config.browser_config import CONTEXT_CONFIG
from src.config.storage_config import DATA_DIR

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

file_write_lock = asyncio.Lock()

_kernel_client: Optional[Kernel] = None


def get_kernel_client() -> Kernel:
    global _kernel_client
    if _kernel_client is None:
        api_key = os.getenv("KERNEL_API_KEY")
        if not api_key:
            raise RuntimeError(
                "KERNEL_API_KEY is required when running without a sandbox"
            )
        _kernel_client = Kernel(api_key=api_key)
    return _kernel_client


def extract_tool_calls(history: list[dict]) -> List[Dict[str, Any]]:
    """Extract tool calls from browser-use history"""
    tool_calls = []

    for step in history:
        if "model_output" in step and "action" in step["model_output"]:
            actions = step["model_output"].get("action")
            if not actions:
                continue
            if isinstance(actions, dict):
                actions = [actions]

            # Get interacted element info from state if available
            interacted_element = None
            state = step.get("state")
            if isinstance(state, dict) and "interacted_element" in state:
                elements = state["interacted_element"]
                if elements and len(elements) > 0 and elements[0]:
                    interacted_element = elements[0]

            # Get click coordinates from result metadata if available
            click_coords = None
            results = step.get("result")
            if isinstance(results, list):
                for result in results:
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
        results = step.get("result")
        if isinstance(results, list):
            for result in results:
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
    task: Dict[str, Any],
    results_dir: Path,
    model: str = "gpt-5-nano",
    *,
    sandbox_bundle: Optional[Path] = None,
    sandbox_allow_network: bool = False,
    sandbox_headless: bool = True,
    sandbox_safe_mode: bool = False,
) -> Dict[str, Any]:
    """Run a single task with the Browser-Use agent and capture all data."""

    start_time = datetime.now()

    doms_output_dir = results_dir / "doms"
    task_dom_dir = doms_output_dir / f"task_{task['task_id']}"
    task_dom_dir.mkdir(parents=True, exist_ok=True)

    step_dom_mapping: Dict[int, str] = {}

    def capture_accessibility_tree(browser_state, agent_output, step_number):
        try:
            if (
                browser_state
                and hasattr(browser_state, "dom_state")
                and browser_state.dom_state
            ):
                accessibility_content = browser_state.dom_state.llm_representation()
                dom_file_path = task_dom_dir / f"step_{step_number}.txt"
                with open(dom_file_path, "w", encoding="utf-8") as f:
                    f.write(accessibility_content)
                relative_path = f"doms/task_{task['task_id']}/step_{step_number}.txt"
                step_dom_mapping[step_number] = relative_path
        except Exception as exc:
            logger.warning(
                "Failed to capture accessibility tree at step %s: %s",
                step_number,
                exc,
            )

    llm = ChatOpenAI(model=model, temperature=0.0)

    sandbox = None
    kernel_browser = None
    kernel_client: Optional[Kernel] = None

    viewport = CONTEXT_CONFIG.get("viewport", {"width": 1366, "height": 768})
    window_size = {
        "width": viewport.get("width", 1366),
        "height": viewport.get("height", 768),
    }

    try:
        sandbox_start_error: Optional[Exception] = None
        sandbox_modes = []
        if sandbox_bundle:
            if sandbox_safe_mode:
                sandbox_modes = [True]
            else:
                sandbox_modes = [False, True]

        for safe_mode in sandbox_modes:
            logger.info(
                "Starting sandbox for task %s at %s (safe_mode=%s)",
                task["task_id"],
                sandbox_bundle,
                safe_mode,
            )

            # Set up log directory for tracking cached vs not-found URLs
            log_dir = results_dir / "logs" / f"task_{task['task_id']}"

            sandbox = SandboxEnvironment(
                sandbox_bundle,
                allow_network_fallback=sandbox_allow_network,
                headless=sandbox_headless,
                safe_mode=safe_mode,
                log_dir=log_dir,
            )
            try:
                cdp_url = await sandbox.start()
                browser = Browser(
                    cdp_url=cdp_url,
                    headless=sandbox_headless if not safe_mode else True,
                    viewport=viewport,
                    window_size=window_size,
                    device_scale_factor=1.0,
                    is_local=True,
                )
                break
            except Exception as exc:
                sandbox_start_error = exc
                logger.warning(
                    "Sandbox launch failed for task %s (safe_mode=%s): %s",
                    task["task_id"],
                    safe_mode,
                    exc,
                )
                try:
                    await sandbox.close()
                except Exception:
                    pass
                sandbox = None

        if sandbox_bundle and sandbox is None:
            if sandbox_allow_network:
                logger.info(
                    "Sandbox unavailable for task %s, falling back to Kernel browser",
                    task["task_id"],
                )
            else:
                raise sandbox_start_error or RuntimeError(
                    "Sandbox launch failed and fallback disabled"
                )

        if sandbox is None:
            kernel_client = get_kernel_client()
            kernel_browser = kernel_client.browsers.create()
            browser = Browser(
                cdp_url=kernel_browser.cdp_ws_url,
                headless=sandbox_headless,
                viewport=viewport,
                window_size=window_size,
                device_scale_factor=1.0,
            )

        agent = Agent(
            browser_session=browser,
            task=task["task_description"],
            llm=llm,
            verbose=True,
            register_new_step_callback=capture_accessibility_tree,
        )

        history = await agent.run(max_steps=30)
        duration = (datetime.now() - start_time).total_seconds()

    finally:
        if kernel_browser and kernel_client:
            kernel_client.browsers.delete_by_id(kernel_browser.session_id)
        if sandbox:
            await sandbox.close()

    # Extract tool calls instead of full history
    history_dump = history.model_dump()["history"]
    tool_calls = extract_tool_calls(history_dump)
    task_type = task.get("task_type")
    print("task", task)
    answer = extract_final_answer(history_dump, task_type)

    # Get token usage
    usage_summary = {}
    if hasattr(agent, "token_cost_service"):
        try:
            # Get usage summary which is async
            usage_summary = await agent.token_cost_service.get_usage_summary()
            usage_summary = usage_summary.model_dump()
            logger.info(f"Token usage summary: {usage_summary}")

        except Exception as e:
            logger.warning(f"Failed to get token usage: {e}")
            usage_summary = {}

    return {
        "task_id": task["task_id"],
        "task_description": task["task_description"],
        "task_type": task_type,
        "success": True,
        "duration_seconds": duration,
        "action_count": len(history.model_actions()),
        "tool_calls": tool_calls,
        "answer": answer,
        "usage_summary": usage_summary,
        "step_dom_mapping": step_dom_mapping,
        "dump": history_dump,
    }


def load_completed_tasks(output_file: Path) -> set:
    """Load task IDs that have already been processed from the JSONL output file"""
    completed_task_ids = set()
    if output_file.exists():
        try:
            with open(output_file, "r") as f:
                for line in f:
                    line = line.strip()
                    if line:  # Skip empty lines
                        try:
                            result = json.loads(line)
                            if isinstance(result, dict) and "task_id" in result:
                                completed_task_ids.add(result["task_id"])
                        except json.JSONDecodeError:
                            logger.warning(
                                f"Failed to parse line in {output_file}: {line[:100]}"
                            )
                            continue
        except FileNotFoundError:
            pass
    return completed_task_ids


async def process_single_task(
    task: Dict[str, Any],
    model: str,
    output_file: Path,
    task_idx: int,
    total_tasks: int,
    *,
    sandbox_root: Optional[Path],
    sandbox_allow_network: bool,
    sandbox_headless: bool,
    sandbox_safe_mode: bool,
    results_dir: Path,
    semaphore: Optional[asyncio.Semaphore] = None,
):
    """Process a single task and write results to file"""
    # Use semaphore to limit concurrency if provided
    async with semaphore if semaphore else asyncio.Lock():
        logger.info(
            f"Processing task {task_idx}/{total_tasks}: "
            f"ID={task['task_id']}, {task['task_description'][:100]}..."
        )

        sandbox_bundle: Optional[Path] = None
        if sandbox_root:
            sandbox_bundle = resolve_recorded_bundle(sandbox_root, task["task_id"])
            if not sandbox_bundle:
                logger.warning(
                    "No sandbox bundle found for task %s under %s; falling back to Kernel",
                    task["task_id"],
                    sandbox_root,
                )

        try:
            result = await run_task_with_agent(
                task,
                results_dir,
                model,
                sandbox_bundle=sandbox_bundle,
                sandbox_allow_network=sandbox_allow_network,
                sandbox_headless=sandbox_headless,
                sandbox_safe_mode=sandbox_safe_mode,
            )

            # Write result with thread-safe lock (append JSONL)
            async with file_write_lock:
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
                "usage_summary": {},
                "step_dom_mapping": {},
            }

            # Write error result with thread-safe lock (append JSONL)
            async with file_write_lock:
                with open(output_file, "a") as f:
                    f.write(json.dumps(error_result, default=str) + "\n")


async def process_all_tasks(
    model: str,
    *,
    sandbox_root: Optional[Path],
    sandbox_allow_network: bool,
    sandbox_headless: bool,
    sandbox_safe_mode: bool,
):
    """Process all tasks and save to JSONL, skipping already completed ones"""
    # Load tasks from input file
    tasks_path = DATA_DIR / "tasks.jsonl"
    if not tasks_path.exists():
        raise FileNotFoundError(f"Tasks file not found at {tasks_path}")

    with open(tasks_path, "r") as f:
        tasks = [json.loads(line) for line in f if line.strip()]

    # Setup output directory with timestamp
    # timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    timestamp = "2025-10-02_19-58-56"
    model_safe = model.replace("/", "-")
    results_dir = Path("results") / f"browseruse-{model_safe}-{timestamp}"
    results_dir.mkdir(parents=True, exist_ok=True)
    output_file = results_dir / "results.jsonl"

    # Load already completed tasks
    completed_task_ids = load_completed_tasks(output_file)
    # completed_task_ids = set()

    # Filter out already completed tasks
    tasks_to_process = [t for t in tasks if t["task_id"] not in completed_task_ids]

    logger.info(f"Loaded {len(tasks)} total tasks")
    logger.info(f"Already completed: {len(completed_task_ids)} tasks")
    logger.info(f"Tasks to process: {len(tasks_to_process)}")

    if not tasks_to_process:
        logger.info("All tasks already processed!")
        return output_file

    # Set up concurrency limit based on whether we're using sandbox
    total_tasks = len(tasks_to_process)
    max_concurrent = 1 if sandbox_root else 4
    semaphore = asyncio.Semaphore(max_concurrent)

    logger.info(
        f"Processing {total_tasks} tasks with max {max_concurrent} concurrent workers"
    )

    # Create all tasks upfront - semaphore will control concurrency
    # As soon as one task finishes, the next one will start automatically
    all_tasks = []
    for task_index, task in enumerate(tasks_to_process, start=1):
        all_tasks.append(
            process_single_task(
                task,
                model,
                output_file,
                task_index,
                total_tasks,
                sandbox_root=sandbox_root,
                sandbox_allow_network=sandbox_allow_network,
                sandbox_headless=sandbox_headless,
                sandbox_safe_mode=sandbox_safe_mode,
                results_dir=results_dir,
                semaphore=semaphore,
            )
        )

    # Execute all tasks - semaphore ensures only max_concurrent run at once
    # As tasks complete, new ones start immediately without waiting for chunks
    await asyncio.gather(*all_tasks)

    logger.info(f"All results saved to {output_file}")
    return output_file


async def main(args: argparse.Namespace) -> None:
    sandbox_root: Optional[Path] = None
    if not args.no_sandbox:
        candidate_root = (DATA_DIR / "captures").expanduser().resolve()
        if candidate_root.exists():
            sandbox_root = candidate_root
            logger.info("Using sandbox captures under %s", sandbox_root)
        else:
            logger.warning(
                "Sandbox root %s not found; falling back to Kernel browser",
                candidate_root,
            )

    sandbox_headless = not args.sandbox_headed
    sandbox_safe_mode = args.sandbox_safe_mode
    if sandbox_safe_mode and args.sandbox_headed:
        logger.warning(
            "Sandbox safe mode forces headless Chromium; ignoring --sandbox-headed"
        )
        sandbox_headless = True

    output_file = await process_all_tasks(
        args.model,
        sandbox_root=sandbox_root,
        sandbox_allow_network=args.sandbox_allow_network,
        sandbox_headless=sandbox_headless,
        sandbox_safe_mode=sandbox_safe_mode,
    )
    print(f"\nFull data saved to: {output_file}")


def parse_args() -> argparse.Namespace:
    logger.info("Parsing arguments")
    parser = argparse.ArgumentParser(
        description="Run browser-use agent over recorded tasks"
    )
    parser.add_argument("--model", default="gpt-5-nano", help="LLM model name to use")
    parser.add_argument(
        "--no-sandbox",
        action="store_true",
        help="Disable sandbox replay and use the Kernel browser",
    )
    parser.add_argument(
        "--sandbox-allow-network",
        action="store_true",
        help="Allow sandboxed replay to fall back to live network requests",
    )
    parser.add_argument(
        "--sandbox-headed",
        action="store_true",
        help="Launch sandbox Chromium with a visible window",
    )
    parser.add_argument(
        "--sandbox-safe-mode",
        action="store_true",
        help="Use a reduced argument set and headless Chromium for stability",
    )
    return parser.parse_args()


def _main() -> None:
    cli_args = parse_args()
    asyncio.run(main(cli_args))
    # - then work on checkpoint based eval, num checkpoints? depending on task complexity or later?

    # Checkpoints analysis
    # - "Executes the search for 'metformin 1000 mg tablet', bringing up relevant results."
    # - some tasks like apple are very brief, that it kinda doesn't make sense.
    # - what if human didn't even complete it?
    # - honestly they seem to make sense, like I don't see any obvious issues, search click, very simple
    # - - better judged once longer horizon tasks are tested. (will check next batch of tasks regardless)

    # Evaluation analysis
    # TODO: run browseruse results using website field
    # TODO: run evaluation on tasks, certainity and accuracy, improve depending.

    # =====
    # TODO: db to jsonl include num human steps, and duration
    # TODO: store results in results/$task_id.json instead.


if __name__ == "__main__":
    _main()
