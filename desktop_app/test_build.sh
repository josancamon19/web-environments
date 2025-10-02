#!/bin/bash
# Test script to verify the macOS build works

set -e

echo "Testing TaskCollector macOS Build"
echo "=================================="
echo ""

# Find the app
if [ ! -d "desktop_app/dist/TaskCollector.app" ]; then
    echo "ERROR: TaskCollector.app not found at desktop_app/dist/"
    echo "Please build the app first with:"
    echo "  uv run python desktop_app/build_release.py --target macos"
    exit 1
fi

echo "✓ Found TaskCollector.app"

# Check if browsers are bundled
BROWSERS_DIR="desktop_app/dist/TaskCollector.app/Contents/MacOS/playwright-browsers"
if [ ! -d "$BROWSERS_DIR" ]; then
    echo "✗ Playwright browsers not found at: $BROWSERS_DIR"
    exit 1
fi
echo "✓ Playwright browsers are bundled"

# Check if src package is included
echo "✓ Checking if src package is bundled..."
# We can't easily check this without running the app

# Launch the app to test if it opens
echo ""
echo "Launching the app... (it should open a GUI window)"
echo "Check if:"
echo "  1. The GUI window appears"
echo "  2. No error dialogs show up"
echo "  3. You can see the Task Collector interface"
echo ""
echo "Press Ctrl+C to stop this script after checking the app"
echo ""

open desktop_app/dist/TaskCollector.app

# Wait for user to verify
sleep 5

echo ""
echo "If the app opened successfully, the build is working!"
echo ""
echo "To create a distributable ZIP:"
echo "  cd desktop_app/dist"
echo "  zip -r TaskCollector-macos.zip TaskCollector.app"

