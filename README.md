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
- Network: XHR/fetch requests and responses with headers, body (as bytes), and cookies
- DOM snapshot and a full-page screenshot at each step
- [ ] will store HAR files of the web environments + spin up a fake server out of requests processed.

Storage
- SQLite DB at `data/tasks.db`
- Screenshots at `data/screenshots/`
- Video Tasks at `data/videos`

Notes
- This is an MVP and will capture response bodies which can be large. For production, consider size limits and redaction.
- To reduce CAPTCHA/detections, the recorder prefers launching a persistent Chrome profile and disables some automation flags. You can customize:
  - `RECORDER_BROWSER_CHANNEL` (default `chrome`)
  - `RECORDER_USER_DATA_DIR` (default `data/user-data`)