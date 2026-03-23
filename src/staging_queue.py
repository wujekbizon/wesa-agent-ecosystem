import sqlite3
import uuid
import os
from datetime import datetime
from typing import Optional

DB_DEFAULT_PATH = "logs/staging.db"

class StagingQueue:
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or DB_DEFAULT_PATH
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS staging_tasks (
                    id TEXT PRIMARY KEY,
                    topic TEXT NOT NULL,
                    source_pipeline TEXT,
                    brief_content TEXT NOT NULL,
                    raw_research TEXT,
                    status TEXT NOT NULL CHECK (status IN ('awaiting_axel','approved','discarded','resend')),
                    axel_notes TEXT,
                    created_at TEXT NOT NULL,
                    reviewed_at TEXT
                )
            """)
            conn.commit()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def add_brief(self, topic: str, brief_content: str, raw_research: str = "", source_pipeline: str = "") -> str:
        now = datetime.utcnow().isoformat()
        # ID format: YYYY-MM-DD-<hex8>
        date_part = now[:10]
        unique_hex = uuid.uuid4().hex[:8]
        brief_id = f"{date_part}-{unique_hex}"

        status = "awaiting_axel"
        reviewed_at = None
        axel_notes = None

        with self._get_connection() as conn:
            cursor = conn.execute("""
                INSERT INTO staging_tasks (id, topic, source_pipeline, brief_content, raw_research, status, axel_notes, created_at, reviewed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (brief_id, topic, source_pipeline, brief_content, raw_research, status, axel_notes, now, reviewed_at))
            conn.commit()

        return brief_id

    def get_awaiting(self) -> list[dict]:
        with self._get_connection() as conn:
            rows = conn.execute("""
                SELECT * FROM staging_tasks
                WHERE status = 'awaiting_axel'
                ORDER BY created_at ASC
            """).fetchall()
        return [dict(row) for row in rows]

    def review(self, brief_id: str, decision: str, axel_notes: str = "") -> None:
        if decision not in ('approved', 'discarded', 'resend'):
            raise ValueError(f"Unknown decision: {decision}")

        reviewed_at = datetime.utcnow().isoformat()

        with self._get_connection() as conn:
            # Check if ID exists first to raise specific error if missing
            cursor = conn.execute("SELECT 1 FROM staging_tasks WHERE id = ?", (brief_id,))
            if not cursor.fetchone():
                raise ValueError(f"Unknown brief ID: {brief_id}")

            conn.execute("""
                UPDATE staging_tasks
                SET status = ?, axel_notes = ?, reviewed_at = ?
                WHERE id = ?
            """, (decision, axel_notes, reviewed_at, brief_id))
            conn.commit()

    def get_all(self) -> list[dict]:
        with self._get_connection() as conn:
            rows = conn.execute("""
                SELECT * FROM staging_tasks
                ORDER BY created_at DESC
            """).fetchall()
        return [dict(row) for row in rows]


if __name__ == '__main__':
    import tempfile

    # Create a temporary DB for smoke test
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        temp_db_path = tmp.name

    try:
        queue = StagingQueue(db_path=temp_db_path)

        # Add 2 briefs
        id1 = queue.add_brief("Market Analysis", "Brief content 1", "Raw research 1", "Pipeline A")
        id2 = queue.add_brief("AI Trends", "Brief content 2", "Raw research 2", "Pipeline B")

        print(f"Added briefs: {id1}, {id2}")

        # Add 3rd one to test ordering
        id3 = queue.add_brief("Third Item", "Brief content 3", "Raw research 3")

        # Review one
        queue.review(id1, "approved", "Approved for publication.")

        # Get awaiting
        awaiting = queue.get_awaiting()
        print(f"Awaiting review: {[item['id'] for item in awaiting]}")

        # Get all
        all_items = queue.get_all()
        print(f"All items (newest first):")
        for item in all_items:
            print(f"  - {item['id']}: status={item['status']}")

    finally:
        # Clean up temp file
        if os.path.exists(temp_db_path):
            os.unlink(temp_db_path)