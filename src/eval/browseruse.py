"""Evaluation entrypoint for the BrowserUse agent built on the shared harness."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from browser_use import (
    Agent as BrowserUseAgent,
    Browser as BrowserUseBrowser,
    ChatOpenAI as BrowserUseChatOpenAI,
)
import typer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from eval.harness.definitions import (
    AgentContext,
    AgentRunResult,
    CaptureCallback,
    HarnessRunConfig,
)
from eval.harness.harness import HarnessConfig
from eval.harness.harness import EvaluationHarness

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def extract_tool_calls(history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Extract tool call summaries from BrowserUse's step history."""

    tool_calls: List[Dict[str, Any]] = []

    for step in history:
        if "model_output" not in step or "action" not in step["model_output"]:
            continue

        actions = step["model_output"].get("action")
        if not actions:
            continue

        if isinstance(actions, dict):
            actions = [actions]

        interacted_element = None
        state = step.get("state")
        if isinstance(state, dict) and "interacted_element" in state:
            elements = state["interacted_element"]
            if elements and len(elements) > 0 and elements[0]:
                interacted_element = elements[0]

        click_coords = None
        results = step.get("result")
        if isinstance(results, list):
            for result in results:
                metadata = result.get("metadata") or {}
                if "click_x" in metadata and "click_y" in metadata:
                    click_coords = {"x": metadata["click_x"], "y": metadata["click_y"]}

        for action in actions:
            if not isinstance(action, dict):
                continue

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
                elif action_type in {"click_element", "click_element_by_index"}:
                    click_params: Dict[str, Any] = {}

                    if interacted_element:
                        node_name = interacted_element.get("node_name", "").lower()
                        attrs = interacted_element.get("attributes", {})

                        if attrs.get("id"):
                            click_params["selector"] = f"#{attrs['id']}"
                        elif attrs.get("jsname"):
                            click_params["selector"] = f"[jsname='{attrs['jsname']}']"
                        elif attrs.get("class"):
                            classes = attrs["class"].replace(" ", ".")
                            click_params["selector"] = f"{node_name}.{classes}"
                        elif attrs.get("href"):
                            click_params["selector"] = (
                                f"{node_name}[href='{attrs['href']}']"
                            )
                        else:
                            click_params["selector"] = node_name or "*"

                        click_params["element_details"] = {
                            "node_name": node_name,
                            "attributes": attrs,
                            "xpath": interacted_element.get("x_path", ""),
                        }
                    elif "selector" in params:
                        click_params["selector"] = params["selector"]
                    elif "index" in params:
                        click_params["selector"] = f"[index:{params['index']}]"

                    if click_coords:
                        click_params["coordinates"] = click_coords

                    tool_calls.append({"type": "click", "params": click_params})
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
                    scroll_params: Dict[str, Any] = {}
                    if "down" in params:
                        scroll_params["direction"] = "down" if params["down"] else "up"
                    if "num_pages" in params:
                        scroll_params["pages"] = params["num_pages"]
                    tool_calls.append({"type": "scroll", "params": scroll_params})

    return tool_calls


def extract_final_answer(
    history: List[Dict[str, Any]], task_type: Optional[str]
) -> Optional[str]:
    if task_type != "information_retrieval":
        return None

    for step in reversed(history):
        results = step.get("result")
        if isinstance(results, list):
            for result in results:
                if result.get("is_done") and result.get("extracted_content") not in {
                    None,
                    "None",
                }:
                    return result["extracted_content"]

    for step in reversed(history):
        model_output = step.get("model_output")
        if not isinstance(model_output, dict):
            continue
        memory = model_output.get("memory")
        if memory and len(memory) > 50:
            status_keywords = {
                "searching",
                "navigating",
                "clicking",
                "loading",
                "looking",
            }
            if not any(keyword in memory.lower() for keyword in status_keywords):
                return memory

    return None


class BrowserUseAgentRunner:
    def __init__(self, max_steps: int = 20, verbose: bool = True) -> None:
        self.max_steps = max_steps
        self.verbose = verbose

    async def __call__(
        self,
        task: Dict[str, Any],
        context: AgentContext,
        capture_dom: CaptureCallback,
    ) -> AgentRunResult:
        resources = context.resources

        llm = BrowserUseChatOpenAI(model=context.model, temperature=0.0)
        browser = BrowserUseBrowser(
            cdp_url=resources.cdp_url,
            headless=resources.headless,
            viewport=resources.viewport,
            window_size=resources.window_size,
            device_scale_factor=1.0,
            is_local=resources.sandbox is not None,
        )

        agent = BrowserUseAgent(
            browser_session=browser,
            task=task["task_description"],
            llm=llm,
            verbose=self.verbose,
            max_steps=self.max_steps,
            register_new_step_callback=capture_dom,
        )

        history = await agent.run()

        usage_summary: Optional[Dict[str, Any]] = None
        if hasattr(agent, "token_cost_service"):
            try:
                usage_summary = (
                    await agent.token_cost_service.get_usage_summary()
                ).model_dump()
            except Exception as exc:
                logger.warning("Failed to get token usage: %s", exc)

        return AgentRunResult(
            history_dump=history.model_dump()["history"],
            action_count=len(history.model_actions()),
            usage_summary=usage_summary,
        )


async def main(
    model: str = "gpt-5-nano",
    use_sandbox: bool = True,
    sandbox_allow_network: bool = True,
    sandbox_headed: bool = False,
    sandbox_safe_mode: bool = False,
    allow_kernel_fallback: bool = True,
) -> None:
    sandbox_root: Optional[Path] = None
    if use_sandbox:
        sandbox_root = Path("data/captures").expanduser().resolve()
        assert sandbox_root.exists()

    run_config = HarnessRunConfig(
        model=model,
        use_sandbox=use_sandbox,
        sandbox_root=sandbox_root,
        sandbox_allow_network=sandbox_allow_network,
        sandbox_headless=not sandbox_headed,
        sandbox_safe_mode=sandbox_safe_mode,
        allow_kernel_fallback=allow_kernel_fallback,
    )

    if run_config.sandbox_safe_mode and not run_config.sandbox_headless:
        logger.warning(
            "Safe mode enforces headless Chromium; overriding headed setting"
        )
        run_config.sandbox_headless = True

    harness_config = HarnessConfig(
        agent_name="browseruse",
        agent_runner=BrowserUseAgentRunner(),
        tool_extractor=extract_tool_calls,
        answer_extractor=extract_final_answer,
    )
    harness = EvaluationHarness(harness_config)
    output_file = await harness.run_all_tasks(run_config)
    print(f"\nFull data saved to: {output_file}")


def _main() -> None:
    typer.run(asyncio.run(main()))


if __name__ == "__main__":
    _main()
