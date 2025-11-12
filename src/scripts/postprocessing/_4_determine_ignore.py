# cleanup HAR

import asyncio
import json
import re
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from pydantic import BaseModel, Field

from config.storage import DATA_DIR
from scripts.postprocessing._ignore_patterns import IGNORED_PATTERNS
from utils.oai import openai_structured_output_request_async

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


async def process_url_batch(batch_data: tuple) -> set:
    batch_urls, batch_original_indices, batch_idx, task_name = batch_data

    try:
        # Format URLs with indices
        url_list = "\n".join([f"{i}: {url}" for i, url in enumerate(batch_urls)])

        result = await openai_structured_output_request_async(
            prompt_name="determine_ignore",
            model="gpt-5",
            reasoning="high",
            text_format=ExtractNonRelevant,
            url_list=url_list,
        )

        lm_ignored_batch = set(result.non_relevant_indices)

        # Map batch indices back to original indices
        ignored_original_indices = set()
        for batch_idx_val in lm_ignored_batch:
            if 0 <= batch_idx_val < len(batch_original_indices):
                ignored_original_indices.add(batch_original_indices[batch_idx_val])

        print(
            f"  {task_name} Batch {batch_idx}: LM identified {len(ignored_original_indices)} URLs to ignore"
        )
        return ignored_original_indices
    except Exception as e:
        print(f"  Warning: {task_name} Batch {batch_idx} LM analysis failed: {e}")
        import traceback

        traceback.print_exc()
        return set()


def collect_task_batches(task_dir: Path) -> tuple[Path, dict] | None:
    """Collect batches for a task without processing them. Returns (task_dir, task_data) or None."""
    task_name = task_dir.name
    har_file = task_dir / "recording.har"

    if not har_file.exists():
        print(f"Skipping {task_name}: recording.har not found")
        return None

    try:
        with open(har_file, "r") as f:
            entries = json.loads(f.read())["log"]["entries"]

        print(f"{task_name}: Total HAR entries: {len(entries)}")

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
            f"{task_name}: After basic filtering: {len(cleaned)} URLs, {len(unique_hosts)} unique hosts"
        )
        print(f"{task_name}: Ignored by patterns: {len(ignored_indices)} URLs")
        print(
            f"{task_name}: Always keep (no_ignore_patterns): {always_keep_count} URLs"
        )
        print(f"{task_name}: URLs to evaluate with LM: {len(lm_candidates)} URLs")

        # Split into batches of 500
        BATCH_SIZE = 500
        batches = []
        if lm_candidates:
            for i in range(0, len(lm_candidates), BATCH_SIZE):
                batch_slice = lm_candidates[i : i + BATCH_SIZE]
                batch_original_indices = [idx for idx, _ in batch_slice]
                batch_urls = [url for _, url in batch_slice]
                batch_idx = i // BATCH_SIZE
                batches.append(
                    (batch_urls, batch_original_indices, batch_idx, task_name)
                )

            print(
                f"{task_name}: Created {len(batches)} batches of up to {BATCH_SIZE} URLs each"
            )

        task_data = {
            "task_name": task_name,
            "all_entries": entries,
            "ignored_indices": ignored_indices,
            "cleaned_urls": cleaned,
            "unique_hosts": unique_hosts,
            "batches": batches,
        }

        return (task_dir, task_data)

    except Exception as e:
        print(f"✗ Error collecting batches for {task_name}: {e}")
        import traceback

        traceback.print_exc()
        return None


async def process_all_batches_async(all_batches: list) -> dict[str, set]:
    """Process all batches from all tasks concurrently using asyncio."""
    print(f"\n{'=' * 80}")
    print(
        f"Processing {len(all_batches)} total batches across all tasks using asyncio..."
    )
    print(f"{'=' * 80}\n")

    # Process all batches concurrently
    tasks = [process_url_batch(batch) for batch in all_batches]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Map results back to task names
    task_results = {}
    for i, result in enumerate(results):
        batch_data = all_batches[i]
        task_name = batch_data[3]  # task_name is 4th element in tuple

        if task_name not in task_results:
            task_results[task_name] = set()

        if isinstance(result, Exception):
            print(f"  Error processing batch for {task_name}: {result}")
        else:
            task_results[task_name].update(result)

    return task_results


def save_task_results(task_data: dict, lm_ignored_indices: set, task_dir: Path):
    """Save results for a single task."""
    task_name = task_data["task_name"]
    ignored_file = task_dir / "ignored.json"

    # Update ignored indices
    all_ignored = task_data["ignored_indices"].copy()
    all_ignored.update(lm_ignored_indices)

    # Collect all ignored URLs for saving
    ignored_urls = []
    for idx in sorted(all_ignored):
        url = task_data["all_entries"][idx]["request"]["url"]
        url_clean = url.replace("http://", "").replace("https://", "")
        ignored_urls.append(url_clean)

    # Save ignored URLs to ignored.json as a simple list
    with open(ignored_file, "w") as f:
        json.dump(ignored_urls, f, indent=2)

    print(f"✓ {task_name}: Saved {len(ignored_urls)} ignored URLs to {ignored_file}")
    print(f"  {task_name}: Total ignored: {len(all_ignored)} URLs")
    print(
        f"  {task_name}: Total relevant: {len(task_data['all_entries']) - len(all_ignored)} URLs"
    )

    return {
        "task_name": task_name,
        "total_entries": len(task_data["all_entries"]),
        "ignored_count": len(all_ignored),
        "pattern_ignored": len(task_data["ignored_indices"]),
        "lm_ignored": len(lm_ignored_indices),
        "unique_hosts": len(task_data["unique_hosts"]),
    }


async def main_async():
    """Process all task directories by collecting all batches first, then processing them all."""
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
    print(f"\n{'=' * 80}")
    print("PHASE 1: Collecting all batches from all tasks (using multiprocessing)")
    print(f"{'=' * 80}\n")

    # Phase 1: Collect all batches from all tasks in parallel using multiprocessing
    all_task_data = []
    all_batches = []

    with ProcessPoolExecutor() as executor:
        # Submit all tasks for parallel processing
        futures = [
            executor.submit(collect_task_batches, task_dir) for task_dir in task_dirs
        ]

        # Collect results as they complete
        for future in futures:
            result = future.result()
            if result is not None:
                task_dir, task_data = result
                all_task_data.append((task_dir, task_data))
                all_batches.extend(task_data["batches"])

    print(f"\n{'=' * 80}")
    print(f"Collected {len(all_batches)} total batches from {len(all_task_data)} tasks")
    print(f"{'=' * 80}\n")

    # Phase 2: Process all batches concurrently
    if all_batches:
        task_lm_results = await process_all_batches_async(all_batches)
    else:
        task_lm_results = {}

    # Phase 3: Save results for each task
    print(f"\n{'=' * 80}")
    print("PHASE 3: Saving results for each task")
    print(f"{'=' * 80}\n")

    results = []
    for task_dir, task_data in all_task_data:
        task_name = task_data["task_name"]
        lm_ignored = task_lm_results.get(task_name, set())

        try:
            result = save_task_results(task_data, lm_ignored, task_dir)
            results.append(result)
        except Exception as e:
            print(f"✗ Error saving results for {task_name}: {e}")
            import traceback

            traceback.print_exc()

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

    if results:
        total_entries = sum(r["total_entries"] for r in results)
        total_ignored = sum(r["ignored_count"] for r in results)
        print(
            f"\nTotal entries: {total_entries}, Total ignored: {total_ignored} ({total_ignored / total_entries * 100:.1f}%)"
        )


def main():
    """Entry point that runs the async main function."""
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
