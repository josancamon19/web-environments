#!/usr/bin/env bash

set -euo pipefail

# Colors for readable output
GREEN="\033[0;32m"
YELLOW="\033[0;33m"
RED="\033[0;31m"
RESET="\033[0m"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_NAME="$(basename "$SCRIPT_DIR")"
VENV_DIR="$SCRIPT_DIR/venv"

info() {
  printf "%b[INFO]%b %s\n" "$GREEN" "$RESET" "$1"
}

warn() {
  printf "%b[WARN]%b %s\n" "$YELLOW" "$RESET" "$1"
}

error() {
  printf "%b[ERROR]%b %s\n" "$RED" "$RESET" "$1"
}

ensure_macos() {
  if [[ "$(uname -s)" != "Darwin" ]]; then
    error "This setup script is intended for macOS systems."
    exit 1
  fi
}

ensure_python() {
  if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN=$(command -v python3)
    info "Found Python at $PYTHON_BIN"
  else
    error "Python 3 is not installed."
    cat <<'EOF'
Please install Python 3 first:
  1. Open https://www.python.org/downloads/macos/
  2. Download the latest macOS installer (universal2).
  3. Run the installer and re-run this script.
EOF
    exit 1
  fi
}

create_venv() {
  if [[ -d "$VENV_DIR" ]]; then
    warn "Virtual environment already exists at $VENV_DIR"
  else
    info "Creating virtual environment..."
    python3 -m venv "$VENV_DIR"
  fi
  # shellcheck source=/dev/null
  source "$VENV_DIR/bin/activate"
  info "Virtual environment activated."
}

install_python_deps() {
  info "Upgrading pip..."
  python -m ensurepip --upgrade >/dev/null 2>&1 || true
  python -m pip install --upgrade pip setuptools wheel

  if [[ -f "$SCRIPT_DIR/requirements.txt" ]]; then
    info "Installing Python dependencies from requirements.txt..."
    python -m pip install -r "$SCRIPT_DIR/requirements.txt"
  else
    warn "requirements.txt not found. Skipping dependency installation."
  fi
}

install_playwright() {
  if python -c "import playwright" >/dev/null 2>&1; then
    info "Playwright Python package is available. Installing browser binaries..."
    python -m playwright install
  else
    warn "Playwright package not found. Skipping browser installation."
  fi
}

maybe_run_main() {
  if [[ -f "$SCRIPT_DIR/main.py" ]]; then
    read -r -p "Do you want to run 'python main.py' now? [y/N]: " RESPONSE
    if [[ "$RESPONSE" =~ ^[Yy]$ ]]; then
      info "Launching python main.py"
      python "$SCRIPT_DIR/main.py"
    else
      info "Setup complete. You can run 'source venv/bin/activate' and 'python main.py' later."
    fi
  else
    warn "main.py not found. Nothing to run."
  fi
}

ensure_macos
ensure_python
create_venv
install_python_deps
install_playwright
maybe_run_main

info "All done!"
