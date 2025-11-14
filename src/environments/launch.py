import asyncio
import base64
import hashlib
import json
import logging
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from typing import Any, Dict, List, Optional, Set

from config.browser_config import BROWSER_ARGS, CONTEXT_CONFIG, IGNORE_DEFAULT_ARGS
from environments.utils.lm_match import retrieve_best_request_match
from environments.models import (
    CandidateEntry,
    CandidateEntryMetadata,
    HarEntry,
    parse_har_entry,
)

from scripts.postprocessing._4_determine_ignore import should_ignore_url
import typer
from rebrowser_playwright.async_api import (
    Browser,
    BrowserContext,
    Request,
    Route,
    async_playwright,
)
from db.step import StepManager

from environments.replay import TaskStepExecutor
from config.storage import DATA_DIR
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


class ReplayBundle:
    """Replay previously captured browsing resources using HAR files."""

    def __init__(self, bundle_path: Path, ignore_cache: bool = False):
        bundle_path = bundle_path.expanduser().resolve()
        manifest_path = self._resolve_manifest(bundle_path)

        self.bundle_path = manifest_path.parent
        self.manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.ignore_cache = ignore_cache

        if "environment" not in self.manifest or "task" not in self.manifest:
            raise ValueError("Invalid manifest: missing required fields")

        self.environment = self.manifest["environment"]
        self.task_id: Optional[int] = self.manifest["task"]["id"]

        # Track consumed HAR entries to avoid reusing them (important for sequential requests)
        self._consumed_har_indices: Set[int] = set()
        self._har_entries: List[HarEntry] = self._load_har_data()
        self._ignored_urls: List[str] = self._load_ignored_urls()  # ignored.json

        logger.info(
            "Loaded bundle %s with %d HAR entries",
            bundle_path,
            len(self._har_entries),
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

        async def log_request_failed(request: Request) -> None:
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

        async def log_request_finished(request: Request) -> None:
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

    def _load_har_data(self) -> List[HarEntry]:
        """Load HAR file data."""
        har_path = self.bundle_path / "recording.har"
        if not har_path.exists():
            raise FileNotFoundError(f"HAR file not found at {har_path}")
        har_data = json.loads(har_path.read_text(encoding="utf-8"))
        har_entries = har_data.get("log", {}).get("entries", [])
        entries: List[HarEntry] = [parse_har_entry(entry) for entry in har_entries]
        return entries

    def _load_ignored_urls(self) -> List[str]:
        """Load ignored URLs from ignored.json file."""
        ignored_path = self.bundle_path / "ignored.json"
        assert ignored_path.exists(), f"ignored.json file not found at {ignored_path}"
        return json.loads(ignored_path.read_text(encoding="utf-8"))

    def _should_ignore_url(self, url: str) -> bool:
        """Check if a URL should be ignored based on the ignored.json patterns."""
        if should_ignore_url(url):
            return True
        return any(ignored_pattern in url for ignored_pattern in self._ignored_urls)

    @staticmethod
    def _get_url_base(url: str) -> str:
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"

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
        context = await browser.new_context(**context_config, bypass_csp=True)
        await self.configure_context(
            context, allow_network_fallback=allow_network_fallback
        )
        return context

    def get_context_config(
        self, *, include_storage_state: bool = False
    ) -> Dict[str, Any]:
        """Prepare context configuration, optionally including storage state."""
        context_config = dict(
            self.environment.get("context_config") or {**CONTEXT_CONFIG}
        )

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

        await context.set_offline(True)
        await context.route("**/*", custom_route_handler)
        await context.route_from_har(str(har_path), not_found="fallback", update=False)
        await context.set_offline(True)

    async def handle_requests_with_no_exact_match(
        self,
        route: Route,
        request: Request,
        allow_network_fallback: bool = False,
    ) -> None:
        # TODO: amazon trajectory fail after sign in? says "no internet" for a second, then refresh page works.
        if self._should_ignore_url(request.url):
            await route.abort()
            return

        data = await self._obtain_request_candidates(
            request, route, allow_network_fallback
        )
        if not data:
            return

        entries, method, shorter_url = data
        entry = await self._select_best_entry(request, entries, method, shorter_url)
        if not entry:
            await route.abort()
            return

        await self._fulfill_request_with_entry_found(
            request, entry, route, allow_network_fallback
        )

    async def _obtain_request_candidates(
        self, request: Request, route: Route, allow_network_fallback: bool = False
    ) -> tuple[List[CandidateEntry], str, str] | None:
        method = request.method.upper()
        full_url = request.url
        shorter_url = full_url[:100] + "..." if len(full_url) > 100 else full_url

        # Find candidate entries by matching method and URL base
        url_base = self._get_url_base(full_url)
        candidate_entries = []
        for idx, entry in enumerate(self._har_entries):
            if idx in self._consumed_har_indices:
                continue
            entry_url_base = self._get_url_base(entry.request.url)
            if entry.request.method.upper() == method and entry_url_base == url_base:
                candidate_entries.append(
                    CandidateEntry(
                        idx=idx,
                        entry=entry,
                        metadata=CandidateEntryMetadata(
                            match_score=0, body_score=0, headers_score=0
                        ),
                    )
                )

        # NOTE: following fields, as well as headers accept and sec-fetch-dest could be used to filter further
        # request_resource_type = request.resource_type
        # is_navigation_request = request.is_navigation_request()

        # Improvements
        # ...
        # Requirements
        # TODO: tell when a page wasn't opened and is still working, return -1 in index or figured based on reading front, should consume indices?
        # TODO: is the model selecting the most chars regardless? or is there some logic that can be extracted
        # koa.com, search bar is a mess, then click to search, chooses the request where you put filters for people, children, etc..., bad.
        # ticketcenter, opened wrong url first, and didn't fail.
        # some websites are very very slow, why? or keep loading during replay for long
        # TODO: log metadata on openai request
        # foxsports loads so many images that require lm_matching

        # =====

        # TODO: test replay.py, and get it to navigate same as human
        # TODO: manage replay to explore with n depth script
        # - consider instead of replay, to simply open every URL navigated, and then expand from there, doesn't really need replay itself
        # TODO: review whole eval pipeline creation, and run 1 eval task offline with an agent.

        # TODO: package this as a library? probably yea.

        if not candidate_entries:
            ignore_log = [".woff", ".jpg", ".gif", ".png", ".svg", ".ico"]
            # NOTE: is this ignorable? like I'm worried this would cause too many requests via LM match unnecessary, wasting tokens and time.
            # this vs `pattern in request.url`
            if any(
                request.url.endswith(ignore_pattern) for ignore_pattern in ignore_log
            ):
                await route.abort()
                return

            candidate_entries = self._fallback_candidates_char_based(
                full_url, method, request
            )
            if not candidate_entries:
                logger.warning(
                    f"No matching HAR entry found for {method} {shorter_url}, aborting",
                )
                await (route.fallback() if allow_network_fallback else route.abort())
                return

        return candidate_entries, method, shorter_url

    def _fallback_candidates_char_based(
        self, full_url: str, method: str, request
    ) -> List[CandidateEntry]:
        # NOTE: explore logs here to find patterns and make less and less lm based selection.
        # NOTE: consider a full char based match, tried a bit and sign in amazon password goes back to password.
        """
        Find all HAR entries with the same domain and method, and select the best match based on the number of characters in the URL.
        e.g. amazon requests css/js/media for the same endpoint in different order, so char based helps with matching.
        """

        # Helper function to compute character frequency
        def count_chars(text: str) -> dict[str, int]:
            char_counts: dict[str, int] = {}
            for char in text:
                char_counts[char] = char_counts.get(char, 0) + 1
            return char_counts

        # Helper function to compute character match score
        def compute_char_match_score(
            target_chars: dict[str, int], candidate_chars: dict[str, int]
        ) -> tuple[int, bool]:
            match_score = 0
            matches_all = True
            for char, count in target_chars.items():
                available = candidate_chars.get(char, 0)
                if available >= count:
                    match_score += count
                else:
                    match_score += available
                    matches_all = False
            return match_score, matches_all

        # Get target request data
        target_url_chars = count_chars(full_url)

        # Get request body if available
        target_body = ""
        if hasattr(request, "post_data"):
            target_body = request.post_data or ""
        target_body_chars = count_chars(target_body) if target_body else {}

        # Get request headers
        target_headers = ""
        if hasattr(request, "headers"):
            # Convert headers dict to string for character matching
            target_headers = str(sorted(request.headers.items()))
        target_headers_chars = count_chars(target_headers) if target_headers else {}

        # Find all HAR entries with the same domain and method
        same_domain_candidates = []
        har_entries = self._get_har_matches_by_base_url(full_url, method)
        if not har_entries:
            return []

        for idx, entry in enumerate(har_entries):
            request_data = entry.request

            # URL matching (primary criteria)
            entry_url = request_data.url
            entry_url_chars = count_chars(entry_url)
            url_score, url_matches_all = compute_char_match_score(
                target_url_chars, entry_url_chars
            )

            # Body matching (for logging)
            entry_body = request_data.postData.text if request_data.postData else ""
            entry_body_chars = count_chars(entry_body) if entry_body else {}
            body_score, _ = (
                compute_char_match_score(target_body_chars, entry_body_chars)
                if target_body_chars
                else (0, True)
            )

            # Headers matching (for logging)
            entry_headers = str(
                sorted([(h.name, h.value) for h in request_data.headers])
            )
            entry_headers_chars = count_chars(entry_headers) if entry_headers else {}
            headers_score, _ = (
                compute_char_match_score(target_headers_chars, entry_headers_chars)
                if target_headers_chars
                else (0, True)
            )

            metadata = CandidateEntryMetadata(
                match_score=url_score,
                matches_all=url_matches_all,
                body_score=body_score,
                headers_score=headers_score,
            )

            same_domain_candidates.append(
                CandidateEntry(
                    idx=idx,
                    entry=entry,
                    metadata=metadata,
                )
            )

        same_domain_candidates.sort(key=lambda x: x.metadata.match_score, reverse=True)

        perfect_match = next(
            (c for c in same_domain_candidates if c.metadata.matches_all), None
        )

        if perfect_match:
            same_domain_candidates = [perfect_match]
        top_k = min(5, len(same_domain_candidates))
        return same_domain_candidates[:top_k]

    # *********** Caching LM select best entry ***********

    def _load_matches_cache(self) -> Dict[str, int]:
        """Load the matches cache from matches.json."""
        cache_path = self.bundle_path / "matches.json"
        if cache_path.exists():
            try:
                return json.loads(cache_path.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning(f"Failed to load matches cache: {exc}")
        return {}

    def _save_to_matches_cache(self, cache_key: str, har_index: int) -> None:
        """Save a match to the matches cache."""
        cache_path = self.bundle_path / "matches.json"
        cache = self._load_matches_cache()
        cache[cache_key] = har_index
        try:
            cache_path.write_text(json.dumps(cache, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.warning(f"Failed to save to matches cache: {exc}")

    def _get_cache_key(self, method: str, url: str, post_data: Optional[str]) -> str:
        """Generate cache key in format: {method}-{URL}-{bodyhashshort}."""
        body_hash = ""
        if post_data:
            body_hash = hashlib.md5(post_data.encode()).hexdigest()[:16]
        return f"{method}-{url}-{body_hash}"

    def _find_entry_index_in_har(self, entry: HarEntry) -> Optional[int]:
        """Find the index of an entry in the original HAR entries."""
        for idx, har_entry in enumerate(self._har_entries):
            if har_entry is entry or har_entry == entry:
                return idx
        return None

    # *********** Caching LM select best entry ***********

    async def _select_best_entry(
        self,
        request: Request,
        entries: List[CandidateEntry],
        method: str,
        shorter_url: str,
    ) -> dict:
        if len(entries) == 1:
            return entries[0].entry

        # Get post data for cache key
        try:
            post_data = request.post_data
        except Exception:
            post_data = None

        # Check cache first (unless ignore_cache is set)
        cache_key = self._get_cache_key(method, request.url, post_data)
        cache = self._load_matches_cache()

        if not self.ignore_cache and cache_key in cache:
            cached_har_index = cache[cache_key]
            if 0 <= cached_har_index < len(self._har_entries):
                cached_entry = self._har_entries[cached_har_index]
                # Verify it's in our candidates
                for entry in entries:
                    if entry.entry is cached_entry or entry.entry == cached_entry:
                        logger.info(f"Using cached match for {method} {shorter_url}")
                        return entry.entry

        logger.info(
            f"Multiple HAR candidates ({len(entries)}) found for {method} {shorter_url}, using LM matching",
        )

        candidates = []
        for candidate_entry in entries:
            # Convert HarEntry back to dict format for LM matching
            entry_dict = {
                "method": candidate_entry.entry.request.method,
                "url": candidate_entry.entry.request.url,
                "headers": {
                    h.name: h.value for h in candidate_entry.entry.request.headers
                },
                "postData": {
                    "mimeType": candidate_entry.entry.request.postData.mimeType,
                    "text": candidate_entry.entry.request.postData.text,
                }
                if candidate_entry.entry.request.postData
                else None,
                "responseMimeType": candidate_entry.entry.response.content.mimeType,
            }
            candidates.append(entry_dict)

        idx = await retrieve_best_request_match(
            target_request=request,
            candidates=candidates,
        )
        if idx is None:
            return None

        # NOTE: consider consumed indices (?)
        selected_candidate = entries[idx]

        # Save to cache
        har_index = self._find_entry_index_in_har(selected_candidate.entry)
        if har_index is not None:
            self._save_to_matches_cache(cache_key, har_index)

        return selected_candidate.entry

    async def _fulfill_request_with_entry_found(
        self,
        request: Request,
        entry: HarEntry,
        route: Route,
        allow_network_fallback: bool = False,
    ) -> None:
        # TODO: ensure this matches as expected
        # Entry is already a HarEntry object
        response = entry.response

        status = response.status
        headers = {h.name: h.value for h in response.headers}
        content = response.content

        # Handle different response body types
        body = None
        if content.text:
            text = content.text
            encoding = content.encoding or ""

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
            content_type=content.mimeType,
        )

    def _get_har_matches_by_base_url(
        self, full_url: str, method: str
    ) -> List[HarEntry]:
        # TODO: baseUrl vs this
        base_url = urlparse(full_url).netloc
        matches = []
        for idx, entry in enumerate(self._har_entries):
            if idx in self._consumed_har_indices:
                continue

            entry_request = entry.request
            entry_method = entry_request.method.upper()
            entry_url = entry_request.url

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
    ignore_cache: bool,
) -> None:
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    bundle = ReplayBundle(bundle_path, ignore_cache=ignore_cache)

    trajectory_steps = (
        StepManager.get_instance().get_steps_by_task_id(bundle.task_id)
        if run_human_trajectory
        else []
    )

    async with async_playwright() as pw:
        launch_kwargs: Dict[str, Any] = {
            "headless": headless,
            "channel": channel,
            "args": BROWSER_ARGS,
            "ignore_default_args": IGNORE_DEFAULT_ARGS,
        }
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
    ignore_cache: bool = typer.Option(
        False, help="Ignore cached request matches and use LM matching for all requests"
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
            ignore_cache=ignore_cache,
        )
    )


def _main():
    app()


if __name__ == "__main__":
    app()
