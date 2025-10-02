"""Shared evaluation harness for browser agents.

This module centralizes the orchestration logic that was previously embedded in
specific agent runners (e.g. BrowserUse). The harness coordinates sandbox or
kernel-backed browser sessions, drives agent runners, captures DOM snapshots,
and persists task results. Agent-specific modules only need to implement a
lightweight runner that conforms to :class:`AgentRunner`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Protocol
from src.capture.sandbox import SandboxEnvironment

logger = logging.getLogger(__name__)

CaptureCallback = Callable[[Any, Any, int], None]


@dataclass(slots=True)
class SessionResources:
    """Holds browser session resources for an agent run."""

    cdp_url: Optional[str]
    sandbox: Optional[SandboxEnvironment]
    kernel_client: Optional[Any]
    kernel_browser: Optional[Any]
    headless: bool
    safe_mode: bool
    viewport: Dict[str, int]
    window_size: Dict[str, int]

    async def aclose(self) -> None:
        """Release any acquired resources."""

        if self.kernel_client and self.kernel_browser:
            try:
                self.kernel_client.browsers.delete_by_id(self.kernel_browser.session_id)
            except Exception as exc:  # pragma: no cover - best effort cleanup
                logger.warning("Failed to close kernel browser: %s", exc)

        if self.sandbox:
            try:
                await self.sandbox.close()
            except Exception as exc:  # pragma: no cover - best effort cleanup
                logger.warning("Failed to close sandbox environment: %s", exc)


@dataclass(slots=True)
class AgentContext:
    """Context object passed to agent runners."""

    model: str
    resources: SessionResources
    extras: Optional[Dict[str, Any]] = None


@dataclass(slots=True)
class AgentRunResult:
    """Standard output returned by agent runners."""

    history_dump: List[Dict[str, Any]]
    action_count: int
    usage_summary: Optional[Dict[str, Any]] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None
    answer: Optional[str] = None


class AgentRunner(Protocol):
    """Interface required from agent-specific runners."""

    async def __call__(
        self,
        task: Dict[str, Any],
        context: AgentContext,
        capture_dom: CaptureCallback,
    ) -> AgentRunResult:
        """Execute a single task and return the agent outputs."""

        ...


@dataclass(slots=True)
class HarnessRunConfig:
    """Per-run configuration supplied by CLI wrappers."""

    model: str
    use_sandbox: bool = True
    sandbox_root: Optional[Path] = None
    sandbox_allow_network: bool = False
    sandbox_headless: bool = True
    sandbox_safe_mode: bool = False
    allow_kernel_fallback: bool = True


class SessionProvider(Protocol):
    """Factory responsible for preparing browser session resources."""

    async def __call__(
        self,
        *,
        task: Dict[str, Any],
        run_config: HarnessRunConfig,
        viewport: Dict[str, int],
        window_size: Dict[str, int],
        sandbox_bundle: Optional[Path],
        sandbox_log_dir: Optional[Path],
    ) -> SessionResources: ...
