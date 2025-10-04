import sqlite3
import pandas as pd
import streamlit as st
from pathlib import Path


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


def main():
    st.set_page_config(page_title="Task Viewer", page_icon="📋", layout="wide")

    st.title("📋 Task Viewer")
    project_root = Path(__file__).parent.parent.parent
    db_path = project_root / "data" / "tasks.db"

    # Check if database exists
    if not db_path.exists():
        st.error(f"Database not found at: {db_path}")
        st.info("Make sure tasks.db exists in the data/ directory")
        return

    # Load tasks
    try:
        df = load_tasks(db_path)

        search_query = st.text_input("Search in description", "")

        filtered_df = df.copy()
        if search_query:
            filtered_df = filtered_df[
                filtered_df["description"].str.contains(
                    search_query, case=False, na=False
                )
            ]

        # Display tasks
        st.subheader(f"Tasks ({len(filtered_df)} results)")

        # Format the dataframe for display
        display_df = filtered_df.copy()
        display_df["answer"] = display_df["answer"].fillna("")
        display_df["website"] = display_df["website"].fillna("")

        # Use st.data_editor to allow editing
        edited_df = st.data_editor(
            display_df,
            width="stretch",
            hide_index=True,
            disabled=["id"],  # ID is not editable
            column_config={
                "id": st.column_config.NumberColumn("ID", width=10),
                "description": st.column_config.TextColumn(
                    "Description", width="large"
                ),
                "answer": st.column_config.TextColumn("Answer", width="medium"),
                "website": st.column_config.TextColumn("Website", width="medium"),
            },
            key="tasks_editor",
        )

        # Check if any changes were made and provide save button
        if not edited_df.equals(display_df):
            if st.button("💾 Save Changes", type="primary"):
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
                            answer = edit_row["answer"] if edit_row["answer"] else None
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

    except Exception as e:
        st.error(f"Error loading database: {str(e)}")
        st.exception(e)


if __name__ == "__main__":
    main()
