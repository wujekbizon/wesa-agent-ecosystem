"""Fact storage (Postgres + pgvector / psycopg3 sync).

Facts are durable assertions with provenance. This store owns the SQL for:
  - writing a fact (with optional embedding as a ::vector literal)
  - dedup lookup by content_hash + scope tuple
  - contradiction lookup by (subject, predicate) with a *different* hash
  - the supersession write (new active row + old row revoked) — driven by the
    manager so it runs in one transaction.

Embeddings are passed in as a Python list[float]; this store does NOT call
Ollama. That keeps the store testable without a running model and keeps the
embedding round-trip in one place (the manager/retrieval layer).
"""
from __future__ import annotations

from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from src.memory.embeddings import to_pgvector_literal


class FactStore:
    def __init__(self, conn: psycopg.Connection):
        self.conn = conn

    def write(
        self,
        *,
        tenant_id: str,
        subject: str,
        predicate: str,
        content: str,
        content_hash: str,
        source_run_id: str,
        confidence: float,
        status: str,
        user_id: str | None = None,
        agent_id: str | None = None,
        source_turn_id: str | None = None,
        source: str | None = None,
        embedding: list[float] | None = None,
        metadata: dict[str, Any] | None = None,
        commit: bool = True,
    ) -> str:
        """Insert one fact row. embedding is bound as a ::vector literal when
        present, NULL otherwise (retrieval's vector tier skips NULL rows)."""
        emb_literal = to_pgvector_literal(embedding) if embedding is not None else None
        cur = self.conn.execute(
            """
            INSERT INTO fact_memory
              (tenant_id, user_id, agent_id, subject, predicate, content,
               content_hash, embedding, metadata, status, source,
               source_run_id, source_turn_id, confidence)
            VALUES
              (%(tid)s, %(uid)s, %(aid)s, %(subj)s, %(pred)s, %(content)s,
               %(chash)s, %(emb)s::vector, %(meta)s, %(status)s, %(source)s,
               %(run)s, %(turn)s, %(conf)s)
            RETURNING fact_id
            """,
            {
                "tid": tenant_id, "uid": user_id, "aid": agent_id,
                "subj": subject, "pred": predicate, "content": content,
                "chash": content_hash, "emb": emb_literal,
                "meta": Jsonb(metadata) if metadata is not None else None,
                "status": status, "source": source,
                "run": source_run_id, "turn": source_turn_id, "conf": confidence,
            },
        )
        fact_id = cur.fetchone()["fact_id"]
        if commit:
            self.conn.commit()
        return fact_id

    def find_dedup(
        self,
        *,
        content_hash: str,
        tenant_id: str,
        user_id: str | None,
        agent_id: str | None,
    ) -> str | None:
        """Return an existing active fact_id with the same content_hash in the
        same scope tuple, or None. Both halves of the key matter: same hash in a
        different scope is a different access boundary and stays separate."""
        cur = self.conn.execute(
            """
            SELECT fact_id FROM fact_memory
             WHERE tenant_id = %(tid)s
               AND content_hash = %(chash)s
               AND status = 'active'
               AND COALESCE(user_id, '')  = COALESCE(%(uid)s, '')
               AND COALESCE(agent_id, '') = COALESCE(%(aid)s, '')
             LIMIT 1
            """,
            {"tid": tenant_id, "chash": content_hash, "uid": user_id, "aid": agent_id},
        )
        row = cur.fetchone()
        return row["fact_id"] if row else None

    def find_contradiction(
        self,
        *,
        tenant_id: str,
        subject: str,
        predicate: str,
        content_hash: str,
        user_id: str | None,
    ) -> dict | None:
        """Find an active fact about the same (subject, predicate) in the same
        user scope but with DIFFERENT content — i.e. the new candidate
        contradicts it and should supersede it rather than create a parallel
        fact. Returns the existing row dict or None."""
        cur = self.conn.execute(
            """
            SELECT fact_id, content, confidence FROM fact_memory
             WHERE tenant_id = %(tid)s
               AND subject = %(subj)s
               AND predicate = %(pred)s
               AND status = 'active'
               AND content_hash <> %(chash)s
               AND COALESCE(user_id, '') = COALESCE(%(uid)s, '')
             ORDER BY created_at DESC
             LIMIT 1
            """,
            {"tid": tenant_id, "subj": subject, "pred": predicate,
             "chash": content_hash, "uid": user_id},
        )
        row = cur.fetchone()
        return dict(row) if row else None

    def revoke(self, fact_id: str, superseded_by: str, commit: bool = True) -> None:
        """Mark a fact revoked and point it at its replacement."""
        self.conn.execute(
            """
            UPDATE fact_memory
               SET status = 'revoked', superseded_by = %(new)s
             WHERE fact_id = %(old)s
            """,
            {"new": superseded_by, "old": fact_id},
        )
        if commit:
            self.conn.commit()

    def get(self, fact_id: str) -> dict | None:
        cur = self.conn.execute(
            """
            SELECT fact_id, subject, predicate, content, status, confidence,
                   superseded_by, source, user_id, agent_id
              FROM fact_memory WHERE fact_id = %(fid)s
            """,
            {"fid": fact_id},
        )
        row = cur.fetchone()
        return dict(row) if row else None

    def promote_provisional(self, fact_id: str, commit: bool = True) -> None:
        """Flip a provisional fact to active (e.g. confirmed by a 2nd source
        or curated by Axel)."""
        self.conn.execute(
            "UPDATE fact_memory SET status = 'active' "
            "WHERE fact_id = %(fid)s AND status = 'provisional'",
            {"fid": fact_id},
        )
        if commit:
            self.conn.commit()
