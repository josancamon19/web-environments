#!/usr/bin/env python3
"""Build Task Collector desktop distributables for macOS and Windows.

This script standardises the PyInstaller build so GitHub Actions and
contributors can produce identical bundles. It takes care of downloading
Playwright's Chromium runtime into the bundle, adds a user-friendly
"How to install" note, and emits a ready-to-upload ZIP archive per OS.
"""

from __future__ import annotations

import argparse
import importlib
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict

APP_NAME = "TaskCollector"
SUPPORTED_TARGETS = {"macos", "windows"}


class BuildError(RuntimeError):
    """Raised when a build step fails."""


def run(cmd: list[str], *, env: Dict[str, str] | None = None) -> None:
    """Run a subprocess, surfacing stderr on failure."""

    process_env = os.environ.copy()
    if env:
        process_env.update(env)

    result = subprocess.run(cmd, env=process_env, check=False)
    if result.returncode != 0:
        raise BuildError(f"Command failed ({result.returncode}): {' '.join(cmd)}")


def ensure_empty_dir(path: Path) -> None:
    """Delete the directory if it exists, then recreate it."""

    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def install_playwright_browser(target_dir: Path) -> None:
    """Install Chromium into a deterministic folder for bundling."""

    ensure_empty_dir(target_dir)
    run(
        [sys.executable, "-m", "playwright", "install", "chromium", "--with-deps"],
        env={"PLAYWRIGHT_BROWSERS_PATH": str(target_dir)},
    )


def module_importable(name: str) -> bool:
    """Return True if the module can be imported."""

    try:
        importlib.import_module(name)
    except Exception:  # pragma: no cover - defensive, matches PyInstaller env
        return False
    return True


def build_with_pyinstaller(
    target: str,
    repo_root: Path,
    dist_dir: Path,
    work_dir: Path,
    spec_dir: Path,
) -> Path:
    """Invoke PyInstaller and return the path to the produced bundle."""

    icon_candidate = repo_root / "desktop_app" / "resources" / "TaskCollector.icns"
    icon_arg = str(icon_candidate) if icon_candidate.exists() else "NONE"

    # Build PyInstaller command with all necessary hidden imports and data
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        str(repo_root / "desktop_app" / "task_collector_app.py"),
        "--name",
        APP_NAME,
        "--windowed",
        "--noconfirm",
        "--clean",
        "--paths",
        str(repo_root),
        "--distpath",
        str(dist_dir),
        "--workpath",
        str(work_dir),
        "--specpath",
        str(spec_dir),
        "--icon",
        icon_arg,
        # Add entire src package
        "--add-data",
        f"{repo_root / 'src'}:src",
        # Hidden imports for dependencies
        "--hidden-import",
        "google.cloud",
        "--hidden-import",
        "google.cloud.storage",
        "--hidden-import",
        "google.auth",
        "--hidden-import",
        "google.auth.transport.requests",
        "--hidden-import",
        "google.protobuf",
        "--hidden-import",
        "playwright",
        "--hidden-import",
        "playwright.async_api",
        "--hidden-import",
        "playwright.sync_api",
        "--hidden-import",
        "dotenv",
        "--hidden-import",
        "sqlite3",
        "--hidden-import",
        "multiprocessing",
        "--hidden-import",
        "multiprocessing.connection",
        # Hidden imports for src modules
        "--hidden-import",
        "src.config.storage_config",
        "--hidden-import",
        "src.config.browser_config",
        "--hidden-import",
        "src.config.initial_tasks",
        "--hidden-import",
        "src.browser.stealth_browser",
        "--hidden-import",
        "src.source_data.database",
        "--hidden-import",
        "src.tasks.task",
        "--hidden-import",
        "desktop_app.task_worker",
        # Collect all submodules
        "--collect-all",
        "google.cloud",
        "--collect-all",
        "google.auth",
        "--collect-all",
        "google.protobuf",
        "--collect-all",
        "playwright",
        "--collect-all",
        "greenlet",
        # macOS specific
    ]

    if target == "macos":
        cmd.extend(
            [
                "--osx-bundle-identifier",
                "com.taskcollector.app",
            ]
        )

    run(cmd)

    if target == "macos":
        bundle = dist_dir / f"{APP_NAME}.app"
    else:
        bundle = dist_dir / APP_NAME

    if not bundle.exists():
        raise BuildError(f"PyInstaller bundle not found at {bundle}")

    return bundle


def copy_browsers_into_bundle(bundle: Path, browsers_dir: Path, target: str) -> None:
    """Copy the Playwright browser runtime into the built bundle."""

    if target == "macos":
        dest = bundle / "Contents" / "MacOS" / "playwright-browsers"
    else:
        dest = bundle / "playwright-browsers"

    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(browsers_dir, dest)


def write_instructions(package_root: Path, target: str) -> None:
    """Create a short setup guide for non-technical users."""

    instructions = package_root / "How to install.txt"
    if target == "macos":
        body = (
            "Task Collector for macOS\n"
            "=========================\n\n"
            "1. Double-click the ZIP to extract `TaskCollector.app`.\n"
            "2. Move it into `Applications` (optional but recommended).\n"
            "3. On first launch macOS may warn that the app is from the internet.\n"
            "   Right-click the app, choose Open, then confirm.\n"
            "4. The bundled Chromium browser is included, so no extra setup is required.\n"
        )
    else:
        body = (
            "Task Collector for Windows\n"
            "==========================\n\n"
            "1. Double-click the ZIP and extract the `TaskCollector` folder.\n"
            "2. Open the folder and run `TaskCollector.exe`.\n"
            "3. Keep the `playwright-browsers` folder alongside the EXE.\n"
            "4. Windows SmartScreen may warn about the download. Click More Info -> Run Anyway.\n"
        )

    instructions.write_text(body, encoding="utf-8")


def stage_release_payload(bundle: Path, target: str, staging_root: Path) -> Path:
    """Prepare the final directory tree that will be zipped."""

    ensure_empty_dir(staging_root)

    if target == "macos":
        staged_bundle = staging_root / f"{APP_NAME}.app"
    else:
        staged_bundle = staging_root / APP_NAME

    if staged_bundle.exists():
        shutil.rmtree(staged_bundle)

    shutil.copytree(bundle, staged_bundle)
    write_instructions(staging_root, target)
    return staging_root


def make_zip(source_dir: Path, destination: Path) -> Path:
    """Create a ZIP archive and return its path."""

    if destination.exists():
        destination.unlink()

    archive_base = destination.with_suffix("")
    shutil.make_archive(str(archive_base), "zip", root_dir=str(source_dir))
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=sorted(SUPPORTED_TARGETS), required=True)
    parser.add_argument(
        "--version",
        help="Version label to embed in the ZIP filename (defaults to env APP_VERSION or 'local')",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Directory where the final ZIP will be written (defaults to desktop_app/dist)",
    )
    args = parser.parse_args()

    target = args.target
    repo_root = Path(__file__).resolve().parents[1]

    version = args.version or os.environ.get("APP_VERSION") or "local"
    output_root = args.output_dir or (repo_root / "desktop_app" / "dist")
    output_root.mkdir(parents=True, exist_ok=True)

    build_root = repo_root / "desktop_app" / "build" / target
    pyinstaller_work = build_root / "pyinstaller" / "build"
    pyinstaller_dist = build_root / "pyinstaller" / "dist"
    spec_dir = build_root / "pyinstaller"
    browsers_dir = build_root / "playwright-browsers"
    staging_dir = build_root / "staging"

    ensure_empty_dir(pyinstaller_dist)
    ensure_empty_dir(pyinstaller_work)
    ensure_empty_dir(spec_dir)

    install_playwright_browser(browsers_dir)
    bundle = build_with_pyinstaller(
        target, repo_root, pyinstaller_dist, pyinstaller_work, spec_dir
    )
    copy_browsers_into_bundle(bundle, browsers_dir, target)

    staged_payload = stage_release_payload(bundle, target, staging_dir)

    zip_name = f"{APP_NAME}-{target}-{version}.zip"
    zip_path = output_root / zip_name
    make_zip(staged_payload, zip_path)

    print(zip_path)


if __name__ == "__main__":
    try:
        main()
    except BuildError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    if module_importable("google.cloud._storage_v2"):
        cmd.extend(["--hidden-import", "google.cloud._storage_v2"])

    if module_importable("grpc"):
        cmd.extend(["--hidden-import", "grpc", "--collect-all", "grpc"])
        if module_importable("grpc._cython.cygrpc"):
            cmd.extend(["--hidden-import", "grpc._cython.cygrpc"])
