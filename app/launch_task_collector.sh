#!/bin/bash
# Launcher script for task collector app with macOS bus error prevention

# Change to the project directory
cd "$(dirname "$0")/.."

# Activate the virtual environment
source .venv/bin/activate

# Set environment variables to prevent bus errors
export DISPLAY=:0.0
export OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES
export PYTHON_COREAUDIO_ALLOW_INSECURE_REQUESTS=1
# Set Python path to include the project root and src directory
export PYTHONPATH="$(pwd)/src:$(pwd):$PYTHONPATH"
echo "PYTHONPATH: $PYTHONPATH"

# Set Playwright browsers path
export PLAYWRIGHT_BROWSERS_PATH="$(pwd)/playwright-browsers"
echo "PLAYWRIGHT_BROWSERS_PATH: $PLAYWRIGHT_BROWSERS_PATH"

# Check if Playwright browsers are installed
if [ ! -d "$PLAYWRIGHT_BROWSERS_PATH/chromium-"* ]; then
    echo "Playwright browsers not found. Installing..."
    python -m playwright install chromium
    if [ $? -ne 0 ]; then
        echo "Failed to install Playwright browsers. Please run 'python -m playwright install chromium' manually."
        exit 1
    fi
    echo "Playwright browsers installed successfully."
fi

# Disable macOS App Nap and other optimizations that can cause issues
# caffeinate prevents the system from sleeping and can help with GUI stability
echo "Starting Task Collector with macOS optimizations disabled..."
caffeinate -dis python -m app.task_collector_app
