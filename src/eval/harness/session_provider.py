from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

from dotenv import load_dotenv
from src.capture.sandbox import SandboxEnvironment
from kernel import Kernel
from src.eval.harness.definitions import HarnessRunConfig, SessionResources

logger = logging.getLogger(__name__)


class DefaultSessionProvider:
    """Start a sandbox session when available, otherwise fall back to Kernel."""

    def __init__(self) -> None:
        self._kernel_client: Optional[Any] = None
        self._kernel_lock = asyncio.Lock()

    async def __call__(
        self,
        *,
        task: Dict[str, Any],
        run_config: HarnessRunConfig,
        viewport: Dict[str, int],
        window_size: Dict[str, int],
        sandbox_bundle: Optional[Path],
    ) -> SessionResources:
        sandbox_result = await self._start_sandbox(
            task=task,
            run_config=run_config,
            viewport=viewport,
            window_size=window_size,
            sandbox_bundle=sandbox_bundle,
        )

        if sandbox_result:
            cdp_url, sandbox, headless, safe_mode = sandbox_result
            return SessionResources(
                cdp_url=cdp_url,
                sandbox=sandbox,
                kernel_client=None,
                kernel_browser=None,
                headless=headless,
                safe_mode=safe_mode,
                viewport=viewport,
                window_size=window_size,
            )

        if run_config.allow_kernel_fallback:
            return await self._start_kernel_session(
                viewport=viewport,
                window_size=window_size,
                headless=run_config.sandbox_headless,
            )

        return SessionResources(
            cdp_url=None,
            sandbox=None,
            kernel_client=None,
            kernel_browser=None,
            headless=run_config.sandbox_headless,
            safe_mode=False,
            viewport=viewport,
            window_size=window_size,
        )

    async def _start_sandbox(
        self,
        *,
        task: Dict[str, Any],
        run_config: HarnessRunConfig,
        viewport: Dict[str, int],
        window_size: Dict[str, int],
        sandbox_bundle: Optional[Path],
    ) -> Optional[tuple[str, SandboxEnvironment, bool, bool]]:
        if not (run_config.use_sandbox and sandbox_bundle):
            return None

        last_error: Optional[Exception] = None
        mode_candidates = [True] if run_config.sandbox_safe_mode else [False, True]

        for safe_mode in mode_candidates:
            sandbox = SandboxEnvironment(
                sandbox_bundle,
                allow_network_fallback=run_config.sandbox_allow_network,
                headless=run_config.sandbox_headless if not safe_mode else True,
                safe_mode=safe_mode,
            )
            try:
                cdp_url = await sandbox.start()
                headless = run_config.sandbox_headless if not safe_mode else True
                return cdp_url, sandbox, headless, safe_mode
            except Exception as exc:  # pragma: no cover - best effort cleanup
                last_error = exc
                logger.warning(
                    "Sandbox launch failed for task %s (safe_mode=%s): %s",
                    task.get("task_id"),
                    safe_mode,
                    exc,
                )
                await self._safe_close_sandbox(sandbox)

        if not run_config.allow_kernel_fallback:
            raise last_error or RuntimeError(
                "Sandbox launch failed and fallback disabled"
            )

        logger.info(
            "Sandbox unavailable for task %s, falling back to kernel",
            task.get("task_id"),
        )
        return None

    async def _start_kernel_session(
        self,
        *,
        viewport: Dict[str, int],
        window_size: Dict[str, int],
        headless: bool,
    ) -> SessionResources:
        kernel_client = await self._ensure_kernel_client()
        kernel_browser = kernel_client.browsers.create()
        return SessionResources(
            cdp_url=kernel_browser.cdp_ws_url,
            sandbox=None,
            kernel_client=kernel_client,
            kernel_browser=kernel_browser,
            headless=headless,
            safe_mode=False,
            viewport=viewport,
            window_size=window_size,
        )

    async def _ensure_kernel_client(self) -> Any:
        if self._kernel_client is not None:
            return self._kernel_client

        if Kernel is None:
            raise RuntimeError(
                "Kernel fallback requested but `kernel` package is not installed"
            )

        async with self._kernel_lock:
            if self._kernel_client is None:
                load_dotenv()
                api_key = os.getenv("KERNEL_API_KEY")
                if not api_key:
                    raise RuntimeError(
                        "KERNEL_API_KEY is required when falling back to the hosted kernel"
                    )
                self._kernel_client = Kernel(api_key=api_key)
        return self._kernel_client

    @staticmethod
    async def _safe_close_sandbox(sandbox: SandboxEnvironment) -> None:
        try:
            await sandbox.close()
        except Exception:  # pragma: no cover - best effort cleanup
            pass
