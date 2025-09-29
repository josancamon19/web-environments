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

Run
```
source .venv/bin/activate
RECORDER_BROWSER_CHANNEL=chrome python index.py
```

Usage
- Enter a task description when prompted (e.g., "Buy me a coffee in DoorDash").
- A Chromium window opens. Interact freely.
- For now ctrl + c to finish

What gets recorded
- Actions: click, contextmenu, keydown, input, scroll
- State changes: DOMContentLoaded, load, frame navigations
- Network: all browser requests (documents, XHR/fetch, assets) with normalized headers, payloads, and byte-accurate bodies
- DOM snapshot and a full-page screenshot at each step
- Offline bundle: every run now emits a replayable package under `data/<env>/captures/task_<id>/<timestamp>/` containing the manifest, resource bodies, storage state, and DB exports so sessions can be reproduced without live network access.

Offline capture & replay
- Capture happens automatically when you launch `main.py`; the session directory (see above) aggregates manifest, raw resources, storage dumps, and database extracts.
- To replay a bundle locally, run `python -m src.capture.replay <bundle_dir>`; the helper spins up Chromium with recorded storage state, routes requests to the archived resources, and opens the first captured document. Use `--allow-network-fallback` if you want missing requests to fall back to the live web during debugging.
- Bundles also ship with `steps.jsonl`, `requests_db.jsonl`, and `responses_db.jsonl` so you can audit actions or feed downstream tooling without touching the SQLite database.

Storage
- SQLite DB at `data/tasks.db`
- Screenshots at `data/screenshots/`
- Video Tasks at `data/videos`

Notes
- This is an MVP and will capture response bodies which can be large. For production, consider size limits and redaction.
- To reduce CAPTCHA/detections, the recorder prefers launching a persistent Chrome profile and disables some automation flags. You can customize:
  - `RECORDER_BROWSER_CHANNEL` (default `chrome`)
  - `RECORDER_USER_DATA_DIR` (default `data/user-data`)
