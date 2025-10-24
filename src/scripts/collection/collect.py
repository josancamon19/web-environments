#!/usr/bin/env python3
"""
Script to download and unzip files from Google Cloud Storage bucket.
"""

import os
import json
import base64
import zipfile
from pathlib import Path
from google.cloud import storage
from google.oauth2 import service_account


def get_credentials():
    """Get Google Cloud credentials from environment or file."""
    # Try to get credentials from environment variable (base64 encoded)
    creds_b64 = os.environ.get("GOOGLE_CREDENTIALS_BASE64")
    if creds_b64:
        print("Using credentials from GOOGLE_CREDENTIALS_BASE64 environment variable")
        creds_json = base64.b64decode(creds_b64).decode("utf-8")
        creds_dict = json.loads(creds_json)
        return service_account.Credentials.from_service_account_info(creds_dict)

    # Fall back to file
    creds_file = Path(__file__).parent.parent.parent / "google-credentials.json"
    if creds_file.exists():
        print(f"Using credentials from file: {creds_file}")
        return service_account.Credentials.from_service_account_file(str(creds_file))

    raise ValueError(
        "No Google credentials found. Set GOOGLE_CREDENTIALS_BASE64 env var or provide google-credentials.json file"
    )


def download_and_unzip_files(
    bucket_name: str = "collection-reports", tmp_dir: Path = None
):
    """Download and unzip all files from the bucket."""
    if tmp_dir is None:
        tmp_dir = Path(__file__).parent.parent.parent / "tmp"

    # Create tmp directory if it doesn't exist
    tmp_dir.mkdir(exist_ok=True)
    print(f"Using tmp directory: {tmp_dir}")

    # Get credentials and create storage client
    credentials = get_credentials()
    client = storage.Client(credentials=credentials)

    # Get bucket
    bucket = client.bucket(bucket_name)
    print(f"Connected to bucket: {bucket_name}")

    # List all blobs in the bucket
    blobs = list(bucket.list_blobs())
    if not blobs:
        print("No files found in bucket")
        return

    print(f"Found {len(blobs)} files in bucket:")
    for blob in blobs:
        print(f"  - {blob.name} ({blob.size / (1024**3):.1f} GB)")

    # Download and unzip each file
    for blob in blobs[-1:]:
        if blob.name.endswith(".zip"):
            download_path = tmp_dir / blob.name

            # Download file
            print(f"\nDownloading {blob.name}...")
            blob.download_to_filename(str(download_path))
            print(f"  Downloaded to: {download_path}")

            # Create extraction directory (without .zip extension)
            extract_dir = tmp_dir / blob.name.replace(".zip", "")
            extract_dir.mkdir(exist_ok=True)

            # Unzip file
            print(f"  Extracting to: {extract_dir}")
            with zipfile.ZipFile(download_path, "r") as zip_ref:
                zip_ref.extractall(extract_dir)
            print("  Extraction complete")

            download_path.unlink()
            print("  Removed zip file")


def main():
    """Main function."""
    try:
        download_and_unzip_files()
        print("\nAll files downloaded and extracted successfully!")
    except Exception as e:
        print(f"\nError: {e}")
        raise


if __name__ == "__main__":
    main()
