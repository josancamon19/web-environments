"""Tkinter desktop app that orchestrates task collection sessions."""

import logging
import multiprocessing
import os
import queue
import sys
import threading
from pathlib import Path

# macOS-specific fix for tkinter bus errors
if sys.platform == "darwin":
    # Disable macOS App Nap which can cause issues with tkinter
    os.environ["PYTHON_COREAUDIO_ALLOW_INSECURE_REQUESTS"] = "1"
    # Ensure we're using the main display
    if "DISPLAY" not in os.environ:
        os.environ["DISPLAY"] = ":0.0"

import tkinter as tk
from tkinter import messagebox, simpledialog
from tkinter.scrolledtext import ScrolledText
from typing import Optional


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
        
        cancel_button = tk.Button(button_frame, text="Cancel", command=self.cancel, width=10)
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

        title = tk.Label(container, text="Collect a New Task", font=("Helvetica", 16, "bold"))
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

        self.launch_button = tk.Button(button_frame, text="Launch Task", command=self.launch_task)
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
        self.log_output = ScrolledText(container, height=12, width=70, state=tk.DISABLED)
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

    def launch_task(self) -> None:
        if self.task_running:
            messagebox.showinfo("Task in progress", "Finish the current task before starting a new one.")
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
        self._set_status("Browser ready – complete the task in the new window.", is_error=False)
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
                prompt="Please enter the information you gathered (leave empty if none):"
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
