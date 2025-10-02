"""Evaluation entrypoint for the BrowserUse agent built on the shared harness."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
import sys
from typing import Any, Dict, Optional

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


def main(
    model: str = "gpt-5-nano",
    use_sandbox: bool = True,
    sandbox_allow_network: bool = True,
    sandbox_headed: bool = False,
    sandbox_safe_mode: bool = False,
    allow_kernel_fallback: bool = True,
) -> None:
    async def wrapper() -> None:
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
            # tool_extractor=extract_tool_calls, # reuse from browseruse.py
            # answer_extractor=extract_final_answer, # reuse from browseruse.py
        )
        harness = EvaluationHarness(harness_config)
        output_file = await harness.run_all_tasks(run_config)
        print(f"\nFull data saved to: {output_file}")

    asyncio.run(wrapper())


def _main() -> None:
    typer.run(main)


if __name__ == "__main__":
    _main()
