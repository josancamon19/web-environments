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
from google.cloud import storage
import base64
from dotenv import load_dotenv
import json

load_dotenv()

creds_base64 = os.getenv("GOOGLE_APPLICATION_CREDENTIALS_BASE64")
decoded_creds = base64.b64decode(creds_base64)

with open("google-credentials.json", "wb") as f:
    f.write(decoded_creds)

os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "google-credentials.json"

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
            messagebox.showwarning("Invalid Username", "Please enter a username.", parent=self)
            return
        # Sanitize username for filename
        self.result = "".join(c for c in username if c.isalnum() or c in "-_")
        if not self.result:
            messagebox.showwarning("Invalid Username", "Username must contain alphanumeric characters.", parent=self)
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

        self.protocol("WM_DELETE_WINDOW", lambda: None)  # Disable close button during upload

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

        self.detail_label = tk.Label(main_frame, text="", fg="gray", font=("Helvetica", 9))
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

        # Create context menu for delete option
        self.context_menu = tk.Menu(self, tearoff=0)
        self.context_menu.add_command(
            label="Delete Task", command=self.delete_selected_task
        )

        # Bind right-click to show context menu
        self.tree.bind("<Button-2>", self.show_context_menu)  # Mac right-click
        self.tree.bind(
            "<Button-3>", self.show_context_menu
        )  # Windows/Linux right-click

        # Define column headings and widths
        self.tree.heading("ID", text="ID")
        self.tree.heading("Description", text="Description")
        self.tree.heading("Type", text="Type")
        self.tree.heading("Answer", text="Answer")
        self.tree.heading("Created At", text="Created At")
        self.tree.heading("Duration", text="Duration (s)")
        self.tree.heading("Video Path", text="Video Path")

        # Set column widths
        self.tree.column("ID", width=50)
        self.tree.column("Description", width=300)
        self.tree.column("Type", width=120)
        self.tree.column("Answer", width=200)
        self.tree.column("Created At", width=150)
        self.tree.column("Duration", width=80)
        self.tree.column("Video Path", width=250)

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
                SELECT id, description, task_type, answer, created_at, duration_seconds, video_path
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
        """Delete the selected task after confirmation."""
        selected_items = self.tree.selection()
        if not selected_items:
            return

        # Get task details from the selected row
        item = selected_items[0]
        values = self.tree.item(item, "values")
        task_id = values[0]
        description = values[1]
        video_path = values[6]  # Video filename from the table

        # Show confirmation dialog
        message = f"Are you sure you want to delete task {task_id}?\n\nDescription: {description}\n\nThis will also delete the associated video and DOM files."
        if not messagebox.askyesno("Confirm Delete", message, parent=self):
            return

        try:
            # Delete from database
            db_path = Path(DATA_DIR) / "tasks.db"
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()

            # Delete task (CASCADE will handle related records)
            cursor.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            conn.commit()
            conn.close()

            # Delete video file if it exists
            if video_path:
                video_full_path = Path(DATA_DIR) / "videos" / video_path
                if video_full_path.exists():
                    try:
                        video_full_path.unlink()
                        print(f"Deleted video: {video_full_path}")
                    except Exception as e:
                        print(f"Error deleting video {video_full_path}: {e}")

            # Delete DOM files directory
            dom_dir = Path(DATA_DIR) / "doms" / f"task_{task_id}"
            if dom_dir.exists():
                try:
                    shutil.rmtree(dom_dir)
                    print(f"Deleted DOM directory: {dom_dir}")
                except Exception as e:
                    print(f"Error deleting DOM directory {dom_dir}: {e}")

            # Remove from tree view
            self.tree.delete(item)

            # Update info label
            current_count = len(self.tree.get_children())
            self.info_label.config(
                text=f"Task {task_id} deleted. Showing {current_count} task(s)"
            )

            messagebox.showinfo(
                "Success", f"Task {task_id} has been deleted.", parent=self
            )

        except Exception as e:
            error_msg = f"Error deleting task: {str(e)}"
            self.info_label.config(text=error_msg, fg="red")
            messagebox.showerror("Delete Error", error_msg, parent=self)


if getattr(sys, "frozen", False):  # Frozen executable (PyInstaller)
    BASE_PATH = Path(getattr(sys, "_MEIPASS"))  # type: ignore[attr-defined]
    PROJECT_ROOT = Path(sys.executable).resolve().parent
else:
    BASE_PATH = Path(__file__).resolve().parents[1]
    PROJECT_ROOT = BASE_PATH

PLAYWRIGHT_BROWSERS_DIR = PROJECT_ROOT / "playwright-browsers"
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(PLAYWRIGHT_BROWSERS_DIR))

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
    "action": "Action: interact with pages (click, type, navigate)",
    "information_retrieval": "Information Retrieval: gather answers",
}


class TaskCollectorApp:
    """Tkinter application that mirrors the CLI task flow."""

    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("Task Collector")
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self.log_queue = queue.Queue()
        self.task_running = False
        self._active_task_type: Optional[str] = None
        self._worker_process: Optional[multiprocessing.Process] = None
        self._worker_conn = None
        self._listener_thread: Optional[threading.Thread] = None

        self._build_ui()
        self._process_log_queue()

    def run(self) -> None:
        """Start the Tkinter main loop."""
        self.root.mainloop()

    def _build_ui(self) -> None:
        container = tk.Frame(self.root, padx=16, pady=16)
        container.pack(fill=tk.BOTH, expand=True)

        title = tk.Label(
            container, text="Collect a New Task", font=("Helvetica", 16, "bold")
        )
        title.pack(anchor=tk.W)

        subtitle = tk.Label(
            container,
            text=(
                "Fill in the task details, then click 'Launch Task'. The browser will open "
                "and recording will start automatically."
            ),
            wraplength=480,
            justify=tk.LEFT,
        )
        subtitle.pack(anchor=tk.W, pady=(4, 12))

        # Source dropdown
        source_frame = tk.Frame(container)
        source_frame.pack(fill=tk.X, pady=4)
        tk.Label(source_frame, text="Task Source:").pack(anchor=tk.W)
        self.source_var = tk.StringVar(value=SOURCE_CHOICES[0][0])
        source_menu = tk.OptionMenu(
            source_frame,
            self.source_var,
            *[label for label, _ in SOURCE_CHOICES],
        )
        # We need to map displayed label to code; track in dictionary
        self._source_label_to_value = {label: value for label, value in SOURCE_CHOICES}
        source_menu.config(width=30)
        source_menu.pack(anchor=tk.W, pady=(2, 0))

        # Task type radio buttons
        type_frame = tk.Frame(container)
        type_frame.pack(fill=tk.X, pady=4)
        tk.Label(type_frame, text="Task Type:").pack(anchor=tk.W)
        self.task_type_var = tk.StringVar(value="action")
        for value, label in TASK_TYPE_CHOICES.items():
            tk.Radiobutton(
                type_frame,
                text=label,
                variable=self.task_type_var,
                value=value,
                anchor=tk.W,
                justify=tk.LEFT,
                wraplength=480,
            ).pack(anchor=tk.W)

        # Task description
        description_frame = tk.Frame(container)
        description_frame.pack(fill=tk.BOTH, expand=False, pady=6)
        tk.Label(description_frame, text="Task Description:").pack(anchor=tk.W)
        self.description_text = tk.Text(description_frame, height=4, width=60)
        self.description_text.pack(fill=tk.BOTH, expand=True, pady=(2, 0))

        button_frame = tk.Frame(container)
        button_frame.pack(fill=tk.X, pady=(12, 6))

        self.open_data_button = tk.Button(
            button_frame,
            text="Open Data Folder",
            command=self.open_data_folder,
        )
        self.open_data_button.pack(side=tk.RIGHT)

        self.upload_data_button = tk.Button(
            button_frame,
            text="Upload Data",
            command=self.upload_data,
        )
        self.upload_data_button.pack(side=tk.RIGHT, padx=(0, 8))

        self.view_tasks_button = tk.Button(
            button_frame,
            text="View Tasks",
            command=self.view_tasks,
        )
        self.view_tasks_button.pack(side=tk.RIGHT, padx=(0, 8))

        self.launch_button = tk.Button(
            button_frame, text="Launch Task", command=self.launch_task
        )
        self.launch_button.pack(side=tk.LEFT)

        self.complete_button = tk.Button(
            button_frame,
            text="Complete Task",
            state=tk.DISABLED,
            command=self.complete_task,
        )
        self.complete_button.pack(side=tk.LEFT, padx=(8, 0))

        self.status_label = tk.Label(container, text="Ready", fg="green")
        self.status_label.pack(anchor=tk.W, pady=(4, 8))

        log_label = tk.Label(container, text="Activity Log:")
        log_label.pack(anchor=tk.W)
        self.log_output = ScrolledText(
            container, height=12, width=70, state=tk.DISABLED
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

    def _log(self, message: str) -> None:
        self.log_queue.put(message)
        logger.info(message)

    def _set_status(self, text: str, *, is_error: bool = False) -> None:
        color = "red" if is_error else "green"
        self.status_label.config(text=text, fg=color)

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

            progress_dialog.update_progress("Preparing zip file...", 5, "Counting files...")
            self.root.update()

            # Count total files for progress tracking
            all_files = [f for f in data_dir.rglob("*") if f.is_file()]
            total_files = len(all_files)
            
            # Create temporary zip file
            with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as temp_zip:
                temp_zip_path = temp_zip.name

            progress_dialog.update_progress("Creating zip archive...", 10, f"0 / {total_files} files")
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
                            f"{idx} / {total_files} files"
                        )
                        self.root.update()

            # Get file size for upload progress
            zip_size = os.path.getsize(temp_zip_path)
            zip_size_mb = zip_size / (1024 * 1024)
            
            self._log(f"Created zip file: {zip_filename} ({zip_size_mb:.1f} MB)")
            progress_dialog.update_progress("Uploading to Google Cloud...", 55, f"{zip_size_mb:.1f} MB")
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
                    f"{zip_size_mb:.1f} MB"
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

            progress_dialog.update_progress("Upload complete!", 100, f"Uploaded {zip_size_mb:.1f} MB")
            self.root.update()
            
            # Small delay to show 100%
            self.root.after(500, progress_dialog.destroy)
            
            self._set_status("Upload completed successfully!", is_error=False)
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
            self._set_status("Upload failed – see log for details.", is_error=True)
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

        if not description:
            description = "General browsing session"
            messagebox.showinfo(
                "Empty description",
                "No description provided. Using 'General browsing session'.",
            )

        self.task_running = True
        self._active_task_type = task_type
        self.launch_button.config(state=tk.DISABLED)
        self.complete_button.config(state=tk.DISABLED)  # enabled after browser launches
        self._set_status("Launching browser…", is_error=False)
        self._log("Preparing to launch a new task…")

        ctx = multiprocessing.get_context("spawn")
        parent_conn, child_conn = ctx.Pipe()
        self._worker_conn = parent_conn
        self._worker_process = ctx.Process(
            target=run_task_worker,
            args=(child_conn, description, task_type, source_value),
            daemon=False,
        )
        self._worker_process.start()
        child_conn.close()

        self.root.after(0, self._poll_worker_messages)

    def _on_browser_ready(self) -> None:
        if not self.task_running:
            return
        self._set_status(
            "Browser ready – complete the task in the new window.", is_error=False
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
            self._set_status("Ready for the next task.")
            self._log("✅ Task completed and saved.")
        else:
            self._set_status("An error occurred – see log for details.", is_error=True)
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
        TaskCollectorApp().run()
    except Exception as e:
        logger.error(f"Application crashed: {e}", exc_info=True)
        sys.exit(1)
