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
MIN_PYTHON_VERSION="3.11"
PYTHON_BIN=""

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

version_ge() {
  # Returns true if version $1 >= version $2
  local IFS=.
  local i
  local -a ver1=($1) ver2=($2)
  local len=$(( ${#ver1[@]} > ${#ver2[@]} ? ${#ver1[@]} : ${#ver2[@]} ))

  for ((i = 0; i < len; i++)); do
    local v1=${ver1[i]:-0}
    local v2=${ver2[i]:-0}
    if ((10#$v1 > 10#$v2)); then
      return 0
    elif ((10#$v1 < 10#$v2)); then
      return 1
    fi
  done

  return 0
}

install_python311() {
  if command -v brew >/dev/null 2>&1; then
    info "Attempting to install Python ${MIN_PYTHON_VERSION} via Homebrew..."
    brew install python@3.11
    local brew_prefix
    brew_prefix=$(brew --prefix python@3.11 2>/dev/null || true)
    if [[ -n "$brew_prefix" && -x "$brew_prefix/bin/python3.11" ]]; then
      PYTHON_BIN="$brew_prefix/bin/python3.11"
      info "Installed Python ${MIN_PYTHON_VERSION} at $PYTHON_BIN"
      return
    fi
    error "Python ${MIN_PYTHON_VERSION} installation succeeded but expected binary not found."
  else
    error "Homebrew is required to install Python ${MIN_PYTHON_VERSION} automatically."
  fi

  cat <<'EOF'
Please install Python 3.11 manually:
  1. Install Homebrew from https://brew.sh/ (if not available).
  2. Run 'brew install python@3.11'.
  3. Re-run this script.
EOF
  exit 1
}

ensure_python() {
  local detected_version=""

  if command -v python3 >/dev/null 2>&1; then
    local candidate
    candidate=$(command -v python3)
    detected_version=$("$candidate" -c 'import sys; print(".".join(map(str, sys.version_info[:3])))' 2>/dev/null || true)
    if [[ -n "$detected_version" ]] && version_ge "$detected_version" "$MIN_PYTHON_VERSION"; then
      PYTHON_BIN="$candidate"
      info "Found Python $detected_version at $PYTHON_BIN"
      return
    fi
    warn "Detected python3 version $detected_version, but >= ${MIN_PYTHON_VERSION} is required."
  else
    warn "python3 command not found in PATH."
  fi

  if command -v python3.11 >/dev/null 2>&1; then
    PYTHON_BIN=$(command -v python3.11)
    detected_version=$("$PYTHON_BIN" -c 'import sys; print(".".join(map(str, sys.version_info[:3])))')
    info "Using Python $detected_version at $PYTHON_BIN"
    return
  fi

  install_python311
}

create_venv() {
  if [[ -d "$VENV_DIR" ]]; then
    warn "Virtual environment already exists at $VENV_DIR"
  else
    info "Creating virtual environment..."
    "$PYTHON_BIN" -m venv "$VENV_DIR"
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
