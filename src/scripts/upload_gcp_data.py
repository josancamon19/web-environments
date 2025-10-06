#!/usr/bin/env python3
"""
Upload script to upload the data directory to Google Cloud Storage.
Uploads data/ to gs://mind2web-subset/data/ in web-environments project.
"""

import json
from pathlib import Path

from google.cloud import storage
from google.oauth2 import service_account
from rich.console import Console
from rich.progress import (
    Progress,
    SpinnerColumn,
    TextColumn,
    BarColumn,
    TaskProgressColumn,
)

console = Console()

# Hardcoded configuration
BUCKET_NAME = "mind2web-subset"
PROJECT_ID = "web-environments"
DATA_DIR = Path("data")
DESTINATION_PREFIX = "data/"


def get_credentials() -> service_account.Credentials:
    """Get Google Cloud credentials from file."""
    credentials_file = Path(__file__).parent.parent.parent / "google-credentials.json"

    if not credentials_file.exists():
        raise ValueError(f"Credentials file not found: {credentials_file}")

    console.print(f"[cyan]Using credentials from file: {credentials_file}[/cyan]")

    # Load and modify the credentials
    with open(credentials_file, "r") as f:
        creds_dict = json.load(f)

    original_project = creds_dict.get("project_id", "unknown")
    creds_dict["project_id"] = PROJECT_ID
    console.print(
        f"[yellow]Overriding project_id: {original_project} → {PROJECT_ID}[/yellow]"
    )

    return service_account.Credentials.from_service_account_info(creds_dict)


def get_files_to_upload(data_dir: Path) -> list[Path]:
    """Get list of all files to upload from data directory."""
    exclude_patterns = [
        "*.db-shm",  # SQLite shared memory
        "*.db-wal",  # SQLite write-ahead log
        "__pycache__",
        "*.pyc",
        ".DS_Store",
    ]

    all_files = []

    for item in data_dir.rglob("*"):
        if item.is_file():
            # Check if file matches exclude patterns
            should_exclude = False
            for pattern in exclude_patterns:
                if pattern.startswith("*"):
                    if item.name.endswith(pattern[1:]):
                        should_exclude = True
                        break
                elif pattern in str(item):
                    should_exclude = True
                    break

            if not should_exclude:
                all_files.append(item)

    return sorted(all_files)


def main() -> None:
    """Upload data directory to GCS."""

    # Validate data directory exists
    if not DATA_DIR.exists():
        console.print(f"[red]✗ Data directory not found: {DATA_DIR}[/red]")
        return

    console.print(f"[bold cyan]Uploading {DATA_DIR} to GCS[/bold cyan]\n")

    # Get credentials
    try:
        credentials = get_credentials()
        console.print("[green]✓ Credentials loaded[/green]")
    except Exception as e:
        console.print(f"[red]✗ Failed to load credentials: {e}[/red]")
        return

    # Create storage client
    try:
        client = storage.Client(credentials=credentials, project=PROJECT_ID)
        bucket = client.bucket(BUCKET_NAME)

        # Check if bucket exists
        if not bucket.exists():
            console.print(
                f"[red]✗ Bucket '{BUCKET_NAME}' not found in project '{PROJECT_ID}'[/red]"
            )
            console.print("[yellow]Available buckets:[/yellow]")
            for b in client.list_buckets():
                console.print(f"  - {b.name}")
            return

        console.print(f"[green]✓ Connected to bucket: {BUCKET_NAME}[/green]")
    except Exception as e:
        console.print(f"[red]✗ Failed to connect to GCS: {e}[/red]")
        return

    # Get list of files to upload
    console.print("[cyan]Scanning directory for files...[/cyan]")
    files_to_upload = get_files_to_upload(DATA_DIR)

    if not files_to_upload:
        console.print("[yellow]No files found to upload[/yellow]")
        return

    # Calculate total size
    total_size = sum(f.stat().st_size for f in files_to_upload)
    total_size_mb = total_size / (1024 * 1024)

    console.print(
        f"[green]✓ Found {len(files_to_upload)} files ({total_size_mb:.2f} MB)[/green]\n"
    )

    # Show upload details
    console.print("[bold yellow]Upload configuration:[/bold yellow]")
    console.print(f"  Bucket: gs://{BUCKET_NAME}/{DESTINATION_PREFIX}")
    console.print(f"  Project: {PROJECT_ID}")
    console.print(f"  Files: {len(files_to_upload)}")
    console.print(f"  Size: {total_size_mb:.2f} MB\n")

    # Confirm upload
    try:
        response = input("Continue with upload? [y/N]: ").strip().lower()
        if response not in ["y", "yes"]:
            console.print("[yellow]Upload cancelled[/yellow]")
            return
    except (KeyboardInterrupt, EOFError):
        console.print("\n[yellow]Upload cancelled[/yellow]")
        return

    # Upload files with progress bar
    uploaded_count = 0
    failed_uploads = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        upload_task = progress.add_task(
            "Uploading files...", total=len(files_to_upload)
        )

        for file_path in files_to_upload:
            try:
                # Calculate relative path from data_dir parent
                rel_path = file_path.relative_to(DATA_DIR.parent)

                # Create blob path (maintain directory structure)
                blob_name = str(rel_path).replace("\\", "/")

                # Upload file
                blob = bucket.blob(blob_name)
                blob.upload_from_filename(str(file_path))

                uploaded_count += 1
                progress.update(
                    upload_task,
                    advance=1,
                    description=f"Uploading... ({uploaded_count}/{len(files_to_upload)})",
                )

            except Exception as e:
                failed_uploads.append((file_path, str(e)))
                console.print(f"[red]✗ Failed to upload {file_path.name}: {e}[/red]")

    # Print summary
    console.print("\n[bold green]Upload Complete![/bold green]")
    console.print(f"  Uploaded: {uploaded_count} files")
    console.print(f"  Total size: {total_size_mb:.2f} MB")
    console.print(f"  Location: gs://{BUCKET_NAME}/{DESTINATION_PREFIX}")

    if failed_uploads:
        console.print(
            f"\n[yellow]⚠ {len(failed_uploads)} files failed to upload:[/yellow]"
        )
        for file_path, error in failed_uploads[:10]:  # Show first 10 errors
            console.print(f"  - {file_path.name}: {error}")
        if len(failed_uploads) > 10:
            console.print(f"  ... and {len(failed_uploads) - 10} more")


if __name__ == "__main__":
    main()