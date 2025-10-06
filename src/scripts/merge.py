#!/usr/bin/env python3
"""
Merge script to combine data_sofia into data folder.
Remaps task IDs sequentially and updates all foreign keys and file paths.
"""

import sqlite3
import shutil
from pathlib import Path
import sys
from typing import Dict, List
import re


class DataMerger:
    def __init__(self, source_dir: Path, target_dir: Path):
        self.source_dir = source_dir
        self.target_dir = target_dir
        self.source_db = source_dir / "tasks.db"
        self.target_db = target_dir / "tasks.db"

        # Mappings to track ID changes
        self.task_id_map: Dict[int, int] = {}
        self.step_id_map: Dict[int, int] = {}
        self.request_id_map: Dict[int, int] = {}

    def get_max_task_id(self) -> int:
        """Get the maximum task ID from target database."""
        conn = sqlite3.connect(self.target_db)
        cursor = conn.cursor()
        cursor.execute("SELECT MAX(id) FROM tasks")
        max_id = cursor.fetchone()[0] or 0
        conn.close()
        return max_id

    def get_source_task_ids(self) -> List[int]:
        """Get all task IDs from source database."""
        conn = sqlite3.connect(self.source_db)
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM tasks ORDER BY id")
        task_ids = [row[0] for row in cursor.fetchall()]
        conn.close()
        return task_ids

    def build_id_mappings(self, source_task_ids: List[int], start_id: int):
        """Build mapping from old IDs to new IDs."""
        for idx, old_id in enumerate(source_task_ids):
            new_id = start_id + idx + 1
            self.task_id_map[old_id] = new_id

        print(f"\n📋 Task ID Mapping:")
        for old_id, new_id in self.task_id_map.items():
            print(f"   task_{old_id} → task_{new_id}")

    def update_video_path(
        self, old_path: str, old_task_id: int, new_task_id: int
    ) -> str:
        """Convert Windows path to relative path with new task ID."""
        if not old_path:
            return None

        # Extract the video folder name (e.g., task1_2025-10-02T16-18-35.856Z.mp4)
        match = re.search(r"task\d+_[\d\-T:.Z]+\.mp4", old_path)
        if match:
            old_video_name = match.group(0)
            new_video_name = old_video_name.replace(
                f"task{old_task_id}_", f"task{new_task_id}_"
            )
            return f"videos/{new_video_name}"

        return None

    def copy_tasks(
        self, conn_source: sqlite3.Connection, conn_target: sqlite3.Connection
    ) -> int:
        """Copy tasks from source to target with new IDs."""
        cursor_source = conn_source.cursor()
        cursor_target = conn_target.cursor()

        print("\n📝 Copying tasks...")

        for old_id, new_id in self.task_id_map.items():
            cursor_source.execute(
                """
                SELECT description, task_type, source, answer, video_path, 
                       created_at, ended_at, duration_seconds, environment_fingerprint
                FROM tasks WHERE id = ?
            """,
                (old_id,),
            )

            row = cursor_source.fetchone()
            if row:
                (
                    description,
                    task_type,
                    source,
                    answer,
                    video_path,
                    created_at,
                    ended_at,
                    duration_seconds,
                    env_fingerprint,
                ) = row

                # Update video path
                new_video_path = self.update_video_path(video_path, old_id, new_id)

                cursor_target.execute(
                    """
                    INSERT INTO tasks (id, description, task_type, source, answer, video_path,
                                     created_at, ended_at, duration_seconds, environment_fingerprint, website)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        new_id,
                        description,
                        task_type,
                        source,
                        answer,
                        new_video_path,
                        created_at,
                        ended_at,
                        duration_seconds,
                        env_fingerprint,
                        None,
                    ),
                )

                print(f"   ✓ Copied task {old_id} → {new_id}: {description[:60]}...")

        return len(self.task_id_map)

    def copy_steps(
        self, conn_source: sqlite3.Connection, conn_target: sqlite3.Connection
    ) -> int:
        """Copy steps from source to target with updated task_id references."""
        cursor_source = conn_source.cursor()
        cursor_target = conn_target.cursor()

        print("\n📸 Copying steps...")
        total_steps = 0

        for old_task_id, new_task_id in self.task_id_map.items():
            cursor_source.execute(
                """
                SELECT id, timestamp, event_type, event_data, dom_snapshot, 
                       dom_snapshot_metadata, screenshot_path
                FROM steps WHERE task_id = ?
                ORDER BY id
            """,
                (old_task_id,),
            )

            steps = cursor_source.fetchall()
            for (
                old_step_id,
                timestamp,
                event_type,
                event_data,
                dom_snapshot,
                dom_metadata,
                screenshot_path,
            ) in steps:
                # Update screenshot path if it exists
                new_screenshot_path = screenshot_path
                if screenshot_path:
                    new_screenshot_path = screenshot_path.replace(
                        f"task{old_task_id}/", f"task{new_task_id}/"
                    )

                cursor_target.execute(
                    """
                    INSERT INTO steps (task_id, timestamp, event_type, event_data, 
                                     dom_snapshot, dom_snapshot_metadata, screenshot_path)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        new_task_id,
                        timestamp,
                        event_type,
                        event_data,
                        dom_snapshot,
                        dom_metadata,
                        new_screenshot_path,
                    ),
                )

                new_step_id = cursor_target.lastrowid
                self.step_id_map[old_step_id] = new_step_id
                total_steps += 1

        print(f"   ✓ Copied {total_steps} steps")
        return total_steps

    def copy_requests(
        self, conn_source: sqlite3.Connection, conn_target: sqlite3.Connection
    ) -> int:
        """Copy requests from source to target with updated foreign keys."""
        cursor_source = conn_source.cursor()
        cursor_target = conn_target.cursor()

        print("\n🌐 Copying requests...")
        total_requests = 0

        for old_task_id, new_task_id in self.task_id_map.items():
            cursor_source.execute(
                """
                SELECT id, step_id, request_uid, url, method, headers, 
                       post_data, cookies, timestamp
                FROM requests WHERE task_id = ?
                ORDER BY id
            """,
                (old_task_id,),
            )

            requests = cursor_source.fetchall()
            for (
                old_req_id,
                old_step_id,
                request_uid,
                url,
                method,
                headers,
                post_data,
                cookies,
                timestamp,
            ) in requests:
                new_step_id = self.step_id_map.get(old_step_id)

                cursor_target.execute(
                    """
                    INSERT INTO requests (task_id, step_id, request_uid, url, method, 
                                        headers, post_data, cookies, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        new_task_id,
                        new_step_id,
                        request_uid,
                        url,
                        method,
                        headers,
                        post_data,
                        cookies,
                        timestamp,
                    ),
                )

                new_req_id = cursor_target.lastrowid
                self.request_id_map[old_req_id] = new_req_id
                total_requests += 1

        print(f"   ✓ Copied {total_requests} requests")
        return total_requests

    def copy_responses(
        self, conn_source: sqlite3.Connection, conn_target: sqlite3.Connection
    ) -> int:
        """Copy responses from source to target with updated foreign keys."""
        cursor_source = conn_source.cursor()
        cursor_target = conn_target.cursor()

        print("\n📨 Copying responses...")
        total_responses = 0

        for old_task_id, new_task_id in self.task_id_map.items():
            cursor_source.execute(
                """
                SELECT request_id, status, headers, body, timestamp
                FROM responses WHERE task_id = ?
                ORDER BY id
            """,
                (old_task_id,),
            )

            responses = cursor_source.fetchall()
            for old_req_id, status, headers, body, timestamp in responses:
                new_req_id = self.request_id_map.get(old_req_id)

                cursor_target.execute(
                    """
                    INSERT INTO responses (task_id, request_id, status, headers, body, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?)
                """,
                    (new_task_id, new_req_id, status, headers, body, timestamp),
                )

                total_responses += 1

        print(f"   ✓ Copied {total_responses} responses")
        return total_responses

    def copy_file_folders(self):
        """Copy all file system folders with renamed task IDs."""
        print("\n📁 Copying file system data...")

        for old_id, new_id in self.task_id_map.items():
            # Copy captures
            src_captures = self.source_dir / "captures" / f"task_{old_id}"
            dst_captures = self.target_dir / "captures" / f"task_{new_id}"
            if src_captures.exists():
                shutil.copytree(src_captures, dst_captures)
                print(f"   ✓ Copied captures: task_{old_id} → task_{new_id}")

            # Copy screenshots (note: no underscore in folder name)
            src_screenshots = self.source_dir / "screenshots" / f"task{old_id}"
            dst_screenshots = self.target_dir / "screenshots" / f"task{new_id}"
            if src_screenshots.exists():
                shutil.copytree(src_screenshots, dst_screenshots)
                print(f"   ✓ Copied screenshots: task{old_id} → task{new_id}")

            # Copy doms if exists
            src_doms = self.source_dir / "doms" / f"task_{old_id}"
            dst_doms = self.target_dir / "doms" / f"task_{new_id}"
            if src_doms.exists():
                shutil.copytree(src_doms, dst_doms)
                print(f"   ✓ Copied DOMs: task_{old_id} → task_{new_id}")

        # Copy videos (need to find and rename based on pattern)
        src_videos = self.source_dir / "videos"
        dst_videos = self.target_dir / "videos"

        if src_videos.exists():
            for video_folder in src_videos.iterdir():
                if video_folder.is_dir():
                    # Extract task ID from folder name (e.g., task1_2025-10-02T16-18-35.856Z.mp4)
                    match = re.match(r"task(\d+)_(.*)", video_folder.name)
                    if match:
                        old_id = int(match.group(1))
                        if old_id in self.task_id_map:
                            new_id = self.task_id_map[old_id]
                            timestamp = match.group(2)
                            new_video_name = f"task{new_id}_{timestamp}"
                            dst_video = dst_videos / new_video_name
                            shutil.copytree(video_folder, dst_video)
                            print(
                                f"   ✓ Copied video: task{old_id}_{timestamp} → task{new_id}_{timestamp}"
                            )

        # Copy user-data as a single renamed folder
        src_user_data = self.source_dir / "user-data"
        if src_user_data.exists():
            task_ids = sorted(self.task_id_map.keys())
            min_id = min(task_ids)
            max_id = max(task_ids)
            dst_user_data = self.target_dir / f"user-data-{min_id}-to-{max_id}"
            shutil.copytree(src_user_data, dst_user_data)
            print(f"   ✓ Copied user-data → user-data-{min_id}-to-{max_id}")

    def verify_merge(self, conn: sqlite3.Connection) -> bool:
        """Verify the merge was successful."""
        cursor = conn.cursor()
        print("\n✅ Verifying merge...")

        all_good = True
        new_task_ids = list(self.task_id_map.values())

        # Check all tasks exist
        for old_id, new_id in self.task_id_map.items():
            cursor.execute("SELECT COUNT(*) FROM tasks WHERE id = ?", (new_id,))
            if cursor.fetchone()[0] == 0:
                print(f"   ❌ Task {new_id} not found!")
                all_good = False
            else:
                print(f"   ✓ Task {new_id} exists")

        # Check foreign key integrity for newly added tasks only
        placeholders = ",".join("?" * len(new_task_ids))

        cursor.execute(
            f"""
            SELECT COUNT(*) FROM steps 
            WHERE task_id IN ({placeholders})
            AND task_id NOT IN (SELECT id FROM tasks)
        """,
            new_task_ids,
        )
        orphaned_steps = cursor.fetchone()[0]
        if orphaned_steps > 0:
            print(f"   ❌ Found {orphaned_steps} orphaned steps in new tasks!")
            all_good = False
        else:
            print(f"   ✓ All new steps have valid task_id references")

        cursor.execute(
            f"""
            SELECT COUNT(*) FROM requests 
            WHERE task_id IN ({placeholders})
            AND task_id NOT IN (SELECT id FROM tasks)
        """,
            new_task_ids,
        )
        orphaned_requests = cursor.fetchone()[0]
        if orphaned_requests > 0:
            print(f"   ❌ Found {orphaned_requests} orphaned requests in new tasks!")
            all_good = False
        else:
            print(f"   ✓ All new requests have valid task_id references")

        cursor.execute(
            f"""
            SELECT COUNT(*) FROM responses 
            WHERE task_id IN ({placeholders})
            AND task_id NOT IN (SELECT id FROM tasks)
        """,
            new_task_ids,
        )
        orphaned_responses = cursor.fetchone()[0]
        if orphaned_responses > 0:
            print(f"   ❌ Found {orphaned_responses} orphaned responses in new tasks!")
            all_good = False
        else:
            print(f"   ✓ All new responses have valid task_id references")

        return all_good

    def run(self):
        """Execute the merge process."""
        print("=" * 70)
        print("🔄 Starting Data Merge: data_sofia → data")
        print("=" * 70)

        # Get current state
        max_task_id = self.get_max_task_id()
        source_task_ids = self.get_source_task_ids()

        print(f"\n📊 Current state:")
        print(f"   Target DB max task ID: {max_task_id}")
        print(f"   Source tasks to merge: {source_task_ids}")
        print(f"   New IDs will start from: {max_task_id + 1}")

        # Build ID mappings
        self.build_id_mappings(source_task_ids, max_task_id)

        # Connect to databases
        conn_source = sqlite3.connect(self.source_db)
        conn_target = sqlite3.connect(self.target_db)

        try:
            # Begin transaction
            conn_target.execute("BEGIN TRANSACTION")

            # Copy all database records
            tasks_copied = self.copy_tasks(conn_source, conn_target)
            steps_copied = self.copy_steps(conn_source, conn_target)
            requests_copied = self.copy_requests(conn_source, conn_target)
            responses_copied = self.copy_responses(conn_source, conn_target)

            # Commit database changes
            conn_target.commit()
            print("\n✅ Database changes committed")

            # Verify database integrity
            if not self.verify_merge(conn_target):
                print("\n❌ Verification failed! Rolling back...")
                conn_target.rollback()
                return False

            # Copy file system data
            self.copy_file_folders()

            # Final summary
            print("\n" + "=" * 70)
            print("✅ MERGE COMPLETED SUCCESSFULLY!")
            print("=" * 70)
            print(f"📈 Summary:")
            print(f"   Tasks copied: {tasks_copied}")
            print(f"   Steps copied: {steps_copied}")
            print(f"   Requests copied: {requests_copied}")
            print(f"   Responses copied: {responses_copied}")
            print(
                f"   New task ID range: {min(self.task_id_map.values())} - {max(self.task_id_map.values())}"
            )
            print("=" * 70)

            return True

        except Exception as e:
            print(f"\n❌ Error during merge: {e}")
            conn_target.rollback()
            import traceback

            traceback.print_exc()
            return False

        finally:
            conn_source.close()
            conn_target.close()


def main():
    """Main entry point."""
    # Get workspace root
    script_dir = Path(__file__).parent
    workspace_root = script_dir.parent.parent

    source_dir = workspace_root / "data_sofia"
    target_dir = workspace_root / "data"

    # Verify directories exist
    if not source_dir.exists():
        print(f"❌ Source directory not found: {source_dir}")
        sys.exit(1)

    if not target_dir.exists():
        print(f"❌ Target directory not found: {target_dir}")
        sys.exit(1)

    # Run merge
    merger = DataMerger(source_dir, target_dir)
    success = merger.run()

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
