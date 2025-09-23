# Task Collector Desktop App

This folder contains a Tkinter-based desktop application that mirrors the original `main.py` CLI workflow. It lets non-technical users collect new tasks through a simple form and then launches the stealth browser recorder automatically.

## Running the app directly

1. Create/activate a Python environment (same one used for the project is fine).
   Make sure the interpreter includes Tk support (`python -m tkinter` should open a
   small window). On macOS with Homebrew Python you may need `brew install python-tk@3.13`,
   or install Python from python.org which bundles Tk by default.
2. Install project dependencies if you have not already:
   ```bash
   python -m pip install -r requirements.txt
   playwright install
   ```
3. Start the desktop app:
   ```bash
   python desktop_app/task_collector_app.py
   ```

The GUI will prompt for the same inputs as the CLI (source, task type, description). After clicking **Launch Task**, a Chromium browser starts recording. When the task is done, click **Complete Task** to close the browser and persist recordings/answers.

## Building a distributable bundle (PyInstaller)

The simplest way to ship the app to volunteers is to package it with [PyInstaller](https://pyinstaller.org/). Run the following from the project root on the platform you're building for (Mac builds on macOS, Windows builds on Windows, etc.):

```bash
python -m pip install pyinstaller
pyinstaller desktop_app/task_collector_app.py \
  --name TaskCollector \
  --windowed \
  --noconfirm \
  --paths .
```

This produces a standalone folder under `dist/TaskCollector` with the executable and required Python modules. Bundle that directory (e.g., zip it) and share it with collaborators.

### Notes for distribution

- The packaged app still depends on Playwright's downloaded browsers. Before building, make sure you have run `playwright install chromium` so PyInstaller picks up the binaries. Depending on the target OS, you may need to copy the `playwright` browser cache into the distribution or provide a setup step for end users.
- The application writes logs and recordings to the same locations as the CLI (`recorder_debug.log`, the `data/` folder, etc.), so keep the folder structure intact when sharing.
- If collaborators see security prompts the first time they launch the executable (common on macOS), instruct them to allow the app to run.

## Cleaning build artifacts

PyInstaller leaves build outputs in `build/` and `dist/`. Remove them when you regenerate a clean package:

```bash
rm -rf build dist TaskCollector.spec
```

Feel free to adapt the packaging command or create OS-specific icons/spec files if you need a more branded experience.
