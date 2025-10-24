import asyncio
import json
import logging
import os
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import typer
from playwright.async_api import Browser, BrowserContext, Route

from db.database import Database
from environments.replay import StepEntry, TaskStepExecutor


logger = logging.getLogger(__name__)


class ReplayBundle:
    """Replay previously captured browsing resources."""

    def __init__(self, bundle_path: Path, log_dir: Optional[Path] = None):
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
        self.task_info: Dict[str, Any] = self.manifest.get("task") or {}
        self.task_id: Optional[int] = self.task_info.get("id")
        self._payloads: Dict[Tuple[str, str, str], list[Dict[str, Any]]] = defaultdict(
            list
        )
        self._payload_indices: Dict[Tuple[str, str, str], int] = defaultdict(int)

        # Set up logging for cached vs not-found URLs
        self.log_dir = log_dir
        self._cached_urls: set[str] = set()
        self._not_found_urls: set[str] = set()

        for resource in self.resources:
            key = self._resource_key(resource)
            self._payloads[key].append(resource)

        logger.info(
            "Loaded bundle %s with %s recorded resources",
            bundle_path,
            len(self.resources),
        )

    def load_steps(self) -> list[StepEntry]:
        if not self.task_id:
            logger.warning(
                "Bundle manifest does not include a task id; skipping step replay"
            )
            return []

        db = Database.get_instance()
        conn = db.get_connection()
        if conn is None:
            logger.error(
                "Database connection unavailable; cannot load steps for task %s",
                self.task_id,
            )
            return []

        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, event_type, event_data, timestamp FROM steps WHERE task_id = ? ORDER BY id",
            (self.task_id,),
        )

        steps: list[StepEntry] = []
        for row in cursor.fetchall():
            raw_event_data = row[2]
            parsed_event: Dict[str, Any] = {}
            if raw_event_data:
                try:
                    parsed_event = json.loads(raw_event_data)
                except json.JSONDecodeError:
                    logger.debug("Failed to decode event data for step %s", row[0])
            steps.append(
                StepEntry(
                    id=row[0],
                    event_type=row[1] or "",
                    event_data=parsed_event,
                    timestamp=row[3],
                )
            )

        logger.info(
            "Loaded %d steps from database for task %s", len(steps), self.task_id
        )
        return steps

    def guess_start_url(self) -> Optional[str]:
        for resource in self.resources:
            if (
                resource.get("resource_type") == "document"
                and resource.get("status", 200) < 400
            ):
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

        entries = self._payloads.get(key)
        payload: Optional[Dict[str, Any]] = None

        if entries:
            idx = self._payload_indices[key]
            if idx < len(entries):
                payload = entries[idx]
                self._payload_indices[key] = idx + 1
            elif request.method.upper() == "GET":
                payload = entries[-1]
                logger.debug(
                    "Reusing cached GET response for %s (recorded %d uses)",
                    request.url,
                    len(entries),
                )
            else:
                payload = entries[-1]
                logger.info(
                    "Reusing last response for %s %s beyond recorded count",
                    request.method,
                    request.url,
                )

        if payload:
            # Log cached URL
            if self.log_dir and request.url not in self._cached_urls:
                self._cached_urls.add(request.url)

            body_bytes = self._load_body(payload)
            headers = dict(payload.get("response_headers") or {})
            if body_bytes is not None:
                has_length = any(k.lower() == "content-length" for k in headers)
                if not has_length:
                    headers["content-length"] = str(len(body_bytes))

            status = payload.get("status") or 200
            await route.fulfill(status=status, headers=headers, body=body_bytes)
            return

        # Log not-found URL
        if self.log_dir and request.url not in self._not_found_urls:
            self._not_found_urls.add(request.url)

        if allow_network_fallback:
            await route.continue_()
            return

        message = f"Offline bundle missing resource for {request.method} {request.url}"
        logger.warning(message)
        await route.fulfill(status=504, body=message)

    def flush_logs(self) -> None:
        """Write cached and not-found URLs to log files."""
        if not self.log_dir:
            return

        self.log_dir.mkdir(parents=True, exist_ok=True)

        if self._cached_urls:
            cached_log_path = self.log_dir / "cached.log"
            with open(cached_log_path, "w", encoding="utf-8") as f:
                for url in sorted(self._cached_urls):
                    f.write(f"{url}\n")
            logger.info(
                "Wrote %d cached URLs to %s", len(self._cached_urls), cached_log_path
            )

        if self._not_found_urls:
            not_found_log_path = self.log_dir / "not-found.log"
            with open(not_found_log_path, "w", encoding="utf-8") as f:
                for url in sorted(self._not_found_urls):
                    f.write(f"{url}\n")
            logger.info(
                "Wrote %d not-found URLs to %s",
                len(self._not_found_urls),
                not_found_log_path,
            )

    def _load_body(self, payload: Dict[str, Any]) -> Optional[bytes]:
        body_path = payload.get("body_path")
        if not body_path:
            size = payload.get("body_size")
            if size:
                logger.debug(
                    "Recorded size without body path for %s", payload.get("url")
                )
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


async def _cli(
    bundle_path: Path,
    *,
    headless: bool,
    allow_fallback: bool,
    run_human_trajectory: bool,
) -> None:
    from playwright.async_api import async_playwright

    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

    bundle = ReplayBundle(bundle_path)
    steps = bundle.load_steps() if run_human_trajectory else []

    async with async_playwright() as pw:
        launch_kwargs: Dict[str, Any] = {"headless": headless}
        # Default to chrome channel for consistent behavior with recording
        channel = (
            # TODO: Requires the same environment variable as recording?
            os.environ.get("REPLAY_BROWSER_CHANNEL")
            or os.environ.get("RECORDER_BROWSER_CHANNEL")
            or "chrome"
        )
        launch_kwargs["channel"] = channel
        logger.info("Launching browser with channel: %s", channel)

        browser = await pw.chromium.launch(**launch_kwargs)
        context = await bundle.build_context(
            browser, allow_network_fallback=allow_fallback
        )
        page = await context.new_page()
        start_url = bundle.guess_start_url() or "about:blank"
        logger.info("Opening %s", start_url)
        await page.goto(start_url)
        if steps:
            executor = TaskStepExecutor(
                steps, run_human_trajectory=run_human_trajectory
            )
            await executor.run(page)
        await asyncio.Event().wait()


app = typer.Typer(help="Replay a captured browser bundle offline")


@app.command()
def main(
    bundle: Path = typer.Argument(..., help="Path to the capture bundle directory"),
    headless: bool = typer.Option(False, help="Run browser in headless mode"),
    allow_network_fallback: bool = typer.Option(
        True, help="Allow requests missing from the bundle to hit the live network"
    ),
    run_human_trajectory: bool = typer.Option(
        False, help="Replay timing with human-like pacing"
    ),
):
    """Replay a captured browser bundle offline."""
    asyncio.run(
        _cli(
            bundle.expanduser().resolve(),
            headless=headless,
            allow_fallback=allow_network_fallback,
            run_human_trajectory=run_human_trajectory,
        )
    )


def _main():
    app()


if __name__ == "__main__":
    app()
