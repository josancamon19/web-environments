#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if ! command -v pyinstaller >/dev/null 2>&1; then
    echo "PyInstaller is not installed. Install it with: python -m pip install pyinstaller" >&2
    exit 1
fi

pyinstaller desktop_app/task_collector_app.py \
  --name TaskCollector \
  --windowed \
  --noconfirm \
  --paths .
