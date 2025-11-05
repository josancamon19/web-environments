import asyncio
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Set

from db.step import StepManager
import typer
from playwright.async_api import (
    Browser,
    BrowserContext,
    Request,
    Route,
    async_playwright,
)

from environments.replay import TaskStepExecutor
from config.storage import DATA_DIR
from environments.fuzzy_match import (
    find_fuzzy_har_match,
)

logger = logging.getLogger(__name__)

IGNORED_PATTERNS = [
    "google-analytics",
    "googleads",
    "google-tag-manager",
    "doubleclick.net",
    "mixpanel",
    "ingest.sentry.io",
    "facebook.com/privacy_sandbox/pixel",
    "cloudflareinsights.com",
    "google.com/ccm/collect",
    "facebook.com/tr/",
    "googletagmanager.com",
    "amazon.com/1/events/",
    "amazon-adsystem.com",
    "amazon.com/*/uedata",
    "fls-na.amazon.com",
    "amazon.com/empty.gif",
]

_compiled_patterns = []
for pattern in IGNORED_PATTERNS:
    if "*" in pattern:
        # Convert wildcard pattern to regex: * matches any characters except nothing
        regex_pattern = re.escape(pattern).replace(r"\*", r"[^/]+")
        _compiled_patterns.append(("regex", re.compile(regex_pattern, re.IGNORECASE)))
    else:
        _compiled_patterns.append(("substring", pattern.lower()))


def should_ignore_url(url: str):
    """Check if URL should be ignored based on IGNORED_PATTERNS (supports wildcards)."""
    url_lower = url.lower()
    for pattern_type, pattern in _compiled_patterns:
        if pattern_type == "substring":
            if pattern in url_lower:
                return True
        elif pattern_type == "regex":
            if pattern.search(url_lower):
                return True
    return False


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

        # Track consumed HAR entries to avoid reusing them (important for sequential requests)
        self._consumed_har_indices: Set[int] = set()

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

    def _setup_har_logging(self, context: BrowserContext) -> None:
        """Set up network event listeners to log requests not found in HAR."""

        async def log_request_failed(request):
            if should_ignore_url(request.url):
                return

            logger.warning(
                "⚠️  Request FAILED (not in HAR): %s %s [%s]",
                request.method,
                request.url[:100] + "..." if len(request.url) > 100 else request.url,
                request.resource_type,
            )

            # Capture full request details to file for debugging
            await self._save_failed_request_to_file(request)

        async def log_request_finished(request):
            response = await request.response()
            if response:
                if response.from_service_worker:
                    logger.info("Request served from service worker: %s", request.url)
            else:
                logger.warning(
                    "⚠️  Request completed but no response: %s %s",
                    request.method,
                    request.url,
                )

        # Set up event listeners for network monitoring
        # context.on("request", lambda req: logger.info("→ Request: %s %s", req.method, req.url))
        context.on("requestfailed", log_request_failed)
        context.on("requestfinished", log_request_finished)

    async def _save_failed_request_to_file(self, request: Request) -> None:
        """Save failed request details to a temporary file for comparison."""
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            filename = f"failed_request_{timestamp}.txt"
            # Extract task_id from bundle_path, assumed structure like data/captures/task_1
            filepath = Path(DATA_DIR) / "debug" / f"task_{self.task_id}" / filename
            filepath.parent.mkdir(parents=True, exist_ok=True)

            # Collect all request details
            details = {
                "timestamp": timestamp,
                "url": request.url,
                "method": request.method,
                "resource_type": request.resource_type,
                "headers": dict(request.headers),
                "post_data": None,
                "failure": request.failure,
            }

            # Try to get POST data if available
            try:
                post_data = request.post_data
                if post_data:
                    details["post_data"] = post_data
                    details["post_data_length"] = len(post_data)
            except Exception:
                pass

            # Format as readable text
            content_lines = [
                "=" * 80,
                f"FAILED REQUEST DETAILS - {timestamp}",
                "=" * 80,
                "",
                f"URL: {details['url']}",
                f"Method: {details['method']}",
                f"Resource Type: {details['resource_type']}",
                f"Failure: {details['failure']}",
                "",
                "HEADERS:",
                "-" * 80,
            ]

            for key, value in details["headers"].items():
                content_lines.append(f"{key}: {value}")

            if details["post_data"]:
                content_lines.extend(
                    [
                        "",
                        "POST DATA:",
                        "-" * 80,
                        f"Length: {details['post_data_length']} bytes",
                        "",
                        details["post_data"],
                    ]
                )

            content_lines.extend(["", "=" * 80, ""])

            filepath.write_text("\n".join(content_lines))

        except Exception as exc:
            logger.error(f"Failed to save request details: {exc}")

    def _load_har_data(self) -> Optional[Dict]:
        """Load and cache HAR file data."""
        if not hasattr(self, "_har_data"):
            har_path = self.bundle_path / "recording.har"
            if har_path.exists():
                self._har_data = json.loads(har_path.read_text(encoding="utf-8"))
            else:
                raise FileNotFoundError(f"HAR file not found at {har_path}")

        return self._har_data

    async def build_context(
        self,
        browser: Browser,
        *,
        allow_network_fallback: bool = False,
        include_storage_state: bool = False,
    ) -> BrowserContext:
        """Build a browser context with HAR-based replay."""
        context_config = self.get_context_config(
            include_storage_state=include_storage_state
        )
        context = await browser.new_context(**context_config)
        await self.configure_context(
            context, allow_network_fallback=allow_network_fallback
        )
        return context

    def get_context_config(
        self, *, include_storage_state: bool = False
    ) -> Dict[str, Any]:
        """Prepare context configuration, optionally including storage state."""
        context_config = dict(self.environment.get("context_config") or {})

        if include_storage_state:
            storage_state_path = self._storage_state_path()
            if storage_state_path:
                context_config["storage_state"] = str(storage_state_path)
            else:
                logger.warning("Storage state file not found, using empty state")
                context_config["storage_state"] = "{}"

        return context_config

    async def configure_context(
        self,
        context: BrowserContext,
        *,
        allow_network_fallback: bool = False,
    ) -> None:
        """Configure an existing browser context with HAR replay and routing."""
        self._setup_har_logging(context)

        har_path = self.bundle_path / "recording.har"
        if not har_path.exists():
            raise FileNotFoundError(
                f"HAR file not found at {har_path}. Cannot replay without HAR file."
            )

        logger.info("[HAR REPLAY] Using HAR replay from %s", har_path)
        await context.route_from_har(
            str(har_path),
            not_found="fallback" if allow_network_fallback else "abort",
            update=False,  # Don't update the HAR, just replay
        )

        # Add custom route handler AFTER HAR routing (so it gets priority - LIFO)
        await context.route(
            "**/*", lambda route, request: self.handle_routes_manually(route, request)
        )

    async def handle_routes_manually(self, route: Route, request: Request) -> None:
        # TODO: do we need to obsfucate in a more clever way?
        # - ?? Normalize JSON (remove volatile fields; sort keys) and hash; tolerate multipart boundary changes; ignore known nonce/timestamp params.
        # TODO: what if the request is sent twice, we'll be selecting the first one all the time.
        # semhash matching URL at times if they vary?

        # TODO: this requires LM postprocessing selection of URL's to match or some dumb way for all POST? or smth
        # TODO: why when collecting, increasing/decreasing cart stuff fails
        # TODO: some assets in GET are also dynamic?, bunch of js/stylesheets are not found in HAR
        # TODO: websockets? like e.g. ChatGPT doesn't allow for collecting anything

        # 1. make amazon sign in work seamless
        # 2. make add to cart, remove from cart work as well
        # 3. make other interactions work well here,

        # - try any URL not body matching, but use them only once, so cache the ones consumed already.
        # - are we getting different URLs even? when it should be the same?

        urls_to_ignore_post_data = {
            "https://www.amazon.com/ax/claim",
            "https://www.amazon.com/aaut/verify/ap",
            "https://www.amazon.com/ap/signin",
        }

        if should_ignore_url(request.url):
            await route.abort()
            return

        har_data = self._load_har_data()

        # 2. Handle POST requests with special URL-only matching (no fuzzy matching)
        if request.method == "POST":
            # Check if this is a POST endpoint where we should ignore body differences
            for base_url in urls_to_ignore_post_data:
                if not request.url.startswith(base_url):
                    continue
                har_entries = har_data.get("log", {}).get("entries", [])
                # TODO: consume the index, and ignore next time
                entry = next(
                    (
                        entry
                        for entry in har_entries
                        if entry.get("request", {}).get("method") == "POST"
                        and entry.get("request", {}).get("url") == request.url
                    ),
                    None,
                )
                shorter_url = (
                    request.url[:100] + "..." if len(request.url) > 100 else request.url
                )
                if entry:
                    logger.info(
                        "✅ Found matching HAR entry (POST, URL-only) for %s",
                        shorter_url,
                    )

                    response = entry.get("response", {})
                    headers = {
                        h["name"]: h["value"] for h in response.get("headers", [])
                    }
                    content = response.get("content", {})
                    body = None if "text" not in content else content["text"]

                    await route.fulfill(
                        status=response.get("status", 200), headers=headers, body=body
                    )
                    return

                logger.warning(
                    "⚠️  No matching HAR entry found for POST %s, aborting", shorter_url
                )
                await route.abort()
                return

            await route.fallback()
            return

        # 3. Handle GET requests with fuzzy URL matching for static assets
        if request.method == "GET":
            # Only apply fuzzy matching to static assets where variations are expected
            # font: .woff vs .woff2 differences
            # image: responsive image sizes, cache busters
            # stylesheet/script: bundled resources with dynamic names
            # Note: xhr/fetch excluded - they need exact matches or should fail
            fuzzy_match_types = {"stylesheet", "script", "image", "font"}

            if request.resource_type in fuzzy_match_types:
                # First check if HAR replay will handle it (exact match exists)
                # We do this by attempting fuzzy match which tries exact first
                match_result = find_fuzzy_har_match(
                    har_data,
                    self._consumed_har_indices,
                    request.url,
                    "GET",
                    request.resource_type,
                )

                if match_result:
                    idx, entry = match_result
                    self._consumed_har_indices.add(idx)

                    response = entry.get("response", {})
                    status = response.get("status", 200)

                    # Only fulfill if it was a successful response
                    if status < 400:
                        headers = {
                            h["name"]: h["value"] for h in response.get("headers", [])
                        }
                        content = response.get("content", {})

                        # Handle different content encodings
                        body = None
                        if "text" in content:
                            body = content["text"]

                        har_url = entry.get("request", {}).get("url", "")
                        if har_url != request.url:
                            logger.info(
                                "✅ Fuzzy matched HAR entry for %s [%s] -> %s",
                                request.resource_type,
                                request.url[:80] + "..."
                                if len(request.url) > 80
                                else request.url,
                                har_url[:80] + "..." if len(har_url) > 80 else har_url,
                            )

                        await route.fulfill(status=status, headers=headers, body=body)
                        return

        # 4. Fallback to HAR replay for everything else (including xhr/fetch)
        await route.fallback()

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
