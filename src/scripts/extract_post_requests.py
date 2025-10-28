#!/usr/bin/env python3
"""Extract dynamic POST requests from a HAR capture."""

from __future__ import annotations

import typer
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, MutableMapping, Sequence
from urllib.parse import urlparse


TELEMETRY_HOST_KEYWORDS: Sequence[str] = (
    "amazon-adsystem",
    "doubleclick",
    "fls-na",
    "google-analytics",
    "metrics",
    "pinpoint",
    "unagi",
)


def normalize_headers(headers: Iterable[Dict[str, Any]] | None) -> Dict[str, Any]:
    """Convert HAR headers to a name -> value/list mapping preserving duplicates."""
    normalized: MutableMapping[str, Any] = {}
    if not headers:
        return {}

    for header in headers:
        name = header.get("name")
        value = header.get("value")
        if not name:
            continue
        if name in normalized:
            existing = normalized[name]
            if isinstance(existing, list):
                existing.append(value)
            else:
                normalized[name] = [existing, value]
        else:
            normalized[name] = value

    return dict(normalized)


def looks_like_dynamic(entry: Dict[str, Any]) -> bool:
    """Best-effort heuristic for requests that need dynamic validation."""
    request = entry.get("request", {})
    response = entry.get("response", {})

    url = request.get("url", "")
    hostname = (urlparse(url).hostname or "").lower()
    if any(keyword in hostname for keyword in TELEMETRY_HOST_KEYWORDS):
        return False

    post_data = request.get("postData") or {}
    request_has_payload = bool(
        (post_data.get("text") and post_data.get("text").strip())
        or post_data.get("params")
    )

    content = response.get("content") or {}
    mime_type = (content.get("mimeType") or "").lower()
    response_has_json = "json" in mime_type
    response_has_body = bool(content.get("text"))

    if request_has_payload:
        return True

    if response_has_json and response_has_body:
        return True

    # Fallback: include if we got a meaningful body with a success status.
    status = response.get("status", 0)
    return response_has_body and status not in (0, 204, 301, 302)


def slim_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    request = entry["request"]
    response = entry["response"]
    content = response.get("content", {})
    post_data = request.get("postData") or {}

    return {
        "startedDateTime": entry.get("startedDateTime"),
        "time": entry.get("time"),
        "request": {
            "method": request.get("method"),
            "url": request.get("url"),
            "headers": normalize_headers(request.get("headers")),
            "queryString": normalize_headers(request.get("queryString")),
            "postData": {
                k: post_data.get(k)
                for k in ("mimeType", "text", "encoding", "params")
                if post_data.get(k) is not None
            },
        },
        "response": {
            "status": response.get("status"),
            "statusText": response.get("statusText"),
            "headers": normalize_headers(response.get("headers")),
            "mimeType": content.get("mimeType"),
            "encoding": content.get("encoding"),
            "text": content.get("text"),
        },
    }


def filter_post_requests(entries: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    filtered: List[Dict[str, Any]] = []
    for entry in entries:
        request = entry.get("request", {})
        if request.get("method") != "POST":
            continue
        if not looks_like_dynamic(entry):
            continue
        filtered.append(slim_entry(entry))
    return filtered


def main(har_path: str) -> None:
    har_path = Path(har_path).expanduser().resolve()
    if not har_path.exists():
        raise SystemExit(f"HAR file not found: {har_path}")

    with har_path.open("r", encoding="utf-8") as fh:
        har_data = json.load(fh)

    entries = har_data.get("log", {}).get("entries", [])
    filtered = filter_post_requests(entries)

    output_path = har_path.with_name(f"{har_path.stem}_POST.json")
    with output_path.open("w", encoding="utf-8") as fh:
        json.dump({"entries": filtered}, fh, indent=2, ensure_ascii=False)
        fh.write("\n")

    print(
        f"Wrote {len(filtered)} POST request(s) requiring dynamic validation to {output_path}"
    )


if __name__ == "__main__":
    typer.run(main)
