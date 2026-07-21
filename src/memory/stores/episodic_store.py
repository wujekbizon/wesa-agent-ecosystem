"""Episodic storage (Postgres + pgvector / psycopg3 sync).

Episodes are distilled summaries of completed work, written on task completion.
Retrieval is hybrid over the `summary` (handled in retrieval.py); this store
owns writes and simple reads.
"""
from __future__ import annotations

from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from src.memory.embeddings import to_pgvector_literal


class EpisodicStore:
    def __init__(self, conn: psycopg.Connection):
        self.conn = conn

    def write(
        self,
        *,
        tenant_id: str,
        task_type: str,
        title: str,
        summary: str,
        outcome: str,
        source_run_id: str,
        status: str = "active",
        user_id: str | None = None,
        key_steps: list[str] | None = None,
        artifacts: dict[str, Any] | None = None,
        embedding: list[float] | None = None,
        commit: bool = True,
    ) -> str:
        emb_literal = to_pgvector_literal(embedding) if embedding is not None else None
        cur = self.conn.execute(
            """
            INSERT INTO episodic_memory
              (tenant_id, user_id, task_type, title, summary, outcome,
               key_steps, artifacts, embedding, status, source_run_id)
            VALUES
              (%(tid)s, %(uid)s, %(tt)s, %(title)s, %(summary)s, %(outcome)s,
               %(steps)s, %(arts)s, %(emb)s::vector, %(status)s, %(run)s)
            RETURNING episode_id
            """,
            {
                "tid": tenant_id, "uid": user_id, "tt": task_type,
                "title": title, "summary": summary, "outcome": outcome,
                "steps": Jsonb(key_steps) if key_steps is not None else None,
                "arts": Jsonb(artifacts) if artifacts is not None else None,
                "emb": emb_literal, "status": status, "run": source_run_id,
            },
        )
        episode_id = cur.fetchone()["episode_id"]
        if commit:
            self.conn.commit()
        return episode_id

    def get(self, episode_id: str) -> dict | None:
        cur = self.conn.execute(
            """
            SELECT episode_id, task_type, title, summary, outcome, status,
                   key_steps, artifacts
              FROM episodic_memory WHERE episode_id = %(eid)s
            """,
            {"eid": episode_id},
        )
        row = cur.fetchone()
        return dict(row) if row else None

    def list_recent(self, tenant_id: str, task_type: str | None = None,
                    limit: int = 10) -> list[dict]:
        cur = self.conn.execute(
            """
            SELECT episode_id, task_type, title, summary, outcome, completed_at
              FROM episodic_memory
             WHERE tenant_id = %(tid)s AND status = 'active'
               AND (%(tt)s IS NULL OR task_type = %(tt)s)
             ORDER BY completed_at DESC
             LIMIT %(lim)s
            """,
            {"tid": tenant_id, "tt": task_type, "lim": limit},
        )
        return [dict(r) for r in cur.fetchall()]
