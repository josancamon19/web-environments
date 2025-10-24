import sqlite3
import pandas as pd
import streamlit as st
from pathlib import Path
import shutil


def load_tasks(db_path: Path):
    """Load tasks from database."""
    conn = sqlite3.connect(db_path)
    query = """
    SELECT id, description, answer, website
    FROM tasks
    ORDER BY id
    """
    df = pd.read_sql_query(query, conn)
    conn.close()
    return df


def update_tasks_batch(db_path: Path, updates: list):
    """Update multiple tasks in the database in a batch."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    for task_id, description, answer, website in updates:
        cursor.execute(
            """
            UPDATE tasks
            SET description = ?, answer = ?, website = ?
            WHERE id = ?
            """,
            (description, answer, website, task_id),
        )

    conn.commit()
    conn.close()


def delete_task(db_path: Path, task_id: int, data_dir: Path) -> tuple[bool, str]:
    """Delete a task and all its related data.

    Returns:
        tuple: (success: bool, message: str)
    """
    try:
        # Delete from database
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Delete steps (which will cascade to delete requests and responses if foreign keys are set up)
        cursor.execute("DELETE FROM steps WHERE task_id = ?", (task_id,))

        # Delete requests associated with this task
        cursor.execute("DELETE FROM requests WHERE task_id = ?", (task_id,))

        # Delete responses associated with this task
        cursor.execute("DELETE FROM responses WHERE task_id = ?", (task_id,))

        # Delete task
        cursor.execute("DELETE FROM tasks WHERE id = ?", (task_id,))

        conn.commit()
        conn.close()

        # Delete file system directories
        deleted_dirs = []

        # Delete DOM files directory
        dom_dir = data_dir / "doms" / f"task_{task_id}"
        if dom_dir.exists():
            shutil.rmtree(dom_dir)
            deleted_dirs.append(f"doms/task_{task_id}")

        # Delete screenshots directory
        screenshots_dir = data_dir / "screenshots" / f"task{task_id}"
        if screenshots_dir.exists():
            shutil.rmtree(screenshots_dir)
            deleted_dirs.append(f"screenshots/task{task_id}")

        # Delete captures directory
        captures_dir = data_dir / "captures" / f"task_{task_id}"
        if captures_dir.exists():
            shutil.rmtree(captures_dir)
            deleted_dirs.append(f"captures/task_{task_id}")

        # Delete video directory (search for task{id}_* pattern)
        videos_dir = data_dir / "videos"
        if videos_dir.exists():
            for video_folder in videos_dir.iterdir():
                if video_folder.is_dir() and video_folder.name.startswith(
                    f"task{task_id}_"
                ):
                    shutil.rmtree(video_folder)
                    deleted_dirs.append(f"videos/{video_folder.name}")

        dirs_msg = f" (deleted: {', '.join(deleted_dirs)})" if deleted_dirs else ""
        return True, f"Task {task_id} deleted successfully{dirs_msg}"

    except Exception as e:
        return False, f"Error deleting task {task_id}: {str(e)}"


def delete_tasks_batch(
    db_path: Path, task_ids: list[int], data_dir: Path
) -> tuple[list[int], list[tuple[int, str]]]:
    """Delete multiple tasks in batch.

    Returns:
        tuple: (successful_ids: list[int], failed: list[tuple[int, str]])
    """
    successful = []
    failed = []

    for task_id in task_ids:
        success, message = delete_task(db_path, task_id, data_dir)
        if success:
            successful.append(task_id)
        else:
            failed.append((task_id, message))

    return successful, failed


def main():
    st.set_page_config(page_title="Task Viewer", page_icon="📋", layout="wide")

    st.title("📋 Task Viewer & Editor")
    project_root = Path(__file__).parent.parent.parent.parent
    db_path = project_root / "data" / "tasks.db"
    data_dir = project_root / "data"

    # Check if database exists
    if not db_path.exists():
        st.error(f"Database not found at: {db_path}")
        st.info("Make sure tasks.db exists in the data/ directory")
        return

    # Initialize session state for selected tasks
    if "selected_tasks" not in st.session_state:
        st.session_state.selected_tasks = []

    # Load tasks
    try:
        df = load_tasks(db_path)

        # Sidebar for actions
        with st.sidebar:
            st.header("🛠️ Actions")

            # Search functionality
            search_query = st.text_input("🔍 Search in description", "")

            st.divider()

            # Delete section
            st.subheader("🗑️ Delete Tasks")
            st.write("Select tasks below and click delete to remove them.")

            if st.session_state.selected_tasks:
                st.warning(f"Selected: {len(st.session_state.selected_tasks)} task(s)")

                if st.button(
                    "🗑️ Delete Selected Tasks",
                    type="secondary",
                    use_container_width=True,
                ):
                    # Show confirmation dialog
                    st.session_state.show_delete_confirmation = True
            else:
                st.info("Select tasks using checkboxes in the table")

        filtered_df = df.copy()
        if search_query:
            filtered_df = filtered_df[
                filtered_df["description"].str.contains(
                    search_query, case=False, na=False
                )
            ]

        # Display tasks
        st.subheader(f"Tasks ({len(filtered_df)} results)")

        # Add selection column
        display_df = filtered_df.copy()
        display_df.insert(0, "Select", False)
        display_df["answer"] = display_df["answer"].fillna("")
        display_df["website"] = display_df["website"].fillna("")

        # Use st.data_editor to allow editing and selection
        edited_df = st.data_editor(
            display_df,
            width="stretch",
            hide_index=True,
            disabled=["id"],  # ID is not editable
            column_config={
                "Select": st.column_config.CheckboxColumn(
                    "Select",
                    help="Select tasks to delete",
                    default=False,
                    width="small",
                ),
                "id": st.column_config.NumberColumn("ID", width="small"),
                "description": st.column_config.TextColumn(
                    "Description", width="large"
                ),
                "answer": st.column_config.TextColumn("Answer", width="medium"),
                "website": st.column_config.TextColumn("Website", width="medium"),
            },
            key="tasks_editor",
        )

        # Update selected tasks in session state
        st.session_state.selected_tasks = edited_df[edited_df["Select"]]["id"].tolist()

        # Action buttons in columns
        col1, col2, col3 = st.columns([1, 1, 4])

        with col1:
            # Check if any changes were made (excluding the Select column)
            display_no_select = display_df.drop(columns=["Select"])
            edited_no_select = edited_df.drop(columns=["Select"])

            if not edited_no_select.equals(display_no_select):
                if st.button(
                    "💾 Save Changes", type="primary", use_container_width=True
                ):
                    try:
                        # Find all modified rows and collect updates
                        updates = []

                        for idx in edited_df.index:
                            # Compare each field individually for more reliable detection
                            orig_row = display_df.loc[idx]
                            edit_row = edited_df.loc[idx]

                            if (
                                orig_row["description"] != edit_row["description"]
                                or orig_row["answer"] != edit_row["answer"]
                                or orig_row["website"] != edit_row["website"]
                            ):
                                task_id = int(edit_row["id"])
                                description = edit_row["description"]
                                answer = (
                                    edit_row["answer"] if edit_row["answer"] else None
                                )
                                website = (
                                    edit_row["website"] if edit_row["website"] else None
                                )

                                updates.append((task_id, description, answer, website))

                        # Batch update all changes
                        if updates:
                            update_tasks_batch(db_path, updates)
                            st.success(f"✅ Successfully saved {len(updates)} task(s)!")
                            st.rerun()
                        else:
                            st.info("No changes detected")

                    except Exception as e:
                        st.error(f"Error saving changes: {str(e)}")
                        st.exception(e)

        with col2:
            if st.session_state.selected_tasks:
                if st.button(
                    "🗑️ Delete Selected", type="secondary", use_container_width=True
                ):
                    st.session_state.show_delete_confirmation = True
                    st.rerun()

        # Delete confirmation dialog
        if st.session_state.get("show_delete_confirmation", False):
            with st.container():
                st.divider()
                st.warning("⚠️ **Confirm Deletion**")
                st.write(
                    f"You are about to delete **{len(st.session_state.selected_tasks)}** task(s):"
                )

                # Show which tasks will be deleted
                tasks_to_delete = df[df["id"].isin(st.session_state.selected_tasks)]
                for _, task in tasks_to_delete.iterrows():
                    st.write(f"- **Task {task['id']}**: {task['description'][:100]}...")

                st.write("\n**This will permanently delete:**")
                st.write("• Task record from database")
                st.write("• All steps, requests, and responses")
                st.write("• DOM files")
                st.write("• Screenshots")
                st.write("• Capture files")
                st.write("• Video files")

                col_confirm1, col_confirm2, col_confirm3 = st.columns([1, 1, 3])

                with col_confirm1:
                    if st.button(
                        "✅ Yes, Delete", type="primary", use_container_width=True
                    ):
                        # Perform deletion
                        with st.spinner("Deleting tasks..."):
                            successful, failed = delete_tasks_batch(
                                db_path, st.session_state.selected_tasks, data_dir
                            )

                        if successful:
                            st.success(
                                f"✅ Successfully deleted {len(successful)} task(s)!"
                            )
                            if failed:
                                st.error(f"❌ Failed to delete {len(failed)} task(s):")
                                for task_id, error in failed:
                                    st.error(f"Task {task_id}: {error}")
                        else:
                            st.error("❌ Failed to delete tasks:")
                            for task_id, error in failed:
                                st.error(f"Task {task_id}: {error}")

                        # Clear selection and confirmation state
                        st.session_state.selected_tasks = []
                        st.session_state.show_delete_confirmation = False
                        st.rerun()

                with col_confirm2:
                    if st.button("❌ Cancel", use_container_width=True):
                        st.session_state.show_delete_confirmation = False
                        st.rerun()

    except Exception as e:
        st.error(f"Error loading database: {str(e)}")
        st.exception(e)


if __name__ == "__main__":
    main()
