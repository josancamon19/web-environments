"""Shared evaluation harness for browser agents.

This module centralizes the orchestration logic that was previously embedded in
specific agent runners (e.g. BrowserUse). The harness coordinates sandbox or
kernel-backed browser sessions, drives agent runners, captures DOM snapshots,
and persists task results. Agent-specific modules only need to implement a
lightweight runner that conforms to :class:`AgentRunner`.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from config.browser_config import CONTEXT_CONFIG
from src.capture.sandbox import resolve_recorded_bundle
from src.eval.harness.definitions import (
    AgentContext,
    AgentRunner,
    HarnessRunConfig,
    SessionResources,
)
from src.eval.harness.session_provider import DefaultSessionProvider

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Harness implementation
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class HarnessConfig:
    agent_name: str
    agent_runner: AgentRunner
    tool_extractor: Optional[Callable[[List[Dict[str, Any]]], List[Dict[str, Any]]]] = (
        None
    )
    answer_extractor: Optional[
        Callable[[List[Dict[str, Any]], Optional[str]], Optional[str]]
    ] = None


FILE_WRITE_LOCK = asyncio.Lock()


def load_completed_tasks(path: Path) -> set[int]:
    completed: set[int] = set()
    if not path.exists():
        return completed

    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                completed.add(json.loads(line)["task_id"])
            except json.JSONDecodeError:
                continue
    return completed


class EvaluationHarness:
    def __init__(self, config: HarnessConfig) -> None:
        self.config = config
        self.session_provider = DefaultSessionProvider()

    def _output_file_for_model(self, model: str) -> Path:
        safe_name = model.replace("/", "-")
        return (
            Path("results")
            / f"{self.config.agent_name}-{safe_name}.jsonl"
        )

    def _ensure_directories(self) -> None:
        self.config.results_output_dir.mkdir(parents=True, exist_ok=True)
        self.config.dom_output_dir.mkdir(parents=True, exist_ok=True)

    def _load_tasks(self, data_dir: Path) -> List[Dict[str, Any]]:
        tasks_file = data_dir / "tasks.jsonl"
        if not tasks_file.exists():
            raise FileNotFoundError(f"Tasks file not found at {tasks_file}")

        with open(tasks_file, "r", encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    async def _write_result(self, output_file: Path, payload: Dict[str, Any]) -> None:
        async with FILE_WRITE_LOCK:
            with open(output_file, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, default=str) + "\n")

    async def run_all_tasks(self, run_config: HarnessRunConfig) -> Path:
        self._ensure_directories()
        tasks = self._load_tasks(run_config.data_dir)
        output_file = self._output_file_for_model(run_config.model)

        completed = load_completed_tasks(output_file)
        pending_tasks = [t for t in tasks if t["task_id"] not in completed]

        logger.info("Loaded %s tasks (%s already complete)", len(tasks), len(completed))

        if not pending_tasks:
            logger.info("All tasks already processed")
            return output_file

        total_tasks = len(pending_tasks)
        for index, task in enumerate(pending_tasks, start=1):
            sandbox_bundle = self._resolve_sandbox_bundle(run_config, task)
            await self._run_task(
                task,
                run_config=run_config,
                output_file=output_file,
                task_index=index,
                total_tasks=total_tasks,
                sandbox_bundle=sandbox_bundle,
            )

        logger.info("All results saved to %s", output_file)
        return output_file

    async def _run_task(
        self,
        task: Dict[str, Any],
        *,
        run_config: HarnessRunConfig,
        output_file: Path,
        task_index: int,
        total_tasks: int,
        sandbox_bundle: Optional[Path],
    ) -> None:
        logger.info(
            "Processing task %s/%s: ID=%s, %.100s",
            task_index,
            total_tasks,
            task["task_id"],
            task["task_description"],
        )

        dom_task_dir = self.config.dom_output_dir / f"task_{task['task_id']}"
        dom_task_dir.mkdir(parents=True, exist_ok=True)
        step_dom_mapping: Dict[int, str] = {}

        def capture_dom(
            browser_state: Any, agent_output: Any, step_number: int
        ) -> None:
            self._capture_dom_snapshot(
                browser_state=browser_state,
                target_dir=dom_task_dir,
                task_id=task["task_id"],
                step_number=step_number,
                mapping=step_dom_mapping,
            )

        resources: Optional[SessionResources] = None
        start_time = datetime.now()

        try:
            resources = await self.session_provider(
                task=task,
                run_config=run_config,
                viewport=CONTEXT_CONFIG["viewport"],
                window_size=CONTEXT_CONFIG["viewport"],
                sandbox_bundle=sandbox_bundle,
            )

            context = AgentContext(
                model=run_config.model,
                resources=resources,
                extras={
                    "sandbox_bundle": str(sandbox_bundle) if sandbox_bundle else None
                },
            )

            run_result = await self.config.agent_runner(task, context, capture_dom)

            duration = (datetime.now() - start_time).total_seconds()
            history_dump = run_result.history_dump
            tool_calls = run_result.tool_calls
            if tool_calls is None and self.config.tool_extractor:
                tool_calls = self.config.tool_extractor(history_dump)

            answer = run_result.answer
            if answer is None and self.config.answer_extractor:
                answer = self.config.answer_extractor(
                    history_dump, task.get("task_type")
                )

            result_payload = {
                "task_id": task["task_id"],
                "task_description": task["task_description"],
                "task_type": task.get("task_type"),
                "success": True,
                "duration_seconds": duration,
                "action_count": run_result.action_count,
                "tool_calls": tool_calls or [],
                "answer": answer,
                "usage_summary": run_result.usage_summary or {},
                "step_dom_mapping": {str(k): v for k, v in step_dom_mapping.items()},
                "dump": history_dump,
            }

            await self._write_result(output_file, result_payload)
            logger.info(
                "Task %s completed (actions=%s, duration=%.2fs)",
                task["task_id"],
                run_result.action_count,
                duration,
            )
        except Exception as exc:
            duration = (datetime.now() - start_time).total_seconds()
            logger.error("Failed to process task %s: %s", task["task_id"], exc)
            error_payload = {
                "task_id": task["task_id"],
                "task_description": task["task_description"],
                "task_type": task.get("task_type"),
                "success": False,
                "error": str(exc),
                "tool_calls": [],
                "answer": None,
                "usage_summary": {},
                "step_dom_mapping": {},
                "duration_seconds": duration,
            }
            await self._write_result(output_file, error_payload)
        finally:
            if resources is not None:
                await resources.aclose()

    def _resolve_sandbox_bundle(
        self, run_config: HarnessRunConfig, task: Dict[str, Any]
    ) -> Optional[Path]:
        if not (run_config.use_sandbox and run_config.sandbox_root):
            return None

        bundle = resolve_recorded_bundle(run_config.sandbox_root, task["task_id"])
        if bundle is None:
            logger.warning(
                "No sandbox bundle found for task %s under %s",
                task["task_id"],
                run_config.sandbox_root,
            )
        return bundle

    def _capture_dom_snapshot(
        self,
        *,
        browser_state: Any,
        target_dir: Path,
        task_id: int,
        step_number: int,
        mapping: Dict[int, str],
    ) -> None:
        try:
            dom_state = getattr(browser_state, "dom_state", None)
            if not dom_state or not hasattr(dom_state, "llm_representation"):
                return

            accessibility_content = dom_state.llm_representation()
            if not accessibility_content:
                return

            dom_file_path = target_dir / f"step_{step_number}.txt"
            dom_file_path.write_text(accessibility_content, encoding="utf-8")
            relative_path = Path("doms") / f"task_{task_id}" / dom_file_path.name
            mapping[step_number] = str(relative_path)
        except Exception as exc:  # pragma: no cover - best effort capture
            logger.warning(
                "Failed to capture accessibility tree at step %s for task %s: %s",
                step_number,
                task_id,
                exc,
            )
