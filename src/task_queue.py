"""
WESA Agent Ecosystem - SQLite Task Queue
Replaces the file-based JSON queue with a persistent SQLite backend.
Single-process safe. Use output_file field in tasks for direct file deployment.
"""

import os
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

DB_PATH = os.getenv("WESA_TASK_DB", "data/tasks.db")
VALID_STATUSES = {"pending", "in_progress", "done", "failed"}


def get_connection() -> sqlite3.Connection:
    """Returns a connection with row_factory set for dict-style access."""
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Initializes the database schema if it doesn't exist."""
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            prompt TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('pending', 'in_progress', 'done', 'failed')),
            model TEXT,
            output_file TEXT,
            created_at TEXT NOT NULL,
            completed_at TEXT,
            result TEXT,
            error TEXT
        )
    """)
    conn.commit()


class TaskQueue:
    """
    SQLite-backed task queue for the WESA agent ecosystem.
    Supports add, fetch, update, and list operations.
    """

    def __init__(self, db_path: str | None = None):
        self._db_path = db_path or DB_PATH
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        self._ensure_schema()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._get_conn() as conn:
            init_db(conn)

    def add_task(self, task: dict[str, Any]) -> str:
        """
        Adds a new task with status 'pending'.

        Args:
            task: Dict with required 'type' and 'prompt', optional 'model' and 'output_file'.

        Returns:
            str: The generated unique task ID (YYYY-MM-DD_<hex8>).
        """
        if "type" not in task or "prompt" not in task:
            raise ValueError("Task must contain 'type' and 'prompt'.")

        now_iso = datetime.now(timezone.utc).isoformat()
        task_id = f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}-{uuid.uuid4().hex[:8]}"

        conn = self._get_conn()
        try:
            conn.execute(
                """
                INSERT INTO tasks (id, type, prompt, status, model, output_file, created_at, completed_at, result, error)
                VALUES (?, ?, ?, 'pending', ?, ?, ?, NULL, NULL, NULL)
                """,
                (
                    task_id,
                    task["type"],
                    task["prompt"],
                    task.get("model"),
                    task.get("output_file"),
                    now_iso,
                ),
            )
            conn.commit()
            return task_id
        except sqlite3.IntegrityError as e:
            conn.rollback()
            raise RuntimeError(f"Failed to add task: {e}")
        finally:
            conn.close()

    def get_next_pending(self) -> dict[str, Any] | None:
        """Retrieves the oldest pending task. Caller must immediately update status to in_progress."""
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM tasks WHERE status = 'pending' ORDER BY created_at ASC LIMIT 1"
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def has_task(self, task_id: str) -> bool:
        """Returns True if a task with this ID already exists in the DB."""
        conn = self._get_conn()
        try:
            row = conn.execute("SELECT id FROM tasks WHERE id = ?", (task_id,)).fetchone()
            return row is not None
        finally:
            conn.close()

    def import_task(self, task_data: dict) -> None:
        """
        Import an existing task dict into the queue, preserving its ID and status.
        Used for syncing JSON files into SQLite on startup. Silently skips duplicates.
        """
        task_id = task_data.get("id")
        if not task_id:
            raise ValueError("task_data must have an 'id' field.")
        if self.has_task(task_id):
            return
        conn = self._get_conn()
        try:
            conn.execute(
                """
                INSERT INTO tasks (id, type, prompt, status, model, output_file, created_at, completed_at, result, error)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    task_data.get("type", "general"),
                    task_data.get("prompt", ""),
                    task_data.get("status", "pending"),
                    task_data.get("model_used") or task_data.get("model"),
                    task_data.get("output_file"),
                    task_data.get("created_at") or datetime.now(timezone.utc).isoformat(),
                    task_data.get("completed_at"),
                    task_data.get("result"),
                    task_data.get("error"),
                ),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            pass  # already exists, fine
        finally:
            conn.close()

    def update_status(
        self, task_id: str, status: str, result: str = "", error: str = "", model_used: str = ""
    ) -> None:
        """
        Updates task status and optional result/error/model.

        Args:
            task_id: The task's unique ID.
            status: New status value.
            result: Output string on success.
            error: Error string on failure.
            model_used: Model that processed the task (stored in 'model' column).
        """
        if status not in VALID_STATUSES:
            raise ValueError(f"Invalid status '{status}'. Must be one of {VALID_STATUSES}")

        completed_at = (
            datetime.now(timezone.utc).isoformat() if status in ("done", "failed") else None
        )

        conn = self._get_conn()
        try:
            cursor = conn.execute(
                """
                UPDATE tasks
                SET status = ?, result = ?, error = ?, completed_at = ?,
                    model = COALESCE(NULLIF(?, ''), model)
                WHERE id = ?
                """,
                (status, result, error, completed_at, model_used, task_id),
            )
            if cursor.rowcount == 0:
                raise ValueError(f"Task '{task_id}' not found.")
            conn.commit()
        finally:
            conn.close()

    def get_all(self) -> list[dict[str, Any]]:
        """Returns all tasks ordered by creation time (newest first)."""
        conn = self._get_conn()
        try:
            rows = conn.execute("SELECT * FROM tasks ORDER BY created_at DESC").fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()


if __name__ == "__main__":
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        tmp_db = f.name

    queue = TaskQueue(db_path=tmp_db)

    id1 = queue.add_task({"type": "code", "prompt": "Write a hello world function"})
    id2 = queue.add_task(
        {"type": "research", "prompt": "Research LlamaIndex", "output_file": "src/test.py"}
    )

    print(f"Added: {id1}, {id2}")

    next_task = queue.get_next_pending()
    print(f"Next pending: {next_task['id']} [{next_task['type']}]")

    queue.update_status(next_task["id"], "in_progress")
    queue.update_status(next_task["id"], "done", result="def hello(): return 'world'")

    for t in queue.get_all():
        print(f"  {t['id']} | {t['status']} | output_file={t['output_file']}")

    os.unlink(tmp_db)
    print("Smoke test passed.")
