#!/bin/bash
# Launcher script for task collector app with macOS bus error prevention

# Change to the project directory
cd "$(dirname "$0")/.."

# Activate the virtual environment
source venv/bin/activate

# Set environment variables to prevent bus errors
export DISPLAY=:0.0
export OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES
export PYTHON_COREAUDIO_ALLOW_INSECURE_REQUESTS=1

# Disable macOS App Nap and other optimizations that can cause issues
# caffeinate prevents the system from sleeping and can help with GUI stability
echo "Starting Task Collector with macOS optimizations disabled..."
caffeinate -dis python desktop_app/task_collector_app.py
