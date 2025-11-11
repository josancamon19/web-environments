# cleanup HAR

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from pydantic import BaseModel, Field
from openai import OpenAI
from dotenv import load_dotenv

from config.storage import DATA_DIR
from scripts.postprocessing._ignore_patterns import IGNORED_PATTERNS

load_dotenv()

# TODO: need to find all of this that don't mean anything to match
# TODO: need to collect traces for LM matching, to amnually check where to expand.
# TODO: optimize this so we don't LM call the same repeated URL's
no_ignore_patterns = [
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".webp",
    ".css",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".ico",
    "jquery",
]
PROMPT = """You are analyzing HTTP requests from HAR recordings to identify requests that are not relevant for website functionality or user experience.

These typically include:
- Analytics services (Google Analytics, Adobe Analytics, etc.)
- Advertisement networks and trackers
- Social media tracking pixels
- Tag managers that don't affect core functionality
- Third-party monitoring and metrics services

However, DO NOT ignore:
- Core API requests needed for functionality
- Authentication/session management requests
- Content delivery requests (images, CSS, fonts, media files)
- WebSocket connections for real-time features
- Any requests that directly contribute to user-visible content or interactions

Analyze the following URLs from a HAR recording and identify which ones are NOT relevant for website functionality or user experience.

Return the indices (0-based) of URLs that can be safely ignored during replay.

URLs to analyze:
{url_list}

Select the indices of URLs that we can ignore during the replay of the trajectory without affecting the website functionality or experience."""

_compiled_patterns = []
for pattern in IGNORED_PATTERNS:
    if "*" in pattern:
        # Convert wildcard pattern to regex: * matches zero or more characters (except /)
        regex_pattern = re.escape(pattern).replace(r"\*", r"[^/]*")
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


def should_always_keep_url(url: str):
    """Check if URL should always be kept (never passed to LM for evaluation)."""
    url_lower = url.lower()
    for pattern in no_ignore_patterns:
        if url_lower.endswith(pattern.lower()):
            return True
    return False


class ExtractNonRelevant(BaseModel):
    """Response format for extracting non-relevant URLs."""

    non_relevant_indices: list[int] = Field(
        description="The list of indices of the URLs that we can ignore during the replay of the trajectory without affecting the website functionality or experience."
    )
    reasoning: str = Field(
        description="Brief explanation of why these URLs were identified as non-relevant"
    )


# Initialize OpenAI client
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))


def process_url_batch(batch_data: tuple) -> set:
    batch_urls, batch_original_indices, batch_idx = batch_data

    try:
        # Format URLs with indices
        url_list = "\n".join([f"{i}: {url}" for i, url in enumerate(batch_urls)])
        prompt = PROMPT.format(url_list=url_list)
        response = client.responses.parse(
            model="gpt-5",
            reasoning={"effort": "high"},
            input=[{"role": "user", "content": prompt}],
            text_format=ExtractNonRelevant,
        )

        result = response.output_parsed
        lm_ignored_batch = set(result.non_relevant_indices)

        # Map batch indices back to original indices
        ignored_original_indices = set()
        for batch_idx_val in lm_ignored_batch:
            if 0 <= batch_idx_val < len(batch_original_indices):
                ignored_original_indices.add(batch_original_indices[batch_idx_val])

        print(
            f"  Batch {batch_idx}: LM identified {len(ignored_original_indices)} URLs to ignore"
        )
        return ignored_original_indices
    except Exception as e:
        print(f"  Warning: Batch {batch_idx} LM analysis failed: {e}")
        import traceback

        traceback.print_exc()
        return set()


def determine_ignored_urls(har_path: str, task_name: str) -> dict:
    with open(har_path, "r") as f:
        entries = json.loads(f.read())["log"]["entries"]

    print(f"Total HAR entries: {len(entries)}")

    # First pass: filter with basic patterns and built-in ignore list
    cleaned = []
    ignored_indices = set()
    lm_candidates = []  # URLs to pass to LM with their original indices
    always_keep_count = 0

    unique_hosts = set()

    for idx, entry in enumerate(entries):
        request = entry["request"]
        method, url = request["method"], request["url"]
        url_clean = url.replace("http://", "").replace("https://", "")
        base_name = url_clean.split("/")[0]

        # Check against analytics/ads patterns
        if should_ignore_url(url_clean):
            ignored_indices.add(idx)
            continue

        cleaned.append((method, url_clean))
        unique_hosts.add(base_name)

        # Check if this URL should always be kept (not passed to LM)
        if should_always_keep_url(url_clean):
            always_keep_count += 1
        else:
            # Add to LM candidates for evaluation
            lm_candidates.append((idx, f"{method} {url_clean}"))

    print(
        f"After basic filtering: {len(cleaned)} URLs, {len(unique_hosts)} unique hosts"
    )
    print(f"Ignored by patterns: {len(ignored_indices)} URLs")
    print(f"Always keep (no_ignore_patterns): {always_keep_count} URLs")
    print(f"URLs to evaluate with LM: {len(lm_candidates)} URLs")

    result = {
        "all_entries": entries,
        "ignored_indices": ignored_indices,
        "ignored_urls": [],
        "cleaned_urls": cleaned,
        "unique_hosts": unique_hosts,
        "lm_ignored_indices": set(),
    }

    if lm_candidates:
        print("\nUsing LM to identify additional non-relevant URLs...")
        try:
            # Split into batches of 100
            BATCH_SIZE = 100
            batches = []
            for i in range(0, len(lm_candidates), BATCH_SIZE):
                batch_slice = lm_candidates[i : i + BATCH_SIZE]
                batch_original_indices = [idx for idx, _ in batch_slice]
                batch_urls = [url for _, url in batch_slice]
                batch_idx = i // BATCH_SIZE
                batches.append((batch_urls, batch_original_indices, batch_idx))

            print(
                f"Processing {len(batches)} batches of up to {BATCH_SIZE} URLs each..."
            )

            # Process batches in parallel
            lm_ignored_all = set()
            with ThreadPoolExecutor(max_workers=32) as executor:
                futures = [
                    executor.submit(process_url_batch, batch) for batch in batches
                ]

                for future in futures:
                    batch_ignored = future.result()
                    lm_ignored_all.update(batch_ignored)

            # Update ignored indices
            result["lm_ignored_indices"] = lm_ignored_all
            ignored_indices.update(lm_ignored_all)

            print(
                f"LM identified {len(result['lm_ignored_indices'])} additional URLs to ignore"
            )
        except Exception as e:
            print(f"Warning: LM analysis failed: {e}")
            import traceback

            traceback.print_exc()

    # Collect all ignored URLs for saving
    ignored_urls = []
    for idx in sorted(ignored_indices):
        url = entries[idx]["request"]["url"]
        url_clean = url.replace("http://", "").replace("https://", "")
        ignored_urls.append(url_clean)

    result["ignored_indices"] = ignored_indices
    result["ignored_urls"] = ignored_urls
    print(f"\nTotal ignored: {len(ignored_indices)} URLs")
    print(f"Total relevant: {len(entries) - len(ignored_indices)} URLs")

    return result


def process_task(task_dir: Path) -> dict:
    """Process a single task directory."""
    task_name = task_dir.name
    har_file = task_dir / "recording.har"
    ignored_file = task_dir / "ignored.json"

    if not har_file.exists():
        print(f"Skipping {task_name}: recording.har not found")
        return None

    try:
        result = determine_ignored_urls(str(har_file), task_name)

        # Save ignored URLs to ignored.json as a simple list
        with open(ignored_file, "w") as f:
            json.dump(result["ignored_urls"], f, indent=2)

        print(f"✓ Saved {len(result['ignored_urls'])} ignored URLs to {ignored_file}")

        return {
            "task_name": task_name,
            "total_entries": len(result["all_entries"]),
            "ignored_count": len(result["ignored_indices"]),
            "pattern_ignored": len(result["ignored_indices"])
            - len(result["lm_ignored_indices"]),
            "lm_ignored": len(result["lm_ignored_indices"]),
            "unique_hosts": len(result["unique_hosts"]),
        }
    except Exception as e:
        print(f"✗ Error processing {task_name}: {e}")
        import traceback

        traceback.print_exc()
        return None


def main():
    """Process all task directories in parallel."""

    # Optional: Set up MLflow tracking if needed
    # import mlflow
    # from datetime import datetime
    # mlflow.set_tracking_uri("http://127.0.0.1:5000")
    # mlflow.set_experiment(
    #     f"determine-ignore-{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}"
    # )
    # mlflow.openai.autolog()

    # Find all task directories
    captures_dir = DATA_DIR / "captures"
    if not captures_dir.exists():
        print(f"Error: captures directory not found at {captures_dir}")
        return

    task_dirs = [
        d for d in captures_dir.iterdir() if d.is_dir() and d.name.startswith("task_")
    ]
    task_dirs.sort()

    print(f"\nFound {len(task_dirs)} task directories to process")
    print(f"Captures directory: {captures_dir}")

    # Process all tasks in parallel
    results = []
    with ThreadPoolExecutor(max_workers=32) as executor:
        futures = [executor.submit(process_task, task_dir) for task_dir in task_dirs]

        for future in futures:
            result = future.result()
            if result:
                results.append(result)

    # Print summary
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    for r in results:
        print(
            f"{r['task_name']:15s} | Total: {r['total_entries']:5d} | "
            f"Ignored: {r['ignored_count']:5d} (Pattern: {r['pattern_ignored']:4d}, LM: {r['lm_ignored']:4d}) | "
            f"Hosts: {r['unique_hosts']:3d}"
        )

    total_entries = sum(r["total_entries"] for r in results)
    total_ignored = sum(r["ignored_count"] for r in results)
    print(
        f"\nTotal entries: {total_entries}, Total ignored: {total_ignored} ({total_ignored / total_entries * 100:.1f}%)"
    )


if __name__ == "__main__":
    main()
