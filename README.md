Minimal Playwright Task Recorder (MVP)

Requirements
- Python 3.11+ recommended
- macOS arm64 supported (adjust as needed)

Setup
1) Create venv and install deps:
```
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install playwright
python -m playwright install chromium
```

Run
```
source .venv/bin/activate
RECORDER_BROWSER_CHANNEL=chrome python recorder.py
```

Usage
- Enter a task description when prompted (e.g., "Buy me a coffee in DoorDash").
- A Chromium window opens. Interact freely.
- In the terminal:
  - Type `shot` to force a screenshot step.
  - Type `save`, then confirm with `y` to persist the task and close the browser.

What gets recorded
- Actions: click, contextmenu, keydown, input, scroll
- State changes: DOMContentLoaded, load, frame navigations
- Network: XHR/fetch requests and responses with headers, body (as bytes), and cookies
- DOM snapshot and a full-page screenshot at each step

Storage
- SQLite DB at `data/tasks.db`
- Screenshots at `data/screenshots/`

Notes
- This is an MVP and will capture response bodies which can be large. For production, consider size limits and redaction.
- To reduce CAPTCHA/detections, the recorder prefers launching a persistent Chrome profile and disables some automation flags. You can customize:
  - `RECORDER_BROWSER_CHANNEL` (default `chrome`)
  - `RECORDER_USER_DATA_DIR` (default `data/user-data`)