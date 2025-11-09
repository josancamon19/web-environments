import asyncio
import base64
import json
import logging
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, parse_qsl
from typing import Any, Dict, List, Optional, Set

from environments.utils.lm_match import retrieve_best_request_match

from scripts.postprocessing._4_determine_ignore import should_ignore_url
import typer
from playwright.async_api import (
    Browser,
    BrowserContext,
    Request,
    Route,
    async_playwright,
)
from db.step import StepManager

from environments.replay import TaskStepExecutor
from config.storage import DATA_DIR

logger = logging.getLogger(__name__)


def most_relevant_entry(entries: List[dict], request_url: str) -> dict:
    """Find the most relevant entry based on the number of overlapping parameters."""
    if not entries:
        raise ValueError("entries list cannot be empty")

    if len(entries) == 1:
        return entries[0]

    # Parse request URL once for reuse
    request_parsed = urlparse(request_url)
    request_params = dict(parse_qsl(request_parsed.query))
    request_fragment_params = dict(parse_qsl(request_parsed.fragment))

    overlap_entries = []
    for entry in entries:
        entry_url = entry.get("request", {}).get("url", "")
        entry_parsed = urlparse(entry_url)
        entry_params = dict(parse_qsl(entry_parsed.query))
        entry_fragment_params = dict(parse_qsl(entry_parsed.fragment))

        # Count overlapping query parameters
        param_overlap = sum(
            1
            for k in request_params
            if k in entry_params and entry_params[k] == request_params[k]
        )

        # Count overlapping fragment parameters
        fragment_overlap = sum(
            1
            for k in request_fragment_params
            if k in entry_fragment_params
            and entry_fragment_params[k] == request_fragment_params[k]
        )

        overlap_entries.append((entry, param_overlap + fragment_overlap))

    overlap_entries.sort(key=lambda x: x[1], reverse=True)
    return overlap_entries[0][0]


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
        self._har_data: Dict = self._load_har_data()

        # Load ignored URLs from ignored.json
        self._ignored_urls: List[str] = self._load_ignored_urls()

        # Build index for fast lookup: (method, url_base) -> list of entries
        # url_base is scheme + netloc + path (without query/fragment for indexing)
        self._har_index: Dict[tuple[str, str], List[tuple[int, dict]]] = {}
        self._har_full_index: Dict[tuple[str, str], List[tuple[int, dict]]] = {}
        self._har_method_index: Dict[str, List[tuple[int, dict]]] = {}
        self._build_har_index()

        logger.info(
            "Loaded bundle %s with %d HAR entries",
            bundle_path,
            len(self._har_data.get("log", {}).get("entries", [])),
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
            if self._should_ignore_url(request.url):
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

    def _load_har_data(self) -> Dict:
        """Load HAR file data."""
        har_path = self.bundle_path / "recording.har"
        if not har_path.exists():
            raise FileNotFoundError(f"HAR file not found at {har_path}")
        return json.loads(har_path.read_text(encoding="utf-8"))

    def _load_ignored_urls(self) -> List[str]:
        """Load ignored URLs from ignored.json file."""
        # ignored_path = self.bundle_path / "ignored.json"
        # assert ignored_path.exists(), f"ignored.json file not found at {ignored_path}"
        # return json.loads(ignored_path.read_text(encoding="utf-8"))
        return []

    def _should_ignore_url(self, url: str) -> bool:
        """Check if a URL should be ignored based on the ignored.json patterns."""
        if should_ignore_url(url):
            return True
        return any(ignored_pattern in url for ignored_pattern in self._ignored_urls)

    def _build_har_index(self) -> None:
        """Build an index of HAR entries for fast lookup by method and URL base."""
        har_entries = self._har_data.get("log", {}).get("entries", [])

        for idx, entry in enumerate(har_entries):
            request = entry.get("request", {})
            method = request.get("method", "GET").upper()
            url = request.get("url", "")

            if not url:
                continue

            # Parse URL once and extract base (scheme + netloc + path)
            parsed = urlparse(url)
            url_base = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"

            # Index by (method, url_base)
            key = (method, url_base)
            if key not in self._har_index:
                self._har_index[key] = []
            # Store index and entry for later reference
            self._har_index[key].append((idx, entry))

            # Also index by full URL for exact matching
            full_key = (method, url)
            if full_key not in self._har_full_index:
                self._har_full_index[full_key] = []
            self._har_full_index[full_key].append((idx, entry))

        logger.debug(
            "Built HAR index with %d unique (method, url_base) keys and %d full URL keys",
            len(self._har_index),
            len(self._har_full_index),
        )

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
        # self._setup_har_logging(context)

        har_path = self.bundle_path / "recording.har"
        if not har_path.exists():
            raise FileNotFoundError(
                f"HAR file not found at {har_path}. Cannot replay without HAR file."
            )

        logger.info("[HAR REPLAY] Using HAR replay from %s", har_path)

        async def custom_route_handler(route: Route, request: Request) -> None:
            await self.handle_requests_with_no_exact_match(
                route, request, allow_network_fallback
            )

        await context.route("**/*", custom_route_handler)
        await context.route_from_har(str(har_path), not_found="fallback", update=False)
        await context.set_offline(True)

    async def handle_requests_with_no_exact_match(
        self,
        route: Route,
        request: Request,
        allow_network_fallback: bool = False,
    ) -> None:
        if self._should_ignore_url(request.url):
            await route.abort()
            return

        data = await self._obtain_request_candidates(
            request, route, allow_network_fallback
        )
        if not data:
            return

        entries, method, shorter_url = data
        entry = self._select_best_entry(request, entries, method, shorter_url)
        await self._fulfill_request_with_entry_found(
            request, entry, route, allow_network_fallback
        )

    async def _obtain_request_candidates(
        self, request: Request, route: Route, allow_network_fallback: bool = False
    ) -> List[dict] | None:
        method = request.method.upper()
        full_url = request.url
        shorter_url = full_url[:100] + "..." if len(full_url) > 100 else full_url
        request_parsed = urlparse(full_url)
        request_url_base = (
            f"{request_parsed.scheme}://{request_parsed.netloc}{request_parsed.path}"
        )

        index_key = (method, request_url_base)
        candidate_entries = self._har_index.get(index_key, [])

        # TODO: try again, are we matching better? why gpt-5-nano rate limit
        # - is amazon handling requests even with different constructed paths given a single endpoint

        if not candidate_entries:
            ignore_log = [".woff", ".jpg", ".gif", ".png", ".svg", ".ico"]
            if any(ignore_pattern in request.url for ignore_pattern in ignore_log):
                await route.abort()
                return

            candidate_entries = self._fallback_candidates_char_based(full_url, method)
            if not candidate_entries:
                logger.warning(
                    f"No matching HAR entry found for {method} {shorter_url}, aborting",
                )
                await (route.fallback() if allow_network_fallback else route.abort())
                return

        entries = [entry for _, entry in candidate_entries]
        return entries, method, shorter_url

    def _fallback_candidates_char_based(self, full_url: str, method: str) -> List[dict]:
        """
        Find all HAR entries with the same domain and method, and select the best match based on the number of characters in the URL.
        e.g. amazon requests css/js/media for the same endpoint in different order, so char based helps with matching.
        """
        target_chars = {}
        for char in full_url:
            target_chars[char] = target_chars.get(char, 0) + 1

        # Find all HAR entries with the same domain and method
        same_domain_candidates = []
        har_entries = self._get_har_matches_by_base_url(full_url, method)

        for idx, entry in enumerate(har_entries):
            entry_url = entry.get("request", {}).get("url", "")
            # Count character occurrences in entry URL
            entry_chars = {}
            for char in entry_url:
                entry_chars[char] = entry_chars.get(char, 0) + 1

            # Calculate match score: count how many target characters are available
            match_score = 0
            matches_all = True
            for char, count in target_chars.items():
                available = entry_chars.get(char, 0)
                if available >= count:
                    match_score += count
                else:
                    match_score += available
                    matches_all = False
            data = {
                "idx": idx,
                "entry": entry,
                "match_score": match_score,
                "matches_all": matches_all,
            }
            same_domain_candidates.append(data)

        same_domain_candidates.sort(key=lambda x: x["match_score"], reverse=True)

        perfect_match = next(
            (c for c in same_domain_candidates if c["matches_all"]), None
        )

        if perfect_match:
            return [(perfect_match["idx"], perfect_match["entry"])]

        top_k = min(5, len(same_domain_candidates))
        top_candidates = same_domain_candidates[:top_k]
        return [(c["idx"], c["entry"]) for c in top_candidates]

    def _select_best_entry(
        self, request: Request, entries: List[dict], method: str, shorter_url: str
    ) -> dict:
        if len(entries) == 1:
            return entries[0]

        logger.info(
            f"Multiple HAR candidates ({len(entries)}) found for {method} {shorter_url}, using LM matching",
        )
        try:
            post_data = request.post_data
        except Exception:
            post_data = None

        # TODO: any obvious way to simplify and call less this? any heuristics? check the LM reasoning response.
        # TODO: add some caching here in a JSON of matches that can be distributed later
        # - how accurate is this? should barely fail
        # ----- 1)
        # TODO: now what websites are manual navigation failing? or collection
        # TODO: coursera input for email changes?
        # TODO: shouldn't replay be just making click, not handling navigations themselves?

        # 1. ignore.py
        # 2. lm match, traces, and find basic heuristics

        idx = retrieve_best_request_match(
            target_request=request.__dict__, candidates=entries, post_data=post_data
        )
        return entries[idx]

    async def _fulfill_request_with_entry_found(
        self,
        request: Request,
        entry: dict,
        route: Route,
        allow_network_fallback: bool = False,
    ) -> None:
        # TODO: ensure this matches as expected
        response = entry.get("response", {})
        status = response.get("status", 200)
        headers = {h["name"]: h["value"] for h in response.get("headers", [])}
        content = response.get("content", {})

        # Handle different response body types
        body = None
        if "text" in content:
            text = content["text"]
            encoding = content.get("encoding", "")

            if encoding == "base64":
                # Decode base64 to bytes for binary content
                try:
                    body = base64.b64decode(text)
                except Exception as exc:
                    logger.warning(
                        "Failed to decode base64 body for %s: %s, falling back",
                        request.url,
                        exc,
                    )
                    if allow_network_fallback:
                        await route.fallback()
                    else:
                        await route.abort()
                    return
            else:
                # Use text as-is for text responses
                body = text

        await route.fulfill(
            status=status,
            headers=headers,
            body=body,
            content_type=content.get("mimeType"),
        )

    def _get_har_matches_by_base_url(self, full_url: str, method: str) -> List[dict]:
        har_entries = self._har_data.get("log", {}).get("entries", [])
        base_url = urlparse(full_url).netloc
        matches = []
        for idx, entry in enumerate(har_entries):
            if idx in self._consumed_har_indices:
                continue

            entry_request = entry.get("request", {})
            entry_method = entry_request.get("method", "GET").upper()
            entry_url = entry_request.get("url", "")

            if not entry_url or entry_method != method:
                continue

            if urlparse(entry_url).netloc == base_url:
                matches.append(entry)
        return matches

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
        await page.goto(start_url, timeout=60000)

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
