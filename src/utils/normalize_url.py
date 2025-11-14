from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

# Query parameters to exclude from URL matching/comparison
EXCLUDED_QUERY_PARAMS = {
    "_",  # cache buster (underscore)
    "t",  # timestamp
    "ts",  # timestamp
    "timestamp",
    "v",  # version
    "version",
    "cache_bust",
    "cb",  # cache bust
    "nocache",
    "r",  # random
    "utm_source",  # Google Analytics tracking
    "utm_campaign",
    "utm_medium",
    "utm_term",
    "utm_content",
    "gclid",  # Google ads click ID
    "fbclid",  # Facebook ads click ID
    "msclkid",  # Microsoft ads click ID
}


def normalize_url_for_matching(url: str) -> str:
    """Remove excluded query parameters from URL for matching purposes.

    This removes cache-busting, tracking, and timestamp parameters that
    should not affect URL matching or character counting.
    """
    parsed = urlparse(url)

    # If there are no query parameters, return as-is
    if not parsed.query:
        return url

    # Parse query string
    query_params = parse_qs(parsed.query, keep_blank_values=True)
    filtered_params = {
        k: v for k, v in query_params.items() if k not in EXCLUDED_QUERY_PARAMS
    }

    new_query = urlencode(filtered_params, doseq=True) if filtered_params else ""
    normalized = urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            new_query,
            parsed.fragment,
        )
    )

    return normalized
