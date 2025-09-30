Playwright Task Recorder

Requirements
- Python 3.11+ recommended

Setup
1) Create venv and install deps:
```
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Run data collection
```
source .venv/bin/activate
RECORDER_BROWSER_CHANNEL=chrome python main.py
```
- Provide the task source, type, and description when prompted; Ctrl+C ends a capture.
- Each session lands in `data/dev/captures/task_<id>/<timestamp>/`; the manifest records the Playwright context config plus capture timestamps, while sibling folders store network bodies, request logs, storage snapshots, and SQLite/asset exports for replay.
- The active task index lives at `data/dev/tasks.db`.
- And most importantly bundles of every page visited, so it can be treated as it's own sandbox later

Replay a capture
```
python -m src.capture.replay <bundle_dir> [--headless] [--allow-network-fallback]
```
- Launches Chromium against a recorded bundle, honoring the stored environment config and storage state; missing resources optionally fall back to the live network.

Export tasks to JSONL
```
python src/tasks/db_to_jsonl_format.py
```
- Reads `data/<env>/tasks.db`, emits tool-call trajectories to `data/<env>/tasks.jsonl`, and saves DOM snapshots to `data/<env>/doms/`.

Generate checkpoints
```
export OPENAI_API_KEY=...
python src/tasks/extract_checkpoints.py
```
- Uses the configured OpenAI model via `dspy` to enrich `data/dev/tasks.jsonl` with `checkpoints` and `checkpoints_reasoning`; adjust the hard-coded paths in the script if you need to target prod data.

Run Browser-Use agent
```
python src/eval/browseruse.py --model gpt-5-nano [--prod] [--sandbox-bundle <bundle_dir>] [--sandbox-allow-network]
```
- Requires `OPENAI_API_KEY`, `KERNEL_API_KEY`, and the Playwright browsers installed via `setup.sh`.
- Replays each task with the Browser-Use agent, saving traces, DOM dumps, and completions under `src/eval/results/browseruse-<model>.jsonl` and `src/eval/results/doms/`.

Evaluate completions
```
python src/eval/evaluate.py <model_name> [judge_model]
```
- Loads the Browser-Use outputs, compares them against human trajectories, and prints per-task and aggregate verdicts; run as `python src/eval/evaluate.py <model> [judge_model] [--prod]` so the script still finds `src/eval/results/browseruse-<model>.jsonl` and `data/<env>/tasks.jsonl`.

Storage
- SQLite DB at `data/<env>/tasks.db`
- Screenshots at `data/<env>/screenshots/`
- Video Tasks at `data/<env>/videos`

Notes
- This is an MVP and will capture response bodies which can be large. For production, consider size limits and redaction.
- To reduce CAPTCHA/detections, the recorder prefers launching a persistent Chrome profile and disables some automation flags. You can customize:
  - `RECORDER_BROWSER_CHANNEL` (default `chrome`)
  - `RECORDER_USER_DATA_DIR` (default `data/user-data`)
