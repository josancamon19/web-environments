import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

import typer
from playwright.async_api import Browser, BrowserContext

from db.database import Database
from environments.replay import StepEntry, TaskStepExecutor


logger = logging.getLogger(__name__)


class ReplayBundle:
    """Replay a previously captured browsing session."""

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
        self.har_path = self._resolve_har_path()
        self.environment = self.manifest.get("context", {})
        self.task_info: Dict[str, Any] = self.manifest.get("task") or {}
        self.task_id: Optional[int] = self.task_info.get("id")
        self.log_dir = log_dir
        self._har_cache: Optional[Dict[str, Any]] = None

        logger.info("Loaded bundle %s", bundle_path)

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
        entries = self._har_entries()
        if not entries:
            return None
        try:
            first = entries[0]
            return first.get("request", {}).get("url")
        except Exception:
            return None

    async def build_context(
        self,
        browser: Browser,
        *,
        allow_network_fallback: bool = False,
    ) -> BrowserContext:
        options = dict(self.environment.get("options") or {})
        launch_meta = self.environment.get("launch") or {}

        # Remove properties that are only valid during recording
        options.pop("record_video_dir", None)
        options.pop("record_video_size", None)
        options.pop("mode", None)
        options.pop("channel", None)
        options.pop("user_data_dir", None)

        storage_state_path = self._storage_state_path()

        if storage_state_path:
            try:
                options["storage_state"] = json.loads(
                    storage_state_path.read_text(encoding="utf-8")
                )
            except Exception as exc:
                logger.warning(
                    "Failed to load storage state from %s: %s", storage_state_path, exc
                )

        context = await browser.new_context(**options)
        await self.attach(context, allow_network_fallback=allow_network_fallback)

        if launch_meta.get("browser_channel"):
            logger.info(
                "Replay using channel %s with %d HAR entries",
                launch_meta["browser_channel"],
                len(self._har_entries()),
            )

        return context

    async def attach(
        self,
        context: BrowserContext,
        *,
        allow_network_fallback: bool = False,
    ) -> None:
        if getattr(context, "_har_attached", False):
            return

        not_found = "fallback" if allow_network_fallback else "abort"
        await context.route_from_har(
            str(self.har_path),
            url="**/*",
            not_found=not_found,
        )
        setattr(context, "_har_attached", True)

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

    def _resolve_har_path(self) -> Path:
        har_rel = self.manifest.get("har_path")
        if not har_rel:
            raise FileNotFoundError("Capture manifest missing 'har_path'")
        candidate = self.bundle_path / har_rel
        if not candidate.exists():
            raise FileNotFoundError(f"HAR file not found at {candidate}")
        return candidate

    def _storage_state_path(self) -> Optional[Path]:
        storage_rel = self.manifest.get("storage_state")
        if not storage_rel:
            return None
        candidate = self.bundle_path / storage_rel
        return candidate if candidate.exists() else None

    def _har_entries(self) -> list[Dict[str, Any]]:
        if self._har_cache is None:
            try:
                data = json.loads(self.har_path.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning("Failed to read HAR file %s: %s", self.har_path, exc)
                data = {}
            self._har_cache = data if isinstance(data, dict) else {}

        log = self._har_cache.get("log") if isinstance(self._har_cache, dict) else None
        entries = log.get("entries") if isinstance(log, dict) else None
        return entries if isinstance(entries, list) else []


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
