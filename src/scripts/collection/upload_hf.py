#!/usr/bin/env python3
"""
Upload script to generate a parquet file from tasks.jsonl and database,
then upload to HuggingFace Hub.
"""

import json
import os
import sqlite3
from pathlib import Path

import pandas as pd
from huggingface_hub import HfApi
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

console = Console()

# Hardcoded configuration
REPO_ID = "josancamon/mind2web-subset-human"
BUCKET_NAME = "mind2web-subset"
OUTPUT_FILE = Path("data/tasks.parquet")
DB_PATH = Path("data/tasks.db")
JSONL_PATH = Path("data/tasks.jsonl")


def load_tasks_from_jsonl(jsonl_path: Path) -> list[dict]:
    """Load tasks from JSONL file."""
    tasks = []
    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                tasks.append(json.loads(line))
    return tasks


def get_database_stats(db_path: Path, task_id: int) -> dict:
    """Query database for additional statistics about a task."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Get counts of steps and requests
    cursor.execute(
        """
        SELECT 
            COUNT(DISTINCT s.id) as steps_count,
            COUNT(DISTINCT r.id) as requests_count
        FROM tasks t
        LEFT JOIN steps s ON t.id = s.task_id
        LEFT JOIN requests r ON t.id = r.task_id
        WHERE t.id = ?
        GROUP BY t.id
        """,
        (task_id,),
    )

    row = cursor.fetchone()
    conn.close()

    if row:
        return {"recorded_steps_count": row[0], "recorded_http_requests_count": row[1]}
    else:
        return {"recorded_steps_count": 0, "recorded_http_requests_count": 0}


def generate_reference_urls(task_id: int, bucket_name: str) -> dict:
    """Generate GCS URLs for reference data matching upload_gcp.py structure."""
    base_url = f"https://storage.googleapis.com/{bucket_name}"

    return {
        "reference_captured_environment": f"{base_url}/data/captures/task_{task_id}/",
        "reference_screenshots": f"{base_url}/data/screenshots/task{task_id}/",
        "reference_videos": f"{base_url}/data/videos/task{task_id}_*.mp4",
        "reference_doms": f"{base_url}/data/doms/task_{task_id}/",
        "reference_browser_data": f"{base_url}/data/user-data/",
    }


def process_tasks(jsonl_path: Path, db_path: Path, bucket_name: str) -> pd.DataFrame:
    """Process tasks from JSONL and enrich with database statistics."""
    console.print(f"[cyan]Loading tasks from {jsonl_path}...[/cyan]")
    tasks = load_tasks_from_jsonl(jsonl_path)
    console.print(f"[green]✓ Loaded {len(tasks)} tasks[/green]")

    enriched_tasks = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task_progress = progress.add_task("Processing tasks...", total=len(tasks))

        for task in tasks:
            task_id = task["task_id"]

            # Get database statistics
            db_stats = get_database_stats(db_path, task_id)

            # Generate reference URLs
            ref_urls = generate_reference_urls(task_id, bucket_name)

            # Create enriched task record
            enriched_task = {
                "task_id": task["task_id"],
                "task_description": task["task_description"],
                "task_type": task["task_type"],
                "website_url": task.get("website_url"),
                "num_steps": task.get("num_steps"),
                "duration_seconds": task.get("duration_seconds"),
                "golden_trajectory": task.get(
                    "tool_calls", []
                ),  # Human-performed trajectory
                "answer": task.get("answer"),
                "checkpoints": task.get("checkpoints", []),
                "checkpoints_reasoning": task.get("checkpoints_reasoning", []),
                "recorded_steps_count": db_stats["recorded_steps_count"],
                "recorded_http_requests_count": db_stats[
                    "recorded_http_requests_count"
                ],
                **ref_urls,
            }

            enriched_tasks.append(enriched_task)
            progress.update(task_progress, advance=1)

    # Convert to DataFrame
    df = pd.DataFrame(enriched_tasks)
    console.print(
        f"[green]✓ Processed {len(enriched_tasks)} tasks with enriched data[/green]"
    )

    return df


def save_parquet(df: pd.DataFrame, output_file: Path) -> None:
    """Save DataFrame to parquet file."""
    output_file.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(output_file, index=False, engine="pyarrow")
    console.print(f"[green]✓ Saved parquet file to {output_file}[/green]")

    # Print some statistics
    file_size = output_file.stat().st_size / (1024 * 1024)  # MB
    console.print(f"[dim]  File size: {file_size:.2f} MB[/dim]")
    console.print(f"[dim]  Rows: {len(df)}[/dim]")
    console.print(f"[dim]  Columns: {len(df.columns)}[/dim]")


def upload_to_huggingface(file_path: Path, repo_id: str) -> None:
    """Upload parquet file and README to HuggingFace Hub."""
    # Get token from environment
    token = os.environ.get("HF_TOKEN")
    if token is None:
        raise ValueError(
            "HuggingFace token not found. Please set HF_TOKEN environment variable."
        )

    api = HfApi()

    # Create repository if it doesn't exist
    try:
        console.print(f"[cyan]Checking repository: {repo_id}...[/cyan]")
        api.repo_info(repo_id=repo_id, repo_type="dataset", token=token)
        console.print("[green]✓ Repository exists[/green]")
    except Exception:
        console.print(f"[yellow]Repository not found. Creating: {repo_id}...[/yellow]")
        try:
            api.create_repo(
                repo_id=repo_id, repo_type="dataset", token=token, private=False
            )
            console.print("[green]✓ Repository created[/green]")
        except Exception as e:
            console.print(f"[red]✗ Failed to create repository: {e}[/red]")
            raise

    console.print(f"[cyan]Uploading to HuggingFace repository: {repo_id}...[/cyan]")

    try:
        # Upload the parquet file
        api.upload_file(
            path_or_fileobj=str(file_path),
            path_in_repo=file_path.name,
            repo_id=repo_id,
            repo_type="dataset",
            token=token,
        )
        console.print("[green]✓ Successfully uploaded parquet file[/green]")

        # Upload README.md if it exists in the same directory
        readme_path = file_path.parent / "README.md"
        if readme_path.exists():
            api.upload_file(
                path_or_fileobj=str(readme_path),
                path_in_repo="README.md",
                repo_id=repo_id,
                repo_type="dataset",
                token=token,
            )
            console.print("[green]✓ Successfully uploaded README.md[/green]")

        console.print(
            f"[green]✓ View dataset at: https://huggingface.co/datasets/{repo_id}[/green]"
        )
    except Exception as e:
        console.print(f"[red]✗ Upload failed: {e}[/red]")
        raise


def main() -> None:
    """
    Generate a parquet dataset from tasks.jsonl and database,
    then upload to HuggingFace Hub.
    """
    console.print("[bold cyan]Web Environments Dataset Generator[/bold cyan]\n")

    # Show configuration
    console.print("[bold yellow]Configuration:[/bold yellow]")
    console.print(f"  Repository: {REPO_ID}")
    console.print(f"  Bucket: gs://{BUCKET_NAME}")
    console.print(f"  Output: {OUTPUT_FILE}")
    console.print(f"  Database: {DB_PATH}")
    console.print(f"  Input: {JSONL_PATH}\n")

    # Validate input files exist
    if not JSONL_PATH.exists():
        console.print(f"[red]✗ JSONL file not found: {JSONL_PATH}[/red]")
        return

    if not DB_PATH.exists():
        console.print(f"[red]✗ Database file not found: {DB_PATH}[/red]")
        return

    try:
        df = process_tasks(JSONL_PATH, DB_PATH, BUCKET_NAME)
        save_parquet(df, OUTPUT_FILE)
        upload_to_huggingface(OUTPUT_FILE, REPO_ID)
        console.print("\n[bold green]✓ Done![/bold green]")

    except Exception as e:
        console.print(f"\n[bold red]✗ Error: {e}[/bold red]")
        raise


if __name__ == "__main__":
    main()
