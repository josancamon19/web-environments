import asyncio
import http.client
import json
import logging
import socket
from pathlib import Path
from typing import List, Optional

from playwright.async_api import Browser as PlaywrightBrowser
from playwright.async_api import BrowserContext, BrowserType, async_playwright

from environments.launch import ReplayBundle
from config.browser_config import BROWSER_ARGS


logger = logging.getLogger(__name__)


def _get_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


SAFE_BROWSER_ARGS = [
    "--disable-gpu",
    "--disable-dev-shm-usage",
    "--no-sandbox",
    "--disable-setuid-sandbox",
]


def resolve_recorded_bundle(root: Path, task_id: int) -> Optional[Path]:
    """Return the newest valid capture bundle directory for a task."""

    task_dir = root / f"task_{task_id}"
    if not task_dir.exists():
        return None

    candidate_dirs = [p for p in task_dir.iterdir() if p.is_dir()]
    candidate_dirs.sort(key=lambda p: (p.name, p.stat().st_mtime), reverse=True)

    def _resolve(path: Path) -> Optional[Path]:
        try:
            manifest_path = ReplayBundle._resolve_manifest(path)
            if manifest_path.exists():
                return manifest_path.parent
        except Exception as exc:
            logger.debug(
                "[SANDBOX] Failed to resolve manifest for task %s at %s: %s",
                task_id,
                path,
                exc,
            )
        return None

    for candidate in candidate_dirs:
        resolved = _resolve(candidate)
        if resolved:
            return resolved

    return _resolve(task_dir)


class SandboxEnvironment:
    """Manage an offline replay browser that exposes a CDP endpoint."""

    def __init__(
        self,
        bundle_path: Path,
        *,
        allow_network_fallback: bool = False,
        headless: bool = False,
        safe_mode: bool = False,
        browser_args: Optional[List[str]] = None,
        include_storage_state: bool = False,
    ) -> None:
        self.bundle = ReplayBundle(bundle_path)
        self.allow_network_fallback = allow_network_fallback
        self.safe_mode = safe_mode
        self.headless = headless
        self.include_storage_state = include_storage_state

        # Set browser args based on mode
        if self.safe_mode:
            self.browser_args = SAFE_BROWSER_ARGS
        elif browser_args is not None:
            self.browser_args = browser_args
        else:
            self.browser_args = BROWSER_ARGS

        self._playwright = None
        self._browser: Optional[PlaywrightBrowser] = None
        # TODO: a list of contexts seem like an overkill
        self._contexts: list[BrowserContext] = []
        self._ws_endpoint: Optional[str] = None
        self._debug_port: Optional[int] = None

    @property
    def ws_endpoint(self) -> str:
        if not self._ws_endpoint:
            raise RuntimeError("Sandbox environment has not been started")
        return self._ws_endpoint

    async def start(self) -> str:
        if self._ws_endpoint:
            return self._ws_endpoint

        self._playwright = await async_playwright().start()
        browser_type: BrowserType = self._playwright.chromium

        self._debug_port = _get_free_port()
        launch_args = list(self.browser_args) + [
            f"--remote-debugging-port={self._debug_port}",
            "--remote-debugging-address=127.0.0.1",
        ]
        logger.info(
            "[SANDBOX] Launching Chromium with CDP port %s (safe_mode=%s, headless=%s)",
            self._debug_port,
            self.safe_mode,
            self.headless,
        )

        launch_kwargs = {"headless": self.headless, "args": launch_args}
        self._browser = await browser_type.launch(**launch_kwargs)
        self._browser.on(
            "context",
            lambda context: asyncio.create_task(self._configure_context(context)),
        )

        # Ensure at least one context exists for routing
        if not self._browser.contexts:
            # TODO: or should use **CONTEXT_CONFIG?
            context_config = self.bundle.get_context_config(
                include_storage_state=self.include_storage_state
            )
            context = await self._browser.new_context(**context_config)
            await self._configure_context(context)
        else:
            for context in list(self._browser.contexts):
                await self._configure_context(context)

        self._ws_endpoint = await self._wait_for_ws_endpoint()
        logger.info("[SANDBOX] Chromium CDP endpoint: %s", self._ws_endpoint)

        # Preload initial URL if available
        start_url = self.bundle.guess_start_url()
        if start_url and self._browser.contexts:
            context = self._browser.contexts[0]
            if not context.pages:
                await context.new_page()
            page = context.pages[0]
            try:
                await page.goto(start_url)
            except Exception as exc:
                logger.debug("[SANDBOX] Failed to preload %s: %s", start_url, exc)

        return self._ws_endpoint

    async def _wait_for_ws_endpoint(self) -> str:
        assert self._debug_port is not None

        def _fetch() -> Optional[str]:
            try:
                conn = http.client.HTTPConnection(
                    "127.0.0.1", self._debug_port, timeout=0.5
                )
                conn.request("GET", "/json/version")
                resp = conn.getresponse()
                if resp.status != 200:
                    conn.close()
                    return None
                data = json.loads(resp.read().decode("utf-8"))
                conn.close()
                return data.get("webSocketDebuggerUrl")
            except (
                TimeoutError,
                ConnectionError,
                json.JSONDecodeError,
                socket.timeout,
                OSError,
            ):
                return None

        for _ in range(50):
            ws_endpoint = await asyncio.to_thread(_fetch)
            if ws_endpoint:
                return ws_endpoint
            await asyncio.sleep(0.1)

        raise RuntimeError("Timed out waiting for Chrome debugger endpoint")

    async def _configure_context(self, context: BrowserContext) -> None:
        """Configure a context with HAR replay using the bundle's configuration."""
        if context in self._contexts:
            return
        self._contexts.append(context)

        # Delegate to the bundle's configure_context method for DRY
        await self.bundle.configure_context(
            context,
            allow_network_fallback=self.allow_network_fallback,
        )

    async def close(self) -> None:
        try:
            await self._browser.close()
        except Exception:
            pass
        try:
            await self._playwright.stop()
        except Exception:
            pass

        self._browser = None
        self._playwright = None
        self._contexts.clear()
        self._ws_endpoint = None
