"""Tkinter desktop app that orchestrates task collection sessions."""

import logging
import multiprocessing
import os
import queue
import shutil
import subprocess
import sys
import threading
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

# macOS-specific fix for tkinter bus errors
if sys.platform == "darwin":
    # Disable macOS App Nap which can cause issues with tkinter
    os.environ["PYTHON_COREAUDIO_ALLOW_INSECURE_REQUESTS"] = "1"
    # Ensure we're using the main display
    if "DISPLAY" not in os.environ:
        os.environ["DISPLAY"] = ":0.0"

import tkinter as tk
from tkinter import messagebox
from tkinter.scrolledtext import ScrolledText
from tkinter import ttk
from typing import Optional
import sqlite3

from src.config.storage_config import DATA_DIR

try:
    from google.cloud import storage
except ModuleNotFoundError:  # pragma: no cover - optional dependency for uploads
    storage = None  # type: ignore[assignment]

import base64
from dotenv import load_dotenv
import json

load_dotenv()

_GOOGLE_CREDS_READY = False
_GOOGLE_CREDS_ERROR: Optional[str] = None
_GOOGLE_CREDS_PATH: Optional[Path] = None


def _load_env_files() -> None:
    """Attempt to load .env files from common locations."""

    candidate_dirs = []

    # Allow explicit override via environment variable
    override = os.environ.get("TASK_COLLECTOR_ENV_PATH")
    if override:
        candidate_dirs.append(Path(override))

    candidate_dirs.extend(
        [
            Path.cwd(),
            Path(__file__).resolve().parent,
            Path(__file__).resolve().parents[1],
            Path(__file__).resolve().parents[2],
            DATA_DIR,
            Path.home() / ".taskcollector",
        ]
    )

    seen = set()
    for directory in candidate_dirs:
        try:
            directory = directory.resolve()
        except FileNotFoundError:
            continue
        if directory in seen:
            continue
        seen.add(directory)
        dotenv_path = directory / ".env"
        if dotenv_path.exists():
            load_dotenv(dotenv_path=dotenv_path, override=False)


def ensure_google_credentials(
    creds_base64: Optional[str] = None,
) -> tuple[bool, Optional[str]]:
    """Ensure Google credentials file exists for storage uploads.

    Expects base64-encoded Google Cloud service account JSON credentials.
    """

    global _GOOGLE_CREDS_READY  # pylint: disable=global-statement
    global _GOOGLE_CREDS_ERROR  # pylint: disable=global-statement
    global _GOOGLE_CREDS_PATH  # pylint: disable=global-statement

    if _GOOGLE_CREDS_READY:
        logger.debug("Google credentials already ready")
        return True, None

    if not creds_base64:
        message = (
            "Google Cloud Storage credentials are required to upload collected data.\n\n"
            "Please paste your base64-encoded credentials in the Settings field above."
        )
        _GOOGLE_CREDS_ERROR = message
        return False, message

    creds_str = creds_base64.strip()
    logger.debug(f"Processing credentials input (length: {len(creds_str)})")

    if getattr(sys, "frozen", False):
        creds_path = Path(tempfile.gettempdir()) / "google-credentials.json"
    else:
        creds_path = Path("google-credentials.json")

    try:
        # Decode base64 credentials
        creds_bytes = base64.b64decode(creds_str)
        creds_json_str = creds_bytes.decode("utf-8")

        # Clean the JSON string - ensure it starts with { and ends with }
        creds_json_str = creds_json_str.strip()

        # Find the first { and last }
        start_idx = creds_json_str.find("{")
        end_idx = creds_json_str.rfind("}")

        if start_idx == -1 or end_idx == -1 or start_idx >= end_idx:
            raise ValueError("Invalid JSON structure - missing braces")

        # Extract clean JSON
        clean_json = creds_json_str[start_idx : end_idx + 1]

        # Validate it's proper JSON
        json.loads(clean_json)

        # Write the clean JSON to file (overwrite, not append)
        creds_path.parent.mkdir(parents=True, exist_ok=True)
        creds_path.write_text(clean_json, encoding="utf-8")
        logger.debug(
            f"Credentials written to {creds_path} (size: {len(clean_json)} chars)"
        )

        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(creds_path)
        _GOOGLE_CREDS_READY = True
        _GOOGLE_CREDS_ERROR = None
        _GOOGLE_CREDS_PATH = creds_path
        return True, None
    except Exception as exc:  # pragma: no cover - defensive
        message = (
            f"Failed to setup Google Cloud credentials: {exc}\n\n"
            "The credentials must be base64-encoded Google Cloud service account JSON.\n"
            "Contact your administrator for assistance."
        )
        _GOOGLE_CREDS_ERROR = message
        logger.error(f"Credential setup failed: {exc}")
        return False, message


# Config file to store user settings
CONFIG_FILE = Path(DATA_DIR) / ".user_config.json"


class UsernameDialog(tk.Toplevel):
    """Simple dialog to ask for username."""

    def __init__(self, parent):
        super().__init__(parent)
        self.parent = parent
        self.result = None

        self.title("Username Required")
        self.transient(parent)
        self.grab_set()

        self.protocol("WM_DELETE_WINDOW", self.cancel)

        # Create and pack widgets
        self.create_widgets()

        # Center the dialog
        self.center_window()

        # Focus on entry
        self.username_entry.focus_set()

    def create_widgets(self):
        main_frame = tk.Frame(self, padx=20, pady=20)
        main_frame.pack(fill=tk.BOTH, expand=True)

        label = tk.Label(
            main_frame,
            text="Please enter your username:\n(This will be used to identify your uploads)",
            justify=tk.LEFT,
        )
        label.pack(anchor=tk.W, pady=(0, 10))

        self.username_entry = tk.Entry(main_frame, width=30)
        self.username_entry.pack(fill=tk.X, pady=(0, 10))

        button_frame = tk.Frame(main_frame)
        button_frame.pack(fill=tk.X)

        ok_button = tk.Button(button_frame, text="OK", command=self.ok, width=10)
        ok_button.pack(side=tk.RIGHT, padx=(5, 0))

        cancel_button = tk.Button(
            button_frame, text="Cancel", command=self.cancel, width=10
        )
        cancel_button.pack(side=tk.RIGHT)

        # Bind Enter key to OK
        self.bind("<Return>", lambda e: self.ok())

    def center_window(self):
        self.update_idletasks()
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        window_width = self.winfo_width()
        window_height = self.winfo_height()
        x = (screen_width - window_width) // 2
        y = (screen_height - window_height) // 2
        self.geometry(f"+{x}+{y}")

    def ok(self):
        username = self.username_entry.get().strip()
        if not username:
            messagebox.showwarning(
                "Invalid Username", "Please enter a username.", parent=self
            )
            return
        # Sanitize username for filename
        self.result = "".join(c for c in username if c.isalnum() or c in "-_")
        if not self.result:
            messagebox.showwarning(
                "Invalid Username",
                "Username must contain alphanumeric characters.",
                parent=self,
            )
            return
        self.destroy()

    def cancel(self):
        self.result = None
        self.destroy()

    def show(self):
        self.wait_window()
        return self.result


class UploadProgressDialog(tk.Toplevel):
    """Dialog to show upload progress."""

    def __init__(self, parent):
        super().__init__(parent)
        self.parent = parent

        self.title("Uploading Data")
        self.transient(parent)
        self.grab_set()

        self.protocol(
            "WM_DELETE_WINDOW", lambda: None
        )  # Disable close button during upload

        # Create widgets
        self.create_widgets()

        # Center the dialog
        self.center_window()

    def create_widgets(self):
        main_frame = tk.Frame(self, padx=30, pady=20)
        main_frame.pack(fill=tk.BOTH, expand=True)

        self.status_label = tk.Label(
            main_frame, text="Preparing upload...", font=("Helvetica", 11)
        )
        self.status_label.pack(pady=(0, 15))

        self.progress = ttk.Progressbar(
            main_frame, length=400, mode="determinate", maximum=100
        )
        self.progress.pack(pady=(0, 10))

        self.detail_label = tk.Label(
            main_frame, text="", fg="gray", font=("Helvetica", 9)
        )
        self.detail_label.pack()

    def center_window(self):
        self.update_idletasks()
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        window_width = 500
        window_height = 150
        x = (screen_width - window_width) // 2
        y = (screen_height - window_height) // 2
        self.geometry(f"{window_width}x{window_height}+{x}+{y}")

    def update_progress(self, status: str, progress: float, detail: str = ""):
        """Update the progress bar and labels.

        Args:
            status: Main status text
            progress: Progress value (0-100)
            detail: Optional detail text
        """
        self.status_label.config(text=status)
        self.progress["value"] = progress
        if detail:
            self.detail_label.config(text=detail)
        self.update_idletasks()


class TextAreaDialog(tk.Toplevel):
    """Custom dialog with a text area for multi-line input."""

    def __init__(self, parent, title="Input", prompt="Enter text:", initial_text=""):
        super().__init__(parent)
        self.parent = parent
        self.result = None

        self.title(title)
        self.transient(parent)
        self.grab_set()

        # Make dialog modal and centered
        self.protocol("WM_DELETE_WINDOW", self.cancel)

        # Create and pack widgets
        self.create_widgets(prompt, initial_text)

        # Center the dialog
        self.center_window()

        # Focus on text area
        self.text_area.focus_set()

    def create_widgets(self, prompt, initial_text):
        # Main frame with padding
        main_frame = tk.Frame(self, padx=20, pady=20)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Prompt label
        label = tk.Label(main_frame, text=prompt, wraplength=400)
        label.pack(anchor=tk.W, pady=(0, 10))

        # Text area with scrollbar
        text_frame = tk.Frame(main_frame)
        text_frame.pack(fill=tk.BOTH, expand=True)

        self.text_area = tk.Text(text_frame, width=60, height=15, wrap=tk.WORD)
        scrollbar = tk.Scrollbar(text_frame, command=self.text_area.yview)
        self.text_area.config(yscrollcommand=scrollbar.set)

        self.text_area.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        if initial_text:
            self.text_area.insert("1.0", initial_text)

        # Button frame
        button_frame = tk.Frame(main_frame)
        button_frame.pack(fill=tk.X, pady=(10, 0))

        # OK and Cancel buttons
        ok_button = tk.Button(button_frame, text="OK", command=self.ok, width=10)
        ok_button.pack(side=tk.RIGHT, padx=(5, 0))

        cancel_button = tk.Button(
            button_frame, text="Cancel", command=self.cancel, width=10
        )
        cancel_button.pack(side=tk.RIGHT)

        # Bind Enter key to OK (Ctrl+Enter for multiline)
        self.bind("<Control-Return>", lambda e: self.ok())

    def center_window(self):
        self.update_idletasks()

        # Get screen dimensions
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()

        # Get window dimensions
        window_width = self.winfo_width()
        window_height = self.winfo_height()

        # Calculate position
        x = (screen_width - window_width) // 2
        y = (screen_height - window_height) // 2

        self.geometry(f"+{x}+{y}")

    def ok(self):
        self.result = self.text_area.get("1.0", tk.END).strip()
        self.destroy()

    def cancel(self):
        self.result = None
        self.destroy()

    def show(self):
        self.wait_window()
        return self.result


class TasksViewDialog(tk.Toplevel):
    """Dialog to display tasks from the database in a table format."""

    def __init__(self, parent):
        super().__init__(parent)
        self.parent = parent

        self.title("View Collected Tasks")
        self.transient(parent)

        # Make dialog large enough for table
        self.geometry("1200x600")

        # Create and pack widgets
        self.create_widgets()

        # Center the dialog
        self.center_window()

        # Load tasks
        self.load_tasks()

    def create_widgets(self):
        # Main frame with padding
        main_frame = tk.Frame(self, padx=10, pady=10)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Title
        title_label = tk.Label(
            main_frame, text="Collected Tasks", font=("Helvetica", 14, "bold")
        )
        title_label.pack(anchor=tk.W, pady=(0, 10))

        # Instructions
        instructions = tk.Label(
            main_frame,
            text="Select a task and click 'Delete Selected' or right-click for options",
            fg="gray",
        )
        instructions.pack(anchor=tk.W, pady=(0, 5))

        # Create frame for treeview and scrollbars
        tree_frame = tk.Frame(main_frame)
        tree_frame.pack(fill=tk.BOTH, expand=True)

        # Create scrollbars
        vsb = tk.Scrollbar(tree_frame, orient="vertical")
        hsb = tk.Scrollbar(tree_frame, orient="horizontal")

        # Create treeview
        columns = (
            "ID",
            "Description",
            "Type",
            "Source",
            "Website",
            "Answer",
            "Created At",
            "Duration",
            "Video Path",
        )
        self.tree = ttk.Treeview(
            tree_frame,
            columns=columns,
            show="headings",
            yscrollcommand=vsb.set,
            xscrollcommand=hsb.set,
        )

        # Configure scrollbars
        vsb.config(command=self.tree.yview)
        hsb.config(command=self.tree.xview)

        # Create context menu for edit and delete options
        self.context_menu = tk.Menu(self, tearoff=0)
        self.context_menu.add_command(label="Edit Website", command=self.edit_website)
        self.context_menu.add_command(label="Edit Answer", command=self.edit_answer)
        self.context_menu.add_separator()
        self.context_menu.add_command(
            label="Delete Task", command=self.delete_selected_task
        )

        # Bind right-click to show context menu
        self.tree.bind("<Button-2>", self.show_context_menu)  # Mac right-click
        self.tree.bind(
            "<Button-3>", self.show_context_menu
        )  # Windows/Linux right-click

        # Bind double-click to edit
        self.tree.bind("<Double-Button-1>", lambda e: self.edit_website())

        # Define column headings and widths
        self.tree.heading("ID", text="ID")
        self.tree.heading("Description", text="Description")
        self.tree.heading("Type", text="Type")
        self.tree.heading("Source", text="Source")
        self.tree.heading("Website", text="Website")
        self.tree.heading("Answer", text="Answer")
        self.tree.heading("Created At", text="Created At")
        self.tree.heading("Duration", text="Duration (s)")
        self.tree.heading("Video Path", text="Video Path")

        # Set column widths
        self.tree.column("ID", width=50)
        self.tree.column("Description", width=250)
        self.tree.column("Type", width=100)
        self.tree.column("Source", width=100)
        self.tree.column("Website", width=200)
        self.tree.column("Answer", width=150)
        self.tree.column("Created At", width=150)
        self.tree.column("Duration", width=80)
        self.tree.column("Video Path", width=200)

        # Grid layout
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")

        # Configure grid weights
        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)

        # Add info label
        self.info_label = tk.Label(main_frame, text="", fg="gray")
        self.info_label.pack(anchor=tk.W, pady=(5, 0))

        # Close button
        button_frame = tk.Frame(main_frame)
        button_frame.pack(fill=tk.X, pady=(10, 0))

        close_button = tk.Button(
            button_frame, text="Close", command=self.destroy, width=10
        )
        close_button.pack(side=tk.RIGHT)

        # Refresh button
        refresh_button = tk.Button(
            button_frame, text="Refresh", command=self.load_tasks, width=10
        )
        refresh_button.pack(side=tk.RIGHT, padx=(0, 5))

        # Delete button
        delete_button = tk.Button(
            button_frame,
            text="Delete Selected",
            command=self.delete_selected_task,
            width=15,
            fg="red",
        )
        delete_button.pack(side=tk.RIGHT, padx=(0, 5))

    def center_window(self):
        self.update_idletasks()

        # Get screen dimensions
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()

        # Get window dimensions
        window_width = self.winfo_width()
        window_height = self.winfo_height()

        # Calculate position
        x = (screen_width - window_width) // 2
        y = (screen_height - window_height) // 2

        self.geometry(f"+{x}+{y}")

    def load_tasks(self):
        """Load tasks from the database and populate the treeview."""
        # Clear existing items
        for item in self.tree.get_children():
            self.tree.delete(item)

        db_path = Path(DATA_DIR) / "tasks.db"

        if not db_path.exists():
            self.info_label.config(
                text="No tasks database found yet. Complete some tasks first."
            )
            return

        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()

            # Query to get tasks with the requested fields
            cursor.execute("""
                SELECT id, description, task_type, source, website, answer, created_at, duration_seconds, video_path
                FROM tasks
                ORDER BY created_at DESC
            """)

            tasks = cursor.fetchall()
            conn.close()

            if not tasks:
                self.info_label.config(text="No tasks found in the database.")
                return

            # Populate treeview
            for task in tasks:
                (
                    task_id,
                    description,
                    task_type,
                    source,
                    website,
                    answer,
                    created_at,
                    duration,
                    video_path,
                ) = task

                # Format values for display
                description = (
                    (description or "")[:100] + "..."
                    if description and len(description) > 100
                    else description or ""
                )

                # Format website for display
                website_display = (
                    (website or "")[:50] + "..."
                    if website and len(website) > 50
                    else website or ""
                )

                answer = (
                    (answer or "")[:50] + "..."
                    if answer and len(answer) > 50
                    else answer or ""
                )

                # Format duration to 2 decimal places if it exists
                if duration is not None:
                    duration = f"{duration:.2f}"
                else:
                    duration = ""

                # Show just the filename for video path
                if video_path:
                    video_path = Path(video_path).name
                else:
                    video_path = ""

                # Insert into treeview
                self.tree.insert(
                    "",
                    tk.END,
                    values=(
                        task_id or "",
                        description,
                        task_type or "",
                        source or "",
                        website_display,
                        answer,
                        created_at or "",
                        duration,
                        video_path,
                    ),
                )

            self.info_label.config(text=f"Showing {len(tasks)} task(s)")

        except Exception as e:
            self.info_label.config(text=f"Error loading tasks: {str(e)}", fg="red")

    def show_context_menu(self, event):
        """Show the context menu at the clicked position."""
        # Select the item under the mouse
        item = self.tree.identify_row(event.y)
        if item:
            self.tree.selection_set(item)
            self.context_menu.post(event.x_root, event.y_root)

    def delete_selected_task(self):
        """Delete the selected task and all related data after confirmation."""
        selected_items = self.tree.selection()
        if not selected_items:
            return

        # Get task details from the selected row
        item = selected_items[0]
        values = self.tree.item(item, "values")
        task_id = values[0]
        description = values[1]

        # Show confirmation dialog
        message = f"Are you sure you want to delete task {task_id}?\n\nDescription: {description}\n\nThis will permanently delete:\n• Task record\n• All steps\n• All requests and responses\n• DOM files\n• Screenshots\n• Capture files"
        if not messagebox.askyesno("Confirm Delete", message, parent=self):
            return

        try:
            # Delete from database
            db_path = Path(DATA_DIR) / "tasks.db"
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()

            # Delete steps (which will cascade to delete requests and responses if foreign keys are set up)
            cursor.execute("DELETE FROM steps WHERE task_id = ?", (task_id,))

            # Delete requests associated with this task
            cursor.execute("DELETE FROM requests WHERE task_id = ?", (task_id,))

            # Delete responses associated with this task
            cursor.execute("DELETE FROM responses WHERE task_id = ?", (task_id,))

            # Delete task (CASCADE will handle any remaining related records)
            cursor.execute("DELETE FROM tasks WHERE id = ?", (task_id,))

            conn.commit()
            conn.close()

            # Delete DOM files directory
            dom_dir = Path(DATA_DIR) / "doms" / f"task_{task_id}"
            if dom_dir.exists():
                try:
                    shutil.rmtree(dom_dir)
                    print(f"Deleted DOM directory: {dom_dir}")
                except Exception as e:
                    print(f"Error deleting DOM directory {dom_dir}: {e}")

            # Delete screenshots directory
            screenshots_dir = Path(DATA_DIR) / "screenshots" / f"task{task_id}"
            if screenshots_dir.exists():
                try:
                    shutil.rmtree(screenshots_dir)
                    print(f"Deleted screenshots directory: {screenshots_dir}")
                except Exception as e:
                    print(
                        f"Error deleting screenshots directory {screenshots_dir}: {e}"
                    )

            # Delete captures directory
            captures_dir = Path(DATA_DIR) / "captures" / f"task_{task_id}"
            if captures_dir.exists():
                try:
                    shutil.rmtree(captures_dir)
                    print(f"Deleted captures directory: {captures_dir}")
                except Exception as e:
                    print(f"Error deleting captures directory {captures_dir}: {e}")

            # Remove from tree view
            self.tree.delete(item)

            # Update info label
            current_count = len(self.tree.get_children())
            self.info_label.config(
                text=f"Task {task_id} deleted. Showing {current_count} task(s)"
            )

            messagebox.showinfo(
                "Success",
                f"Task {task_id} and all related data have been deleted.",
                parent=self,
            )

        except Exception as e:
            error_msg = f"Error deleting task: {str(e)}"
            self.info_label.config(text=error_msg, fg="red")
            messagebox.showerror("Delete Error", error_msg, parent=self)

    def edit_website(self):
        """Edit the website field for the selected task."""
        selected_items = self.tree.selection()
        if not selected_items:
            return

        # Get task details from the selected row
        item = selected_items[0]
        values = self.tree.item(item, "values")
        task_id = values[0]

        # Get full website from database (not truncated display version)
        db_path = Path(DATA_DIR) / "tasks.db"
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT website FROM tasks WHERE id = ?", (task_id,))
            result = cursor.fetchone()
            current_website = result[0] if result and result[0] else ""
            conn.close()
        except Exception as e:
            messagebox.showerror(
                "Database Error", f"Failed to load current website: {e}", parent=self
            )
            return

        # Show dialog to edit website
        dialog = TextAreaDialog(
            self,
            title="Edit Website URL",
            prompt=f"Edit website URL for task {task_id}:",
            initial_text=current_website,
        )
        new_website = dialog.show()

        if new_website is None:
            # User cancelled
            return

        # Update database
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE tasks SET website = ? WHERE id = ?",
                (new_website if new_website else None, task_id),
            )
            conn.commit()
            conn.close()

            # Refresh the display
            self.load_tasks()

            messagebox.showinfo(
                "Success",
                f"Website updated for task {task_id}.",
                parent=self,
            )

        except Exception as e:
            error_msg = f"Error updating website: {str(e)}"
            messagebox.showerror("Update Error", error_msg, parent=self)

    def edit_answer(self):
        """Edit the answer field for the selected task."""
        selected_items = self.tree.selection()
        if not selected_items:
            return

        # Get task details from the selected row
        item = selected_items[0]
        values = self.tree.item(item, "values")
        task_id = values[0]

        # Get full answer from database (not truncated display version)
        db_path = Path(DATA_DIR) / "tasks.db"
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT answer FROM tasks WHERE id = ?", (task_id,))
            result = cursor.fetchone()
            current_answer = result[0] if result and result[0] else ""
            conn.close()
        except Exception as e:
            messagebox.showerror(
                "Database Error", f"Failed to load current answer: {e}", parent=self
            )
            return

        # Show dialog to edit answer
        dialog = TextAreaDialog(
            self,
            title="Edit Answer",
            prompt=f"Edit answer for task {task_id}:",
            initial_text=current_answer,
        )
        new_answer = dialog.show()

        if new_answer is None:
            # User cancelled
            return

        # Update database
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE tasks SET answer = ? WHERE id = ?",
                (new_answer if new_answer else None, task_id),
            )
            conn.commit()
            conn.close()

            # Refresh the display
            self.load_tasks()

            messagebox.showinfo(
                "Success",
                f"Answer updated for task {task_id}.",
                parent=self,
            )

        except Exception as e:
            error_msg = f"Error updating answer: {str(e)}"
            messagebox.showerror("Update Error", error_msg, parent=self)


if getattr(sys, "frozen", False):  # Frozen executable (PyInstaller)
    BASE_PATH = Path(getattr(sys, "_MEIPASS"))  # type: ignore[attr-defined]
    # For macOS .app bundles, the executable is in .app/Contents/MacOS/
    # For Windows, it's in the root directory
    if sys.platform == "darwin":
        # Navigate from Contents/MacOS to the app bundle root
        PROJECT_ROOT = Path(sys.executable).resolve().parent
    else:
        PROJECT_ROOT = Path(sys.executable).resolve().parent
else:
    BASE_PATH = Path(__file__).resolve().parents[1]
    PROJECT_ROOT = BASE_PATH

PLAYWRIGHT_BROWSERS_DIR = PROJECT_ROOT / "playwright-browsers"
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(PLAYWRIGHT_BROWSERS_DIR))

# Debug logging for frozen apps
if getattr(sys, "frozen", False):
    print("Running as frozen app")
    print(f"BASE_PATH: {BASE_PATH}")
    print(f"PROJECT_ROOT: {PROJECT_ROOT}")
    print(f"PLAYWRIGHT_BROWSERS_DIR: {PLAYWRIGHT_BROWSERS_DIR}")
    print(f"Browsers exist: {PLAYWRIGHT_BROWSERS_DIR.exists()}")

if str(BASE_PATH) not in sys.path:
    sys.path.insert(0, str(BASE_PATH))

# pylint: disable=wrong-import-position
from desktop_app.task_worker import run_task_worker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(str(PROJECT_ROOT / "recorder_debug.log")),
    ],
)
logger = logging.getLogger(__name__)


SOURCE_CHOICES = [
    ("Custom / None", "none"),
    ("Bearcubs", "bearcubs"),
    ("BrowserComp", "browsercomp"),
    ("GAIA", "gaia"),
    ("WebVoyager", "webvoyager"),
    ("WebArena", "webarena"),
    ("Mind2Web", "mind2web"),
    ("Mind2Web 2", "mind2web2"),
    ("Real-World", "real"),
]

TASK_TYPE_CHOICES = {
    "action": "Action: add to cart, book a flight.",
    "information_retrieval": "Information Retrieval: find information, gather answers",
}


class TaskCollectorApp:
    """Tkinter application that mirrors the CLI task flow."""

    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("Task Collector")
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # Set better default window size
        self.root.geometry("800x750")
        self.root.minsize(700, 650)

        self.log_queue = queue.Queue()
        self.task_running = False
        self._active_task_type: Optional[str] = None
        self._worker_process: Optional[multiprocessing.Process] = None
        self._worker_conn = None
        self._listener_thread: Optional[threading.Thread] = None

        self._build_ui()
        self._process_log_queue()
        self._warn_if_credentials_missing()

    def run(self) -> None:
        """Start the Tkinter main loop."""
        self.root.mainloop()

    def _build_ui(self) -> None:
        # Main container with better padding
        container = tk.Frame(self.root, padx=24, pady=20)
        container.pack(fill=tk.BOTH, expand=True)

        title = tk.Label(
            container, text="Collect a New Task", font=("Helvetica", 18, "bold")
        )
        title.pack(anchor=tk.W, pady=(0, 4))

        subtitle = tk.Label(
            container,
            text=(
                "Fill in the task details, then click 'Launch Task'. The browser will open "
                "and recording will start automatically."
            ),
            wraplength=700,
            justify=tk.LEFT,
            font=("Helvetica", 11),
            fg="#555555",
        )
        subtitle.pack(anchor=tk.W, pady=(0, 20))

        # Google Cloud credentials input
        creds_frame = tk.Frame(container)
        creds_frame.pack(fill=tk.X, pady=(0, 12))
        tk.Label(
            creds_frame,
            text="Google Cloud Credentials:",
            font=("Helvetica", 12),
        ).pack(anchor=tk.W, pady=(0, 4))
        tk.Label(
            creds_frame,
            text="Paste your base64-encoded Google Cloud service account JSON here",
            font=("Helvetica", 9),
            fg="#777777",
        ).pack(anchor=tk.W, pady=(0, 4))
        self.credentials_text = tk.Text(
            creds_frame,
            height=6,
            width=80,
            font=("Courier", 9),
            wrap=tk.WORD,
            relief=tk.SOLID,
            borderwidth=1,
        )
        self.credentials_text.pack(fill=tk.X, pady=(0, 0))

        # Try to load saved credentials
        self._load_credentials()

        # Source dropdown
        source_frame = tk.Frame(container)
        source_frame.pack(fill=tk.X, pady=(0, 12))
        tk.Label(source_frame, text="Task Source:", font=("Helvetica", 12)).pack(
            anchor=tk.W, pady=(0, 4)
        )
        self.source_var = tk.StringVar(value=SOURCE_CHOICES[0][0])
        source_menu = tk.OptionMenu(
            source_frame,
            self.source_var,
            *[label for label, _ in SOURCE_CHOICES],
        )
        # We need to map displayed label to code; track in dictionary
        self._source_label_to_value = {label: value for label, value in SOURCE_CHOICES}
        source_menu.config(width=35, font=("Helvetica", 11))
        source_menu.pack(anchor=tk.W, pady=(0, 0))

        # Task type radio buttons
        type_frame = tk.Frame(container)
        type_frame.pack(fill=tk.X, pady=(0, 12))
        tk.Label(type_frame, text="Task Type:", font=("Helvetica", 12)).pack(
            anchor=tk.W, pady=(0, 6)
        )
        self.task_type_var = tk.StringVar(value="action")
        for value, label in TASK_TYPE_CHOICES.items():
            tk.Radiobutton(
                type_frame,
                text=label,
                variable=self.task_type_var,
                value=value,
                anchor=tk.W,
                justify=tk.LEFT,
                wraplength=700,
                font=("Helvetica", 11),
            ).pack(anchor=tk.W, pady=2)

        # Task description
        description_frame = tk.Frame(container)
        description_frame.pack(fill=tk.BOTH, expand=False, pady=(0, 8))

        tk.Label(
            description_frame, text="Task Description:", font=("Helvetica", 12)
        ).pack(anchor=tk.W, pady=(0, 6))

        self.description_text = tk.Text(
            description_frame,
            height=5,
            width=80,
            font=("Helvetica", 11),
            wrap=tk.WORD,
            relief=tk.SOLID,
            borderwidth=1,
        )
        self.description_text.pack(fill=tk.BOTH, expand=True, pady=(0, 0))

        # Website URL (optional)
        website_frame = tk.Frame(container)
        website_frame.pack(fill=tk.X, pady=(0, 12))
        tk.Label(
            website_frame, text="Website URL (Optional):", font=("Helvetica", 12)
        ).pack(anchor=tk.W, pady=(0, 4))
        tk.Label(
            website_frame,
            text="Enter the website URL if this task is specific to a particular site (e.g., https://www.google.com)",
            font=("Helvetica", 9),
            fg="#777777",
        ).pack(anchor=tk.W, pady=(0, 4))
        self.website_entry = tk.Entry(
            website_frame,
            font=("Helvetica", 11),
            relief=tk.SOLID,
            borderwidth=1,
        )
        self.website_entry.pack(fill=tk.X, pady=(0, 0))

        button_frame = tk.Frame(container)
        button_frame.pack(fill=tk.X, pady=(16, 0))

        self.open_data_button = tk.Button(
            button_frame,
            text="Open Data Folder",
            command=self.open_data_folder,
            font=("Helvetica", 11),
            padx=12,
            pady=6,
        )
        self.open_data_button.pack(side=tk.RIGHT)

        self.upload_data_button = tk.Button(
            button_frame,
            text="Upload Data",
            command=self.upload_data,
            font=("Helvetica", 11),
            padx=12,
            pady=6,
        )
        self.upload_data_button.pack(side=tk.RIGHT, padx=(0, 8))

        self.view_tasks_button = tk.Button(
            button_frame,
            text="View Tasks",
            command=self.view_tasks,
            font=("Helvetica", 11),
            padx=12,
            pady=6,
        )
        self.view_tasks_button.pack(side=tk.RIGHT, padx=(0, 8))

        self.launch_button = tk.Button(
            button_frame,
            text="Launch Task",
            command=self.launch_task,
            font=("Helvetica", 11, "bold"),
            bg="#4CAF50",
            fg="white",
            padx=16,
            pady=8,
            cursor="hand2",
        )
        self.launch_button.pack(side=tk.LEFT)

        self.complete_button = tk.Button(
            button_frame,
            text="Complete Task",
            state=tk.DISABLED,
            command=self.complete_task,
            font=("Helvetica", 11, "bold"),
            bg="#2196F3",
            fg="white",
            padx=16,
            pady=8,
            cursor="hand2",
        )
        self.complete_button.pack(side=tk.LEFT, padx=(12, 0))

        # Status bar with colored background
        status_container = tk.Frame(
            container, bg="#E8F5E9", relief=tk.SOLID, borderwidth=1
        )
        status_container.pack(fill=tk.X, pady=(16, 12))

        self.status_icon = tk.Label(
            status_container,
            text="●",
            font=("Helvetica", 16),
            fg="#4CAF50",
            bg="#E8F5E9",
        )
        self.status_icon.pack(side=tk.LEFT, padx=(12, 8), pady=8)

        self.status_label = tk.Label(
            status_container,
            text="Ready to collect tasks",
            fg="#2E7D32",
            bg="#E8F5E9",
            font=("Helvetica", 11),
        )
        self.status_label.pack(side=tk.LEFT, anchor=tk.W, pady=8)

        # Store status container for color changes
        self.status_container = status_container

        log_label = tk.Label(container, text="Activity Log:", font=("Helvetica", 12))
        log_label.pack(anchor=tk.W, pady=(0, 6))
        self.log_output = ScrolledText(
            container,
            height=12,
            width=85,
            state=tk.DISABLED,
            font=("Courier", 10),
            relief=tk.SOLID,
            borderwidth=1,
        )
        self.log_output.pack(fill=tk.BOTH, expand=True)

    def _process_log_queue(self) -> None:
        while not self.log_queue.empty():
            message = self.log_queue.get_nowait()
            self.log_output.config(state=tk.NORMAL)
            self.log_output.insert(tk.END, message + "\n")
            self.log_output.see(tk.END)
            self.log_output.config(state=tk.DISABLED)
        self.root.after(150, self._process_log_queue)

    def _load_credentials(self) -> None:
        """Load saved credentials from config file."""
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r") as f:
                    config = json.load(f)
                    creds = config.get("google_credentials_base64", "")
                    if creds:
                        # Only load if text widget is empty to avoid concatenating
                        current_content = self.credentials_text.get(
                            "1.0", tk.END
                        ).strip()
                        if not current_content:
                            self.credentials_text.delete(
                                "1.0", tk.END
                            )  # Clear any content
                            self.credentials_text.insert("1.0", creds)
            except Exception as exc:
                logger.warning(f"Failed to load credentials from config: {exc}")

    def _save_credentials(self) -> None:
        """Save credentials to config file."""
        creds = self.credentials_text.get("1.0", tk.END).strip()
        try:
            CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            config = {}
            if CONFIG_FILE.exists():
                with open(CONFIG_FILE, "r") as f:
                    config = json.load(f)
            config["google_credentials_base64"] = creds
            with open(CONFIG_FILE, "w") as f:
                json.dump(config, f, indent=2)
        except Exception as exc:
            logger.warning(f"Failed to save credentials: {exc}")

    def _warn_if_credentials_missing(self) -> None:
        if storage is None:
            warning = (
                "Google Cloud Storage library is not installed. Uploading data will "
                "be disabled until the dependency is available."
            )
            self._log(f"⚠️ {warning}")
            messagebox.showwarning("Upload Unavailable", warning, parent=self.root)
            return

        creds = self.credentials_text.get("1.0", tk.END).strip()
        if not creds:
            self._log(
                "⚠️ Upload requires Google Cloud credentials in the Settings field."
            )
        else:
            # Test credentials
            creds_ready, error_message = ensure_google_credentials(creds)
            if creds_ready:
                self._log("✅ Google Cloud credentials loaded successfully.")
            elif error_message:
                self._log(
                    "⚠️ There's an issue with your credentials - please check the format."
                )

    def _log(self, message: str) -> None:
        self.log_queue.put(message)
        logger.info(message)

    def _set_status(self, text: str, *, status_type: str = "ready") -> None:
        """Update status with appropriate colors.

        Args:
            text: Status message to display
            status_type: One of 'ready', 'launching', 'active', 'error'
        """
        status_colors = {
            "ready": {
                "bg": "#E8F5E9",
                "fg": "#2E7D32",
                "icon": "#4CAF50",
                "icon_text": "●",
            },
            "launching": {
                "bg": "#FFF3E0",
                "fg": "#E65100",
                "icon": "#FF9800",
                "icon_text": "◐",
            },
            "active": {
                "bg": "#E3F2FD",
                "fg": "#1565C0",
                "icon": "#2196F3",
                "icon_text": "◉",
            },
            "error": {
                "bg": "#FFEBEE",
                "fg": "#C62828",
                "icon": "#F44336",
                "icon_text": "✕",
            },
        }

        colors = status_colors.get(status_type, status_colors["ready"])

        self.status_container.config(bg=colors["bg"])
        self.status_label.config(text=text, fg=colors["fg"], bg=colors["bg"])
        self.status_icon.config(
            text=colors["icon_text"], fg=colors["icon"], bg=colors["bg"]
        )

    def open_data_folder(self) -> None:
        """Reveal the directory where recordings and logs are stored."""
        target_dir = Path(DATA_DIR)

        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            error_msg = f"Failed to prepare data directory: {exc}"
            self._log(f"❌ {error_msg}")
            messagebox.showerror("Open Data Folder", error_msg)
            return

        self._log(f"Opening data folder at {target_dir}")

        try:
            if sys.platform == "darwin":
                subprocess.Popen(
                    ["open", str(target_dir)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            elif sys.platform.startswith("win"):
                os.startfile(str(target_dir))  # type: ignore[attr-defined]
            else:
                opener = shutil.which("xdg-open")
                if opener:
                    subprocess.Popen(
                        [opener, str(target_dir)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                else:
                    raise RuntimeError("xdg-open not available on this system")
        except Exception as exc:  # pylint: disable=broad-except
            error_msg = f"Could not open folder: {exc}"
            self._log(f"❌ {error_msg}")
            messagebox.showerror("Open Data Folder", error_msg)

    def view_tasks(self) -> None:
        """Open a dialog to view collected tasks from the database."""
        try:
            dialog = TasksViewDialog(self.root)
            dialog.grab_set()  # Make dialog modal
            self.root.wait_window(dialog)
        except Exception as exc:
            error_msg = f"Failed to view tasks: {exc}"
            self._log(f"❌ {error_msg}")
            messagebox.showerror("View Tasks", error_msg)

    def _get_username(self) -> Optional[str]:
        """Get the saved username, or ask for it if not found."""
        # Try to load from config file
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r") as f:
                    config = json.load(f)
                    username = config.get("username")
                    if username:
                        return username
            except Exception as exc:
                logger.warning(f"Failed to load username from config: {exc}")

        # Ask user for username
        dialog = UsernameDialog(self.root)
        username = dialog.show()

        if username:
            # Save for next time
            self._save_username(username)
            return username

        return None

    def _save_username(self, username: str) -> None:
        """Save username to config file."""
        try:
            CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            config = {}
            if CONFIG_FILE.exists():
                with open(CONFIG_FILE, "r") as f:
                    config = json.load(f)
            config["username"] = username
            with open(CONFIG_FILE, "w") as f:
                json.dump(config, f, indent=2)
        except Exception as exc:
            logger.warning(f"Failed to save username: {exc}")

    def upload_data(self) -> None:
        """Zip the data directory and upload to GCP bucket."""
        if storage is None:
            error_msg = "Google Cloud Storage library not installed. Please install google-cloud-storage."
            self._log(f"❌ {error_msg}")
            messagebox.showerror("Upload Error", error_msg)
            return

        data_dir = Path(DATA_DIR)
        if not data_dir.exists():
            error_msg = f"Data directory does not exist: {data_dir}"
            self._log(f"❌ {error_msg}")
            messagebox.showerror("Upload Error", error_msg)
            return

        # Check if data directory has content
        if not any(data_dir.iterdir()):
            error_msg = "Data directory is empty. Nothing to upload."
            self._log(f"❌ {error_msg}")
            messagebox.showerror("Upload Error", error_msg)
            return

        # Get username (ask if first time)
        username = self._get_username()
        if not username:
            self._log("Upload cancelled - no username provided")
            return

        # Get and save credentials
        creds = self.credentials_text.get("1.0", tk.END).strip()
        if not creds:
            error_msg = "Please paste your Google Cloud credentials in the Settings field above."
            self._log(f"❌ {error_msg}")
            messagebox.showerror("Upload Error", error_msg, parent=self.root)
            self.credentials_text.focus_set()
            return

        # Save credentials for next time
        self._save_credentials()

        creds_ready, error_message = ensure_google_credentials(creds)
        if not creds_ready:
            error_text = (
                error_message
                or "Google Cloud credentials are not configured correctly."
            )
            self._log(f"❌ {error_text.splitlines()[0]}")
            messagebox.showerror("Upload Error", error_text, parent=self.root)
            return

        # Confirm upload
        if not messagebox.askyesno(
            "Confirm Upload",
            f"This will zip and upload the entire data directory ({data_dir}) to GCP bucket 'collection-reports'. Continue?",
        ):
            return

        self._log("Starting data upload process...")

        # Create progress dialog
        progress_dialog = UploadProgressDialog(self.root)
        temp_zip_path = None

        try:
            # Create timestamped zip filename with username prefix
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            zip_filename = f"{username}-web-envs-data-{timestamp}.zip"

            progress_dialog.update_progress(
                "Preparing zip file...", 5, "Counting files..."
            )
            self.root.update()

            # Count total files for progress tracking
            all_files = [f for f in data_dir.rglob("*") if f.is_file()]
            total_files = len(all_files)

            # Create temporary zip file
            with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as temp_zip:
                temp_zip_path = temp_zip.name

            progress_dialog.update_progress(
                "Creating zip archive...", 10, f"0 / {total_files} files"
            )
            self.root.update()

            # Create zip archive with progress tracking
            with zipfile.ZipFile(temp_zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
                for idx, file_path in enumerate(all_files, 1):
                    arcname = str(file_path.relative_to(data_dir.parent))
                    try:
                        zipf.write(file_path, arcname)
                    except (ValueError, OSError):
                        # Handle files with timestamps before 1980 or other issues
                        logger.error(f"Error adding file to zip: {file_path}")
                        zinfo = zipfile.ZipInfo(arcname)
                        zinfo.date_time = (1980, 1, 1, 0, 0, 0)
                        with open(file_path, "rb") as f:
                            zipf.writestr(zinfo, f.read())

                    # Update progress every 10 files or on last file
                    if idx % 10 == 0 or idx == total_files:
                        progress_pct = 10 + (idx / total_files) * 40  # 10-50%
                        progress_dialog.update_progress(
                            "Creating zip archive...",
                            progress_pct,
                            f"{idx} / {total_files} files",
                        )
                        self.root.update()

            # Get file size for upload progress
            zip_size = os.path.getsize(temp_zip_path)
            zip_size_mb = zip_size / (1024 * 1024)

            self._log(f"Created zip file: {zip_filename} ({zip_size_mb:.1f} MB)")
            progress_dialog.update_progress(
                "Uploading to Google Cloud...", 55, f"{zip_size_mb:.1f} MB"
            )
            self.root.update()

            # Initialize GCP client
            client = storage.Client()
            bucket = client.bucket("collection-reports")
            blob = bucket.blob(zip_filename)

            # Upload file (we'll show progress with animation)
            # Start upload in a thread to keep UI responsive
            upload_complete = threading.Event()
            upload_error = None

            def do_upload():
                nonlocal upload_error
                try:
                    blob.upload_from_filename(temp_zip_path)
                except Exception as e:
                    upload_error = e
                finally:
                    upload_complete.set()

            upload_thread = threading.Thread(target=do_upload, daemon=True)
            upload_thread.start()

            # Animate progress while uploading
            upload_progress = 55
            while not upload_complete.is_set():
                # Smoothly increment progress from 55% to 95%
                if upload_progress < 95:
                    upload_progress += 0.5
                progress_dialog.update_progress(
                    "Uploading to Google Cloud...",
                    upload_progress,
                    f"{zip_size_mb:.1f} MB",
                )
                self.root.update()
                self.root.after(100)  # Wait 100ms

            # Check if upload succeeded
            if upload_error:
                raise upload_error

            progress_dialog.update_progress("Finalizing upload...", 95, "")
            self.root.update()

            # Clean up temporary file
            os.unlink(temp_zip_path)

            progress_dialog.update_progress(
                "Upload complete!", 100, f"Uploaded {zip_size_mb:.1f} MB"
            )
            self.root.update()

            # Small delay to show 100%
            self.root.after(500, progress_dialog.destroy)

            self._set_status("Upload completed successfully!", status_type="ready")
            self._log(
                f"✅ Successfully uploaded {zip_filename} to collection-reports bucket"
            )
            messagebox.showinfo(
                "Upload Success", f"Data uploaded successfully as {zip_filename}"
            )

        except Exception as exc:
            progress_dialog.destroy()

            # Clean up temporary file if it exists
            if temp_zip_path and os.path.exists(temp_zip_path):
                try:
                    os.unlink(temp_zip_path)
                except Exception:
                    pass

            error_msg = f"Failed to upload data: {exc}"
            self._log(f"❌ {error_msg}")
            self._set_status("Upload failed – see log for details", status_type="error")
            messagebox.showerror("Upload Error", error_msg)

    def launch_task(self) -> None:
        if self.task_running:
            messagebox.showinfo(
                "Task in progress", "Finish the current task before starting a new one."
            )
            return

        displayed_source = self.source_var.get()
        source_value = self._source_label_to_value.get(displayed_source, "none")
        task_type = self.task_type_var.get()
        description = self.description_text.get("1.0", tk.END).strip()
        website = self.website_entry.get().strip() or None

        # Validate that description is provided and meaningful
        if not description:
            messagebox.showwarning(
                "Task Description Required",
                "Please enter a task description before launching.\n\nExample: 'Search for wireless headphones on Amazon and add the top-rated one to cart'",
                parent=self.root,
            )
            self.description_text.focus_set()
            return

        # Check for minimum length (at least 10 characters)
        if len(description) < 10:
            messagebox.showwarning(
                "Description Too Short",
                "Please provide a more detailed task description (at least 10 characters).\n\nBe specific about what you'll do in the browser.",
                parent=self.root,
            )
            self.description_text.focus_set()
            return

        self.task_running = True
        self._active_task_type = task_type
        self.launch_button.config(state=tk.DISABLED)
        self.complete_button.config(state=tk.DISABLED)  # enabled after browser launches
        self._set_status("Launching browser…", status_type="launching")
        self._log("Preparing to launch a new task…")

        ctx = multiprocessing.get_context("spawn")
        parent_conn, child_conn = ctx.Pipe()
        self._worker_conn = parent_conn
        self._worker_process = ctx.Process(
            target=run_task_worker,
            args=(child_conn, description, task_type, source_value, website),
            daemon=False,
        )
        self._worker_process.start()
        child_conn.close()

        self.root.after(0, self._poll_worker_messages)

    def _on_browser_ready(self) -> None:
        if not self.task_running:
            return
        self._set_status(
            "🎬 RECORDING IN PROGRESS – Complete the task in the browser window",
            status_type="active",
        )
        self.complete_button.config(state=tk.NORMAL)

    def complete_task(self) -> None:
        if not self.task_running:
            messagebox.showinfo("No active task", "Launch a task before completing it.")
            return

        answer: Optional[str] = ""
        if self._active_task_type == "information_retrieval":
            dialog = TextAreaDialog(
                self.root,
                title="Task Answer",
                prompt="Please enter the information you gathered (leave empty if none):",
            )
            answer = dialog.show()
            if answer is None:
                # User cancelled; don't finalize the task yet
                return

        self.complete_button.config(state=tk.DISABLED)
        self._log("Completing task – saving data and closing browser…")
        self._send_to_worker({"type": "complete", "answer": answer})

    def _poll_worker_messages(self) -> None:
        conn = self._worker_conn
        if conn is None:
            return
        try:
            while conn.poll():
                message = conn.recv()
                self._handle_worker_message(message)
                if message.get("type") == "finished":
                    return
        except EOFError:
            self._handle_worker_disconnect()
            return
        self.root.after(100, self._poll_worker_messages)

    def _handle_worker_message(self, message: dict) -> None:
        msg_type = message.get("type")
        if msg_type == "log":
            self._log(message.get("message", ""))
        elif msg_type == "task_started":
            task_id = message.get("task_id")
            self._log(f"Task stored with ID {task_id}.")
        elif msg_type == "browser_ready":
            self._on_browser_ready()
        elif msg_type == "finished":
            success = message.get("success", False)
            error = message.get("error")
            self._on_task_finished(success, error)

    def _handle_worker_disconnect(self) -> None:
        self._on_task_finished(False, "Worker process exited unexpectedly.")

    def _send_to_worker(self, payload: dict) -> None:
        if not self._worker_conn:
            return
        try:
            self._worker_conn.send(payload)
        except (BrokenPipeError, EOFError) as exc:
            self._log(f"❌ Failed to communicate with worker: {exc}")

    def _cleanup_worker(self) -> None:
        if self._worker_conn is not None:
            try:
                self._worker_conn.close()
            except Exception:  # pylint: disable=broad-except
                pass
            self._worker_conn = None
        if self._worker_process is not None:
            if self._worker_process.is_alive():
                self._worker_process.join(timeout=1)
                if self._worker_process.is_alive():
                    self._worker_process.terminate()
            self._worker_process = None

    def _on_task_finished(self, success: bool, error: Optional[str] = None) -> None:
        self.task_running = False
        self.launch_button.config(state=tk.NORMAL)
        self.complete_button.config(state=tk.DISABLED)
        self._active_task_type = None
        self._cleanup_worker()

        if success:
            self._set_status("Ready for the next task", status_type="ready")
            self._log("✅ Task completed and saved.")
        else:
            self._set_status(
                "An error occurred – see log for details", status_type="error"
            )
            if error:
                self._log(f"❌ {error}")

    def _post_ui(self, callback, *args) -> None:
        self.root.after(0, lambda: callback(*args))

    def _on_close(self) -> None:
        if self.task_running:
            if not messagebox.askyesno(
                "Task in progress",
                "A task is still running. Do you want to abort it and close the application?",
            ):
                return
            self._send_to_worker({"type": "cancel"})
            self._cleanup_worker()
        self.root.destroy()


if __name__ == "__main__":
    # Ensure we're running on the main thread
    import threading

    if threading.current_thread() is not threading.main_thread():
        print("ERROR: This application must be run on the main thread")
        sys.exit(1)

    multiprocessing.freeze_support()
    try:
        multiprocessing.set_start_method("spawn")
    except RuntimeError:
        # Already set elsewhere; continue with existing method
        pass

    # On macOS, set additional environment variable to prevent crashes
    if sys.platform == "darwin":
        os.environ["OBJC_DISABLE_INITIALIZE_FORK_SAFETY"] = "YES"

    try:
        print("Starting Task Collector App...")
        print(f"Python version: {sys.version}")
        print(f"Platform: {sys.platform}")
        print(f"Frozen: {getattr(sys, 'frozen', False)}")

        app = TaskCollectorApp()
        print("App initialized successfully")
        app.run()
    except Exception as e:
        logger.error(f"Application crashed: {e}", exc_info=True)
        # Show error dialog
        try:
            import tkinter as tk
            from tkinter import messagebox

            root = tk.Tk()
            root.withdraw()
            messagebox.showerror(
                "Application Error",
                f"Failed to start Task Collector:\n\n{str(e)}\n\nCheck the log file for details.",
            )
        except Exception:  # pylint: disable=broad-except
            print(f"ERROR: {e}")
        sys.exit(1)
