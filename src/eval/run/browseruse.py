import asyncio
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import typer
from dotenv import load_dotenv
from browser_use import Agent, Browser, ChatOpenAI
from kernel import Kernel

from environments.environment import SandboxEnvironment, resolve_recorded_bundle
from config.browser_config import CONTEXT_CONFIG
from config.storage import DATA_DIR

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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


def _normalize_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None

    cleaned = url.strip()
    if not cleaned:
        return None

    if not cleaned.startswith(("http://", "https://")):
        cleaned = f"https://{cleaned.lstrip('/')}"

    return cleaned


def _resolve_initial_url(task: Dict[str, Any]) -> Optional[str]:
    tool_calls = task.get("tool_calls")
    if isinstance(tool_calls, list):
        for call in tool_calls:
            if not isinstance(call, dict):
                continue
            if call.get("type") != "go_to":
                continue
            params = call.get("params") or {}
            url = _normalize_url(params.get("url"))
            if url:
                return url

    return _normalize_url(task.get("website_url"))


async def cleanup_all_kernel_sessions() -> None:
    """Delete all active Kernel browser sessions before starting a new run."""
    try:
        kernel_client = get_kernel_client()
        sessions = kernel_client.browsers.list()

        if not sessions:
            logger.info("No active Kernel browser sessions to cleanup")
            return

        logger.info(f"Cleaning up {len(sessions)} active Kernel browser sessions")
        for session in sessions:
            try:
                kernel_client.browsers.delete_by_id(session.session_id)
                logger.info(f"Deleted session {session.session_id}")
            except Exception as e:
                logger.warning(f"Failed to delete session {session.session_id}: {e}")

        logger.info("Completed cleanup of all Kernel browser sessions")
    except Exception as e:
        logger.error(f"Error during Kernel session cleanup: {e}")


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
                                    click_params[
                                        "selector"
                                    ] = f"[jsname='{attrs['jsname']}']"
                                elif "class" in attrs and attrs["class"]:
                                    classes = attrs["class"].replace(" ", ".")
                                    click_params["selector"] = f"{node_name}.{classes}"
                                elif "href" in attrs:
                                    click_params[
                                        "selector"
                                    ] = f"{node_name}[href='{attrs['href']}']"
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
    model: str,
    *,
    sandbox_bundle: Optional[Path] = None,
    sandbox_allow_network: bool = False,
    sandbox_headless: bool = True,
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

        logger.info(
            "Starting sandbox for task %s at %s",
            task["task_id"],
            sandbox_bundle,
        )

        sandbox = SandboxEnvironment(
            sandbox_bundle,
            allow_network_fallback=sandbox_allow_network,
            headless=sandbox_headless,
            safe_mode=False,
        )
        try:
            cdp_url = await sandbox.start()
            browser = Browser(
                cdp_url=cdp_url,
                headless=sandbox_headless,
                viewport=viewport,
                window_size=window_size,
                device_scale_factor=1.0,
                is_local=True,
            )
        except Exception as exc:
            sandbox_start_error = exc
            logger.warning(
                "Sandbox launch failed for task %s: %s",
                task["task_id"],
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

        sensitive_data = None
        if "credentials" in task and task["credentials"]:
            sensitive_data = {}
            for cred in task["credentials"]:
                website = cred.get("website")
                fields = cred.get("fields", {})
                if website and fields:
                    domain_patterns = [f"https://*.{website}", f"https://{website}"]
                    for pattern in domain_patterns:
                        sensitive_data[pattern] = fields

        initial_url = _resolve_initial_url(task)
        task_description = task["task_description"].lower()
        if initial_url:
            agent_task = f"Open {initial_url} and {task_description}"
        else:
            agent_task = task_description
            logger.warning(
                "Falling back to raw task description for task %s; no URL found",
                task["task_id"],
            )

        agent = Agent(
            browser_session=browser,
            task=agent_task,
            llm=llm,
            verbose=True,
            register_new_step_callback=capture_accessibility_tree,
            sensitive_data=sensitive_data,
        )

        history = await agent.run(max_steps=30)
        duration = (datetime.now() - start_time).total_seconds()

    finally:
        # Cleanup Kernel browser session
        if kernel_browser and kernel_client:
            try:
                kernel_client.browsers.delete_by_id(kernel_browser.session_id)
                logger.info(
                    f"Cleaned up Kernel browser session {kernel_browser.session_id}"
                )
            except Exception as e:
                logger.error(f"Failed to cleanup Kernel browser session: {e}")

        # Cleanup sandbox
        if sandbox:
            try:
                await sandbox.close()
            except Exception as e:
                logger.error(f"Failed to close sandbox: {e}")

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
        "credentials": task.get("credentials"),
        "dump": history_dump,
    }


def load_completed_tasks(results_dir: Path) -> set:
    """Load task IDs that have already been processed from individual JSON files"""
    completed_task_ids = set()
    results_subdir = results_dir / "results"
    if results_subdir.exists():
        try:
            # Look for all task JSON files in the results subdirectory
            for json_file in results_subdir.glob("*.json"):
                try:
                    with open(json_file, "r") as f:
                        result = json.load(f)
                        if isinstance(result, dict) and "task_id" in result:
                            completed_task_ids.add(result["task_id"])
                except (json.JSONDecodeError, IOError) as e:
                    logger.warning(f"Failed to parse file {json_file}: {e}")
                    continue
        except Exception as e:
            logger.warning(f"Error scanning results directory: {e}")
    return completed_task_ids


async def process_single_task(
    task: Dict[str, Any],
    model: str,
    results_dir: Path,
    task_idx: int,
    total_tasks: int,
    *,
    sandbox_root: Optional[Path],
    sandbox_allow_network: bool,
    sandbox_headless: bool,
    semaphore: Optional[asyncio.Semaphore] = None,
):
    """Process a single task and write results to individual JSON file"""
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

        # Create output file path for this specific task
        results_subdir = results_dir / "results"
        results_subdir.mkdir(parents=True, exist_ok=True)
        output_file = results_subdir / f"{task['task_id']}.json"

        try:
            result = await run_task_with_agent(
                task,
                results_dir,
                model,
                sandbox_bundle=sandbox_bundle,
                sandbox_allow_network=sandbox_allow_network,
                sandbox_headless=sandbox_headless,
            )

            # Write result to individual JSON file
            with open(output_file, "w") as f:
                json.dump(result, f, indent=2, default=str)

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
                "credentials": task.get("credentials"),
            }

            # Write error result to individual JSON file
            with open(output_file, "w") as f:
                json.dump(error_result, f, indent=2, default=str)


async def process_all_tasks(
    model: str,
    *,
    sandbox_root: Optional[Path],
    sandbox_allow_network: bool,
    sandbox_headless: bool,
):
    """Process all tasks and save to individual JSON files, skipping already completed ones"""
    # Cleanup all active Kernel browser sessions before starting
    await cleanup_all_kernel_sessions()

    # Load tasks from input file
    tasks_path = DATA_DIR / "tasks.jsonl"
    if not tasks_path.exists():
        raise FileNotFoundError(f"Tasks file not found at {tasks_path}")

    with open(tasks_path, "r") as f:
        tasks = [json.loads(line) for line in f if line.strip()]

    # Setup output directory with timestamp
    # timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    timestamp = "2025-10-04_16-39-06"
    model_safe = model.replace("/", "-")
    results_dir = Path("results") / f"browseruse-{model_safe}-{timestamp}"
    results_dir.mkdir(parents=True, exist_ok=True)

    # Load already completed tasks
    completed_task_ids = load_completed_tasks(results_dir)
    # completed_task_ids = set()

    # Filter out already completed tasks
    tasks_to_process = [t for t in tasks if t["task_id"] not in completed_task_ids]

    logger.info(f"Loaded {len(tasks)} total tasks")
    logger.info(f"Already completed: {len(completed_task_ids)} tasks")
    logger.info(f"Tasks to process: {len(tasks_to_process)}")

    if not tasks_to_process:
        logger.info("All tasks already processed!")
        return results_dir

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
                results_dir,
                task_index,
                total_tasks,
                sandbox_root=sandbox_root,
                sandbox_allow_network=sandbox_allow_network,
                sandbox_headless=sandbox_headless,
                semaphore=semaphore,
            )
        )

    # Execute all tasks - semaphore ensures only max_concurrent run at once
    # As tasks complete, new ones start immediately without waiting for chunks
    await asyncio.gather(*all_tasks)

    logger.info(f"All results saved to {results_dir}")
    return results_dir


async def main(model: str) -> None:
    sandbox_root: Optional[Path] = None
    # handle no sandbox in case
    candidate_root = (DATA_DIR / "captures").expanduser().resolve()
    assert candidate_root.exists()
    sandbox_root = candidate_root
    logger.info("Using sandbox captures under %s", sandbox_root)

    results_dir = await process_all_tasks(
        model,
        sandbox_root=sandbox_root,
        sandbox_allow_network=False,
        sandbox_headless=False,
    )
    print(f"\nAll results saved to: {results_dir}")


app = typer.Typer(help="Run browser-use agent over recorded tasks")


@app.command()
def run(model: str = typer.Option("gpt-5-nano", "--model", "-m")) -> None:
    asyncio.run(main(model))


def _main() -> None:
    app()


if __name__ == "__main__":
    app()
    # TODO: test evaluation pipeline, real quick
    # TODO: Repeat for 2 more tasks/websites
    # TODO: start writing paper.md
