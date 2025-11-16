#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

usage() {
  cat <<'EOF'
Usage: run.sh [--force-ignore] [DATA_ROOT ...]

Runs the post-processing pipeline in three groups:
  1. Run postprocess-toolcalls for every DATA_ROOT
  2. Run postprocess-credentials, then postprocess-set-checkpoints
     (sequentially per DATA_ROOT)
  3. Run determine-ignore in parallel for every DATA_ROOT

If no DATA_ROOT is provided, the script defaults to <repo>/data.

Options:
  --force-ignore   Pass --force to the _4_determine_ignore.py script.
  -h, --help       Show this message.
EOF
}

cd "$REPO_ROOT"

declare -a DATA_ROOTS=()
FORCE_IGNORE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    --force-ignore)
      FORCE_IGNORE=1
      shift
      ;;
    *)
      if [[ -d "$1" ]]; then
        DATA_ROOTS+=("$(cd "$1" && pwd)")
        shift
      else
        echo "Error: data root '$1' does not exist." >&2
        exit 1
      fi
      ;;
  esac
done

if [[ ${#DATA_ROOTS[@]} -eq 0 ]]; then
  DEFAULT_ROOT="$REPO_ROOT/data"
  if [[ -d "$DEFAULT_ROOT" ]]; then
    DATA_ROOTS+=("$DEFAULT_ROOT")
  else
    echo "Error: default data root '$DEFAULT_ROOT' not found. Provide at least one DATA_ROOT." >&2
    exit 1
  fi
fi

run_step() {
  local data_root="$1"
  local label="$2"
  shift 2

  local cmd_display
  printf -v cmd_display '%q ' "$@"

  echo
  echo "[$(basename "$data_root")] $label"
  echo "  -> ${cmd_display% }"

  TASK_COLLECTOR_DATA_ROOT="$data_root" "$@"
}

declare -a bg_pids=()

run_step_bg() {
  local data_root="$1"
  local label="$2"
  shift 2

  local cmd_display
  printf -v cmd_display '%q ' "$@"

  echo
  echo "[$(basename "$data_root")] $label (background)"
  echo "  -> ${cmd_display% }"

  TASK_COLLECTOR_DATA_ROOT="$data_root" "$@" &
  local pid=$!
  bg_pids+=("$pid")
}

cleanup() {
  local exit_code=$1
  if [[ ${#bg_pids[@]} -gt 0 ]]; then
    for pid in "${bg_pids[@]}"; do
      if kill -0 "$pid" >/dev/null 2>&1; then
        kill "$pid" >/dev/null 2>&1 || true
      fi
    done
  fi
}

trap 'cleanup "$?"' EXIT
trap 'exit 1' INT TERM

echo
echo "=== Group 1/3: postprocess-toolcalls ==="
for data_root in "${DATA_ROOTS[@]}"; do
  run_step "$data_root" "Step 1: postprocess-toolcalls" uv run postprocess-toolcalls
done

echo
echo "=== Group 2/3: postprocess-credentials → postprocess-set-checkpoints ==="
for data_root in "${DATA_ROOTS[@]}"; do
  run_step "$data_root" "Step 2: postprocess-credentials" uv run postprocess-credentials
  run_step "$data_root" "Step 3: postprocess-set-checkpoints" uv run postprocess-set-checkpoints
done

echo
echo "=== Group 3/3: determine-ignore (parallel per data root) ==="
for data_root in "${DATA_ROOTS[@]}"; do
  declare -a force_args=()
  if [[ $FORCE_IGNORE -eq 1 ]]; then
    force_args+=(--force)
  fi

  if [[ ${#force_args[@]} -gt 0 ]]; then
    run_step_bg \
      "$data_root" \
      "Step 4: determine-ignore" \
      uv run python -m scripts.postprocessing._4_determine_ignore \
      "${force_args[@]}"
  else
    run_step_bg \
      "$data_root" \
      "Step 4: determine-ignore" \
      uv run python -m scripts.postprocessing._4_determine_ignore
  fi
done

for pid in "${bg_pids[@]}"; do
  wait "$pid"
done

echo
echo "All data roots processed successfully."

