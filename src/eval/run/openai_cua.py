"""Evaluation entrypoint for OpenAI's Computer Use API via the shared harness."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional, Tuple
from openai import OpenAI
from dotenv import load_dotenv
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from src.config.browser_config import CONTEXT_CONFIG  # noqa: E402
from src.eval.harness.definitions import AgentContext, AgentRunResult, HarnessRunConfig  # noqa: E402
from src.eval.harness.harness import EvaluationHarness, HarnessConfig  # noqa: E402

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DEFAULT_INSTRUCTIONS = (
    "You are an AI assistant controlling a remote computer. Use the available tools "
    "to complete the user's request. Report a concise final answer when the task is done."
)


class OpenAICUAAgentRunner:
    """Agent runner that proxies tasks through OpenAI's Computer Use API."""

    def __init__(self) -> None:
        self.instructions = DEFAULT_INSTRUCTIONS
        self._client: Optional[Any] = None

    def _get_client(self) -> Any:
        if self._client is None:
            if OpenAI is None:
                raise RuntimeError(
                    "The `openai` package is required to use the Computer Use runner"
                )
            self._client = OpenAI()
        return self._client

    async def __call__(
        self,
        task: Dict[str, Any],
        context: AgentContext,
        capture_dom: Any,
    ) -> AgentRunResult:
        client = self._get_client()

        metadata = {"task_id": str(task["task_id"])} if "task_id" in task else None

        viewport = getattr(context.resources, "viewport", {}) or {}
        window_size = getattr(context.resources, "window_size", {}) or {}
        display_width = viewport.get("width") or window_size.get("width") or 1024
        display_height = viewport.get("height") or window_size.get("height") or 768

        request_payload = {
            "model": context.model,
            "instructions": self.instructions,
            "tools": [
                {
                    "type": "computer_use_preview",
                    "display_width": display_width,
                    "display_height": display_height,
                    "environment": "browser",
                }
            ],
            "input": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": task["task_description"],
                        }
                    ],
                }
            ],
            "reasoning": {"summary": "concise"},
            "truncation": "auto",
        }

        if metadata is not None:
            request_payload["metadata"] = metadata

        history, tool_calls, final_answer = await asyncio.to_thread(
            self._run_session, client, request_payload
        )

        return AgentRunResult(
            history_dump=history,
            action_count=len(tool_calls),
            tool_calls=tool_calls,
            answer=final_answer,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _run_session(
        self,
        client: Any,
        request_payload: Dict[str, Any],
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Optional[str]]:
        history: List[Dict[str, Any]] = []
        tool_calls: List[Dict[str, Any]] = []
        text_chunks: List[str] = []

        with client.responses.stream(**request_payload) as stream:
            for event in stream:
                event_dict = self._event_to_dict(event)
                history.append(event_dict)

                event_type = event_dict.get("event") or event_dict.get("type")
                if event_type and "tool" in event_type:
                    tool_calls.append(event_dict)

                if event_type == "response.output_text.delta":
                    delta = (
                        event_dict.get("delta")
                        or event_dict.get("text")
                        or event_dict.get("data")
                        or ""
                    )
                    if isinstance(delta, str):
                        text_chunks.append(delta)

            response = stream.get_final_response()

        final_answer = self._extract_final_text(text_chunks, response)
        return history, tool_calls, final_answer

    def _event_to_dict(self, event: Any) -> Dict[str, Any]:
        if hasattr(event, "to_dict_recursive"):
            return event.to_dict_recursive()
        if hasattr(event, "model_dump"):
            return event.model_dump()
        if isinstance(event, dict):
            return event
        return {"event": str(event)}

    def _extract_final_text(
        self, text_chunks: List[str], response: Any
    ) -> Optional[str]:
        accumulated = "".join(text_chunks).strip()
        if accumulated:
            return accumulated

        response_dict: Dict[str, Any]
        if hasattr(response, "model_dump"):
            response_dict = response.model_dump()
        elif isinstance(response, dict):
            response_dict = response
        else:
            return None

        output = response_dict.get("output") or []
        if isinstance(output, list):
            for item in output:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "message":
                    for part in item.get("content", []):
                        if isinstance(part, dict) and part.get("type") in {
                            "output_text",
                            "input_text",
                            "text",
                        }:
                            text = part.get("text")
                            if isinstance(text, str) and text.strip():
                                return text.strip()
                elif item.get("type") in {"text", "output_text"}:
                    text = item.get("text")
                    if isinstance(text, str) and text.strip():
                        return text.strip()

        return None


async def main() -> None:
    viewport = CONTEXT_CONFIG.get("viewport", {"width": 1366, "height": 768})
    window_size = {
        "width": viewport.get("width", 1366),
        "height": viewport.get("height", 768),
    }

    harness = EvaluationHarness(
        HarnessConfig(
            agent_name="openai-cua",
            agent_runner=OpenAICUAAgentRunner(),
            viewport=viewport,
            window_size=window_size,
        )
    )

    run_config = HarnessRunConfig(
        model="computer-use-preview",
        use_sandbox=False,
        sandbox_root=None,
        sandbox_allow_network=False,
        sandbox_headless=True,
        sandbox_safe_mode=False,
        allow_kernel_fallback=False,
    )

    output_file = await harness.run_all_tasks(run_config)
    print(f"\nFull data saved to: {output_file}")


if __name__ == "__main__":
    asyncio.run(main())
