import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from db.step import StepManager
import typer
from playwright.async_api import Browser, BrowserContext, async_playwright

from environments.replay import TaskStepExecutor


logger = logging.getLogger(__name__)


class ReplayBundle:
    """Replay previously captured browsing resources using HAR files."""

    def __init__(self, bundle_path: Path):
        bundle_path = bundle_path.expanduser().resolve()
        manifest_path = self._resolve_manifest(bundle_path)

        self.bundle_path = manifest_path.parent
        self.manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        if "environment" not in self.manifest or "task" not in self.manifest:
            raise ValueError("Invalid manifest: missing required fields")

        self.environment = self.manifest["environment"]
        self.task_id: Optional[int] = self.manifest["task"]["id"]

        logger.info(
            "Loaded bundle %s",
            bundle_path,
        )

    def guess_start_url(self) -> Optional[str]:
        """Extract the initial navigation URL from the manifest resources."""
        resources = self.manifest.get("resources", [])
        for resource in resources:
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
        include_storage_state: bool = False,
    ) -> BrowserContext:
        """Build a browser context with HAR-based replay."""
        context_config = dict(self.environment.get("context_config") or {})
        if include_storage_state:
            storage_state_path = self._storage_state_path()
            if storage_state_path:
                context_config["storage_state"] = str(storage_state_path)
            else:
                logger.warning("Storage state file not found, using empty state")
                context_config["storage_state"] = "{}"

        context = await browser.new_context(**context_config)

        # Use HAR replay
        har_path = self.bundle_path / "recording.har"
        if har_path.exists():
            logger.info("Using HAR replay from %s", har_path)
            await context.route_from_har(
                str(har_path),
                not_found="fallback" if allow_network_fallback else "abort",
                update=False,  # Don't update the HAR, just replay
            )
        else:
            raise FileNotFoundError(
                f"HAR file not found at {har_path}. Cannot replay without HAR file."
            )

        return context

    def _storage_state_path(self) -> Optional[Path]:
        storage_dir = self.bundle_path / "storage"
        storage_state = storage_dir / "storage_state.json"
        return storage_state if storage_state.exists() else None

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

        raise FileNotFoundError(f"No manifest found at {bundle_path}")


async def _cli(
    bundle_path: Path,
    *,
    channel: str,
    headless: bool,
    allow_fallback: bool,
    run_human_trajectory: bool,
    exit_on_completion: bool,
    include_storage_state: bool,
) -> None:
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    bundle = ReplayBundle(bundle_path)

    trajectory_steps = (
        StepManager.get_instance().get_steps_by_task_id(bundle.task_id)
        if run_human_trajectory
        else []
    )

    async with async_playwright() as pw:
        launch_kwargs: Dict[str, Any] = {"headless": headless, "channel": channel}
        browser = await pw.chromium.launch(**launch_kwargs)
        context = await bundle.build_context(
            browser,
            allow_network_fallback=allow_fallback,
            include_storage_state=include_storage_state,
        )
        page = await context.new_page()
        start_url = bundle.guess_start_url() or "about:blank"
        logger.info("Opening %s", start_url)
        await page.goto(start_url)

        if trajectory_steps:
            executor = TaskStepExecutor(
                trajectory=trajectory_steps,
                run_human_trajectory=run_human_trajectory,
            )
            await executor.run(page)
            if exit_on_completion:
                await asyncio.sleep(1)
                logger.info("Trajectory completed, exiting as requested")
                return

        await asyncio.Event().wait()


app = typer.Typer(help="Replay a captured browser bundle offline")


@app.command()
def main(
    bundle: Path = typer.Argument(..., help="Path to the capture bundle directory"),
    headless: bool = typer.Option(False, help="Run browser in headless mode"),
    # Default to chrome channel for consistent behavior with recording
    channel: str = typer.Option("chrome", help="Browser channel to use for replay"),
    allow_network_fallback: bool = typer.Option(
        False, help="Allow requests missing from the HAR to hit the live network"
    ),
    exit_on_completion: bool = typer.Option(
        False, help="Exit the program after completing the replay"
    ),
    include_storage_state: bool = typer.Option(
        False,
        help="Include the storage state in the replay (means any collected signed in cookies/storage info from trajectory would be included in launch)",
    ),
    run_human_trajectory: bool = typer.Option(
        False, help="Replay timing with human-like pacing"
    ),
):
    """Replay a captured browser bundle offline using HAR files."""
    asyncio.run(
        _cli(
            bundle.expanduser().resolve(),
            headless=headless,
            channel=channel,
            allow_fallback=allow_network_fallback,
            run_human_trajectory=run_human_trajectory,
            exit_on_completion=exit_on_completion,
            include_storage_state=include_storage_state,
        )
    )


def _main():
    app()


if __name__ == "__main__":
    app()
