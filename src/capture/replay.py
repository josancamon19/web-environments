import argparse
import asyncio
import json
import logging
import os
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Deque, Dict, Optional, Tuple

from playwright.async_api import Browser, BrowserContext, Route


logger = logging.getLogger(__name__)


class ReplayBundle:
    """Replay previously captured browsing resources."""

    def __init__(self, bundle_path: Path):
        bundle_path = bundle_path.expanduser().resolve()

        if bundle_path.is_file():
            if bundle_path.name == "manifest.json":
                bundle_path = bundle_path.parent
            else:
                raise FileNotFoundError(
                    f"Bundle path points to unexpected file: {bundle_path}"
                )

        manifest_path = self._resolve_manifest(bundle_path)

        if not manifest_path.exists():
            raise FileNotFoundError(f"No manifest found at {manifest_path}")

        self.bundle_path = manifest_path.parent
        self.manifest_path = manifest_path

        self.manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        self.resources = self.manifest.get("resources", [])
        self.environment = self.manifest.get("environment", {})
        self._payloads: Dict[Tuple[str, str, str], Deque[Dict[str, Any]]] = defaultdict(deque)

        for resource in self.resources:
            key = self._resource_key(resource)
            self._payloads[key].append(resource)

        logger.info(
            "Loaded bundle %s with %s recorded resources",
            bundle_path,
            len(self.resources),
        )

    def guess_start_url(self) -> Optional[str]:
        for resource in self.resources:
            if resource.get("resource_type") == "document" and resource.get("status", 200) < 400:
                return resource.get("url")
        return None

    async def build_context(
        self,
        browser: Browser,
        *,
        allow_network_fallback: bool = False,
    ) -> BrowserContext:
        context_config = dict(self.environment.get("context_config") or {})
        storage_state_path = self._storage_state_path()

        if storage_state_path:
            context_config["storage_state"] = str(storage_state_path)

        context = await browser.new_context(**context_config)
        await self.attach(context, allow_network_fallback=allow_network_fallback)
        return context

    async def attach(
        self,
        context: BrowserContext,
        *,
        allow_network_fallback: bool = False,
    ) -> None:
        async def _handler(route: Route):
            await self._fulfill(route, allow_network_fallback=allow_network_fallback)

        await context.route("**/*", _handler)

    async def _fulfill(self, route: Route, *, allow_network_fallback: bool) -> None:
        request = route.request
        post_data = await self._safe_post_data(request)
        key = (request.method, request.url, post_data or "")

        queue = self._payloads.get(key)
        if queue and queue:
            payload = queue.popleft()
            body_bytes = self._load_body(payload)
            headers = dict(payload.get("response_headers") or {})
            if body_bytes is not None:
                has_length = any(k.lower() == "content-length" for k in headers)
                if not has_length:
                    headers["content-length"] = str(len(body_bytes))

            status = payload.get("status") or 200
            await route.fulfill(status=status, headers=headers, body=body_bytes)
            return

        if allow_network_fallback:
            await route.continue_()
            return

        message = f"Offline bundle missing resource for {request.method} {request.url}"
        logger.warning(message)
        await route.fulfill(status=504, body=message)

    def _load_body(self, payload: Dict[str, Any]) -> Optional[bytes]:
        body_path = payload.get("body_path")
        if not body_path:
            size = payload.get("body_size")
            if size:
                logger.debug("Recorded size without body path for %s", payload.get("url"))
            return b"" if size == 0 else None

        target = self.bundle_path / body_path
        if not target.exists():
            logger.warning("Missing body file %s", target)
            return None

        return target.read_bytes()

    def _storage_state_path(self) -> Optional[Path]:
        storage_dir = self.bundle_path / "storage"
        storage_state = storage_dir / "storage_state.json"
        return storage_state if storage_state.exists() else None

    async def _safe_post_data(self, request) -> Optional[str]:
        accessor = getattr(request, "post_data", None)
        try:
            if callable(accessor):
                try:
                    return await accessor()
                except TypeError:
                    return accessor()
            return accessor
        except Exception:
            return None

    @staticmethod
    def _resource_key(resource: Dict[str, Any]) -> Tuple[str, str, str]:
        return (
            resource.get("method") or "GET",
            resource.get("url") or "",
            resource.get("post_data") or "",
        )

    @staticmethod
    def _resolve_manifest(bundle_path: Path) -> Path:
        manifest = bundle_path / "manifest.json"
        if manifest.exists():
            return manifest

        # If this is a resources/ folder, walk up
        if bundle_path.name == "resources":
            parent_manifest = bundle_path.parent / "manifest.json"
            if parent_manifest.exists():
                return parent_manifest
            bundle_path = bundle_path.parent

        # If this directory has timestamped subdirectories, pick the newest
        candidates = sorted(
            [p for p in bundle_path.iterdir() if p.is_dir()],
            reverse=True,
        )
        for candidate in candidates:
            manifest = candidate / "manifest.json"
            if manifest.exists():
                return manifest

        return manifest  # fall back to initial attempt for error reporting


async def _cli(bundle_path: Path, *, headless: bool, allow_fallback: bool) -> None:
    from playwright.async_api import async_playwright

    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

    bundle = ReplayBundle(bundle_path)

    async with async_playwright() as pw:
        launch_kwargs: Dict[str, Any] = {"headless": headless}
        channel = (
            os.environ.get("REPLAY_BROWSER_CHANNEL")
            or os.environ.get("RECORDER_BROWSER_CHANNEL")
        )
        if channel:
            launch_kwargs["channel"] = channel

        browser = await pw.chromium.launch(**launch_kwargs)
        context = await bundle.build_context(browser, allow_network_fallback=allow_fallback)
        page = await context.new_page()
        start_url = bundle.guess_start_url() or "about:blank"
        logger.info("Opening %s", start_url)
        await page.goto(start_url)
        await asyncio.Event().wait()


def main():
    parser = argparse.ArgumentParser(description="Replay a captured browser bundle offline")
    parser.add_argument("bundle", type=Path, help="Path to the capture bundle directory")
    parser.add_argument("--headless", action="store_true", help="Run browser in headless mode")
    parser.add_argument(
        "--allow-network-fallback",
        action="store_true",
        help="If set, allow requests missing from the bundle to hit the live network",
    )

    args = parser.parse_args()
    asyncio.run(
        _cli(
            args.bundle.expanduser().resolve(),
            headless=args.headless,
            allow_fallback=args.allow_network_fallback,
        )
    )


if __name__ == "__main__":
    main()
