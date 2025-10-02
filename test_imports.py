#!/usr/bin/env python3
"""Test script to verify imports work correctly."""

try:
    from browser.stealth_browser import StealthBrowser
    print("✓ Imported StealthBrowser successfully")
except ImportError as e:
    print(f"✗ Failed to import StealthBrowser: {e}")

try:
    from config.initial_tasks import InitialTasks
    print("✓ Imported InitialTasks successfully")
except ImportError as e:
    print(f"✗ Failed to import InitialTasks: {e}")

try:
    from tasks.task import TaskManager, CreateTaskDto, Task
    print("✓ Imported Task classes successfully")
except ImportError as e:
    print(f"✗ Failed to import Task classes: {e}")

try:
    from utils.get_task_description import get_task_description_from_user
    print("✓ Imported get_task_description_from_user successfully")
except ImportError as e:
    print(f"✗ Failed to import get_task_description_from_user: {e}")

print("Import test completed.")
