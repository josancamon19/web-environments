# LM GENERATED, NOT CLEANED UP YET
import re
from typing import Dict, Optional
from typing_extensions import Set
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
    - For fonts: domain + path (ignore extension differences between .woff and .woff2)
    - For xhr/fetch: domain + path + essential params only
    - For scripts/stylesheets: domain + base path pattern
    """
    parsed = urlparse(url)

    if resource_type == "image":
        # Images often have:
        # 1. Cache busters in query params
        # 2. Responsive size parameters in the filename (e.g., _AC_UL200_SR200,200_)
        # Amazon pattern: /images/I/{HASH}._AC_UL{size}_SR{size},{size}_.{ext}
        # Normalize to: /images/I/{HASH}.{ext}
        path = parsed.path

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
        path = parsed.path
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
) -> Optional[tuple[int, Dict]]:
    """
    Find a matching HAR entry using fuzzy URL matching.

    Returns:
        Tuple of (entry_index, entry_dict) if found, None otherwise
    """
    if not har_data:
        return None

    har_entries = har_data.get("log", {}).get("entries", [])

    # Normalize the request URL
    normalized_request_url = normalize_url_for_matching(request_url, resource_type)
    similarity_key = compute_url_similarity_key(request_url, resource_type)

    # Try exact match first (for URLs without dynamic params)
    for idx, entry in enumerate(har_entries):
        if idx in consumed_har_indices:
            continue

        har_url = entry.get("request", {}).get("url", "")
        har_method = entry.get("request", {}).get("method", "GET")

        if har_method == request_method and har_url == request_url:
            return (idx, entry)

    # Try normalized URL match
    for idx, entry in enumerate(har_entries):
        if idx in consumed_har_indices:
            continue

        har_url = entry.get("request", {}).get("url", "")
        har_method = entry.get("request", {}).get("method", "GET")

        if har_method != request_method:
            continue

        normalized_har_url = normalize_url_for_matching(har_url, resource_type)

        if normalized_har_url == normalized_request_url:
            return (idx, entry)

    # Try similarity key match (most aggressive, for images and bundled resources)
    for idx, entry in enumerate(har_entries):
        if idx in consumed_har_indices:
            continue

        har_url = entry.get("request", {}).get("url", "")
        har_method = entry.get("request", {}).get("method", "GET")

        if har_method != request_method:
            continue

        har_similarity_key = compute_url_similarity_key(har_url, resource_type)

        if har_similarity_key == similarity_key:
            return (idx, entry)

    return None
