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
- Each session lands in `data/<env>/captures/task_<id>/<timestamp>/`; the manifest records the Playwright context config plus capture timestamps, while sibling folders store storage snapshots, network bodies, request logs, and SQLite/asset exports so the entire environment can be replayed offline.
- The active task index lives at `data/<env>/tasks.db` along with useful artifacts under `data/<env>/screenshots/`, `data/<env>/videos/`, and `data/<env>/doms/`.

Launch a sandbox browser
```
python -m src.capture.sandbox --task-id <id> [--root data/dev/captures] [--allow-network-fallback] [--headed] [--safe-mode]
```
- Spins up a Playwright instance pointed at the recorded bundle and prints the CDP endpoint; press Ctrl+C when finished. Pass `--bundle <path>` to target a specific folder.
- Add `--safe-mode` to launch a headless Chromium with a minimal argument set if the default profile crashes; add `--headed` to force a visible window.

Replay a capture
```
python -m src.capture.replay <bundle_dir> [--headless] [--allow-network-fallback]
```
- Launches Chromium against a recorded bundle, honoring the stored environment config and storage state; missing resources optionally fall back to the live network.

Export tasks to JSONL
```
python src/tasks/db_to_jsonl_format.py [--prod]
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
python src/eval/browseruse.py --model gpt-5-nano [--prod] [--no-sandbox] [--sandbox-root <captures_dir>] [--sandbox-allow-network] [--sandbox-channel <channel>] [--sandbox-headed] [--sandbox-safe-mode]
```
- Uses `data/<env>/captures` by default, auto-selecting the newest bundle per task; pass `--no-sandbox` to fall back to the Kernel browser.
- Requires Playwright browsers installed via `setup.sh` and an `OPENAI_API_KEY`. A `KERNEL_API_KEY` is only needed when the sandbox is disabled or no bundle is found for a task.
- Default behaviour launches a headless Chromium; use `--sandbox-headed` for a visible window or `--sandbox-safe-mode` to retry with a reduced argument set if Chromium crashes.
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
