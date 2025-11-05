# LM GENERATED, NOT CLEANED UP YET
import re
from dataclasses import dataclass
from typing import Dict, Optional, Set
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

# Query parameters that are often dynamic and should be ignored for URL matching
DYNAMIC_QUERY_PARAMS = {
    "_",  # Cache buster timestamp
    "timestamp",
    "t",
    "time",
    "rid",  # Request ID
    "requestId",
    "cacheBuster",
    "cb",
    "v",  # Version/timestamp
    "rnd",
    "random",
    "__timestamp",
    "_cacheBuster",
}

HASHY_BASENAME_RE = re.compile(r"^[A-Za-z0-9$+\-]{10,}$")

# Static assets can safely be fulfilled multiple times since browsers cache them.
REUSABLE_RESOURCE_TYPES = {"font", "image", "stylesheet", "script", "media"}


@dataclass(frozen=True)
class MatchResult:
    index: int
    entry: Dict
    reason: str
    allow_reuse: bool


def _normalize_hashy_path(path: str, resource_type: Optional[str]) -> str:
    """
    Replace cache-busting hash tokens in the path with stable placeholders so we
    can match equivalent assets whose filenames changed between capture/replay.
    """

    if not path:
        return path

    should_normalize = False
    if resource_type == "font":
        should_normalize = True
    elif resource_type == "image":
        # Amazon hosts sprite sheets and iconography under /S/sash/
        should_normalize = "/sash/" in path
    elif resource_type in {"stylesheet", "script", "media"}:
        should_normalize = True

    if not should_normalize:
        return path

    segments = path.split("/")
    for idx, segment in enumerate(segments):
        if not segment:
            continue

        # Normalise directory level hash tokens (e.g. /build/1A2B3C4D/)
        if HASHY_BASENAME_RE.match(segment):
            segments[idx] = "__HASH_DIR__"
            continue

        if "." not in segment:
            continue

        name, *rest = segment.split(".")
        if not HASHY_BASENAME_RE.match(name):
            continue

        placeholder = "__HASH__"
        if rest:
            placeholder = ".".join([placeholder, *rest])
        segments[idx] = placeholder

    return "/".join(segments)


def normalize_url_for_matching(url: str, resource_type: str = None) -> str:
    """
    Normalize a URL for fuzzy matching by removing dynamic query parameters.

    Args:
        url: The URL to normalize
        resource_type: The resource type (script, stylesheet, image, xhr, fetch, etc.)

    Returns:
        Normalized URL string for matching
    """
    parsed = urlparse(url)
    path = parsed.path

    path = _normalize_hashy_path(path, resource_type)

    # For stylesheets and scripts with bundled resources, extract the base pattern
    if resource_type in ("stylesheet", "script"):
        # Handle Amazon's bundled CSS/JS format: images/I/HASH._RC|file1.css,file2.css,....css?param
        # Normalize to just the base domain and path without the dynamic bundle part
        if "_RC%7C" in path or "_RC|" in path:
            # Extract up to the _RC marker
            base_path = path.split("_RC")[0] + "_RC"
            return f"{parsed.scheme}://{parsed.netloc}{base_path}"

    # Normalize dynamic path segments (e.g., session IDs in ad-events URLs)
    # Pattern: /v1/ad-events/loaded/01K9AEDK69PXX9FAY25RCKA7VQ -> /v1/ad-events/loaded/*
    if "/ad-events/loaded/" in path:
        path = re.sub(r"/ad-events/loaded/[^/?]+", "/ad-events/loaded/*", path)

    # Parse and filter query parameters
    query_params = parse_qs(parsed.query, keep_blank_values=True)

    # Remove dynamic parameters
    filtered_params = {
        k: v for k, v in query_params.items() if k not in DYNAMIC_QUERY_PARAMS
    }

    # Sort for consistent matching
    sorted_params = sorted(filtered_params.items())
    new_query = urlencode(sorted_params, doseq=True) if sorted_params else ""

    # Reconstruct URL without dynamic params
    normalized = urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            path,
            parsed.params,
            new_query,
            "",  # Remove fragment
        )
    )

    return normalized


def compute_url_similarity_key(url: str, resource_type: str = None) -> str:
    """
    Compute a similarity key for URL matching.

    For certain resource types, we use more aggressive normalization:
    - For images: domain + base image hash (ignore size parameters like _AC_UL200_SR200,200_)
    - For fonts: domain + path (ignore extension differences between .woff and .woff2 and hash variations)
    - For xhr/fetch: domain + path + essential params only
    - For scripts/stylesheets: domain + base path pattern
    """
    parsed = urlparse(url)
    path = _normalize_hashy_path(parsed.path, resource_type)

    if resource_type == "image":
        # Images often have:
        # 1. Cache busters in query params
        # 2. Responsive size parameters in the filename (e.g., _AC_UL200_SR200,200_)
        # Amazon pattern: /images/I/{HASH}._AC_UL{size}_SR{size},{size}_.{ext}
        # Normalize to: /images/I/{HASH}.{ext}
        # Strip Amazon responsive image size parameters
        # Pattern: ._AC_UL100_SR100,100_ or similar
        path = re.sub(r"\._AC_UL\d+_SR\d+,\d+_", ".", path)
        # Also handle other common size patterns like ._SL1500_ or ._AC_SX425_
        path = re.sub(r"\._SL\d+_", ".", path)
        path = re.sub(r"\._AC_SX\d+_", ".", path)
        path = re.sub(r"\._AC_SY\d+_", ".", path)

        return f"{parsed.scheme}://{parsed.netloc}{path}"

    if resource_type == "font":
        # Fonts may differ in extension (.woff vs .woff2)
        # Normalize by removing the extension and matching the base filename
        path = path
        # Remove .woff2 or .woff extension
        if path.endswith(".woff2"):
            path = path[:-6]  # Remove .woff2
        elif path.endswith(".woff"):
            path = path[:-5]  # Remove .woff
        return f"{parsed.scheme}://{parsed.netloc}{path}"

    # For other types, use the standard normalization
    return normalize_url_for_matching(url, resource_type)


def find_fuzzy_har_match(
    har_data: Dict,
    consumed_har_indices: Set[int],
    request_url: str,
    request_method: str,
    resource_type: str,
) -> Optional[MatchResult]:
    """
    Find a matching HAR entry using fuzzy URL matching.

    Returns:
        MatchResult containing the entry index and data if found, None otherwise
    """
    if not har_data:
        return None

    har_entries = har_data.get("log", {}).get("entries", [])

    # Normalize the request URL
    normalized_request_url = normalize_url_for_matching(request_url, resource_type)
    similarity_key = compute_url_similarity_key(request_url, resource_type)

    reuse_friendly = resource_type in REUSABLE_RESOURCE_TYPES

    def _make_result(idx: int, entry: Dict, reason: str) -> MatchResult:
        return MatchResult(
            index=idx,
            entry=entry,
            reason=reason,
            allow_reuse=reuse_friendly,
        )

    # Try exact match first (for URLs without dynamic params)
    for idx, entry in enumerate(har_entries):
        if not reuse_friendly and idx in consumed_har_indices:
            continue

        har_url = entry.get("request", {}).get("url", "")
        har_method = entry.get("request", {}).get("method", "GET")

        if har_method == request_method and har_url == request_url:
            return _make_result(idx, entry, "exact")

    # Try normalized URL match
    for idx, entry in enumerate(har_entries):
        if not reuse_friendly and idx in consumed_har_indices:
            continue

        har_url = entry.get("request", {}).get("url", "")
        har_method = entry.get("request", {}).get("method", "GET")

        if har_method != request_method:
            continue

        normalized_har_url = normalize_url_for_matching(har_url, resource_type)

        if normalized_har_url == normalized_request_url:
            return _make_result(idx, entry, "normalized")

    # Try similarity key match (most aggressive, for images and bundled resources)
    for idx, entry in enumerate(har_entries):
        if not reuse_friendly and idx in consumed_har_indices:
            continue

        har_url = entry.get("request", {}).get("url", "")
        har_method = entry.get("request", {}).get("method", "GET")

        if har_method != request_method:
            continue

        har_similarity_key = compute_url_similarity_key(har_url, resource_type)

        if har_similarity_key == similarity_key:
            return _make_result(idx, entry, "similarity")

    # No match found - this is expected for resources not in the original capture
    # (e.g., A/B tested CSS loading different fonts, responsive images at different sizes)
    return None
