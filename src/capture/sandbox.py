import asyncio
import json
import logging
import socket
from pathlib import Path
from typing import Optional
from urllib import request as urllib_request
from urllib.error import URLError

from playwright.async_api import Browser as PlaywrightBrowser
from playwright.async_api import BrowserContext, BrowserType, async_playwright

from src.capture.replay import ReplayBundle
from src.config.browser_config import BROWSER_ARGS, CONTEXT_CONFIG


logger = logging.getLogger(__name__)


def _get_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class SandboxEnvironment:
    """Manage an offline replay browser that exposes a CDP endpoint."""

    def __init__(
        self,
        bundle_path: Path,
        *,
        allow_network_fallback: bool = False,
        channel: Optional[str] = None,
    ) -> None:
        self.bundle = ReplayBundle(bundle_path)
        self.allow_network_fallback = allow_network_fallback
        self.channel = channel

        self._playwright = None
        self._browser: Optional[PlaywrightBrowser] = None
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
        launch_args = list(BROWSER_ARGS) + [
            f"--remote-debugging-port={self._debug_port}",
        ]

        launch_kwargs = {
            "headless": False,
            "args": launch_args,
        }
        if self.channel:
            launch_kwargs["channel"] = self.channel

        self._browser = await browser_type.launch(**launch_kwargs)
        self._browser.on(
            "context",
            lambda context: asyncio.create_task(self._configure_context(context)),
        )

        # Ensure at least one context exists for routing
        if not self._browser.contexts:
            context = await self._browser.new_context(**CONTEXT_CONFIG)
            await self._configure_context(context)
            if not context.pages:
                await context.new_page()
        else:
            for context in list(self._browser.contexts):
                await self._configure_context(context)

        self._ws_endpoint = await self._wait_for_ws_endpoint()

        # Preload initial URL if available
        start_url = self.bundle.guess_start_url()
        if start_url and self._browser.contexts:
            page = self._browser.contexts[0].pages[0]
            try:
                await page.goto(start_url)
            except Exception as exc:
                logger.debug("[SANDBOX] Failed to preload %s: %s", start_url, exc)

        return self._ws_endpoint

    async def _wait_for_ws_endpoint(self) -> str:
        assert self._debug_port is not None
        url = f"http://127.0.0.1:{self._debug_port}/json/version"

        def _fetch() -> Optional[str]:
            try:
                with urllib_request.urlopen(url, timeout=0.5) as resp:
                    if getattr(resp, "status", 200) != 200:
                        return None
                    data = json.loads(resp.read().decode("utf-8"))
                    return data.get("webSocketDebuggerUrl")
            except (URLError, TimeoutError, ConnectionError, json.JSONDecodeError, socket.timeout):
                return None

        for _ in range(50):
            ws_endpoint = await asyncio.to_thread(_fetch)
            if ws_endpoint:
                return ws_endpoint
            await asyncio.sleep(0.1)

        raise RuntimeError("Timed out waiting for Chrome debugger endpoint")

    async def _configure_context(self, context: BrowserContext) -> None:
        if context in self._contexts:
            return
        self._contexts.append(context)
        await self.bundle.attach(
            context,
            allow_network_fallback=self.allow_network_fallback,
        )

    async def close(self) -> None:
        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass

        self._browser = None
        self._playwright = None
        self._contexts.clear()
        self._ws_endpoint = None
