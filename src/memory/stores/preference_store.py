"""Per-scope preferences (Postgres / psycopg3 sync).

Rewritten from the Oracle demo's stores/preference.py. The Oracle MERGE ...
USING dual becomes Postgres INSERT ... ON CONFLICT, targeting the COALESCE
expression index (uq_pref_scope_key) so NULL user_id/agent_id scopes upsert
correctly instead of inserting duplicates.

Scope: (tenant_id, user_id, agent_id, pref_key). user_id NULL = global;
agent_id NULL = not agent-specific.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

# ON CONFLICT must name the same expression as uq_pref_scope_key in
# schema_level1.sql. Postgres matches inference targets by expression.
_CONFLICT_TARGET = "(tenant_id, COALESCE(user_id, ''), COALESCE(agent_id, ''), pref_key)"


class PreferenceStore:
    def __init__(self, conn: psycopg.Connection):
        self.conn = conn

    def set(
        self,
        tenant_id: str,
        pref_key: str,
        pref_value: Any,
        source: str,
        user_id: str | None = None,
        agent_id: str | None = None,
        confidence: float | None = None,
        commit: bool = True,
    ) -> None:
        """Upsert a preference row. commit=False lets the promotion gate batch
        this write with its checks into one transaction."""
        ts = datetime.now(tz=timezone.utc)
        self.conn.execute(
            f"""
            INSERT INTO preference_memory
              (tenant_id, user_id, agent_id, pref_key, pref_value,
               source, confidence, updated_at)
            VALUES
              (%(tid)s, %(uid)s, %(aid)s, %(pkey)s, %(pval)s,
               %(src)s, %(conf)s, %(ts)s)
            ON CONFLICT {_CONFLICT_TARGET} DO UPDATE SET
              pref_value = EXCLUDED.pref_value,
              source     = EXCLUDED.source,
              confidence = EXCLUDED.confidence,
              updated_at = EXCLUDED.updated_at
            """,
            {
                "tid": tenant_id,
                "uid": user_id,
                "aid": agent_id,
                "pkey": pref_key,
                "pval": Jsonb(pref_value),
                "src": source,
                "conf": confidence,
                "ts": ts,
            },
        )
        if commit:
            self.conn.commit()

    def get(
        self,
        tenant_id: str,
        pref_key: str,
        user_id: str | None = None,
        agent_id: str | None = None,
    ) -> dict | None:
        cur = self.conn.execute(
            """
            SELECT pref_value, source, confidence
              FROM preference_memory
             WHERE tenant_id = %(tid)s AND pref_key = %(pkey)s
               AND COALESCE(user_id, '')  = COALESCE(%(uid)s, '')
               AND COALESCE(agent_id, '') = COALESCE(%(aid)s, '')
            """,
            {"tid": tenant_id, "pkey": pref_key, "uid": user_id, "aid": agent_id},
        )
        row = cur.fetchone()
        if not row:
            return None
        return {
            "value": row["pref_value"],
            "source": row["source"],
            "confidence": (
                float(row["confidence"]) if row["confidence"] is not None else None
            ),
        }

    def list_for_scope(
        self,
        tenant_id: str,
        user_id: str | None = None,
        agent_id: str | None = None,
    ) -> list[dict]:
        """Load preferences for a session: global rows (user_id IS NULL) plus
        the given user, and global-agent rows plus the given agent. This is the
        Path A preference load — exhaustive, no ranking."""
        cur = self.conn.execute(
            """
            SELECT pref_key, pref_value, source, confidence, user_id, agent_id
              FROM preference_memory
             WHERE tenant_id = %(tid)s
               AND (user_id IS NULL OR user_id = %(uid)s)
               AND (agent_id IS NULL OR agent_id = %(aid)s)
             ORDER BY pref_key
            """,
            {"tid": tenant_id, "uid": user_id, "aid": agent_id},
        )
        return [
            {
                "key": row["pref_key"],
                "value": row["pref_value"],
                "source": row["source"],
                "confidence": (
                    float(row["confidence"])
                    if row["confidence"] is not None
                    else None
                ),
                "user_id": row["user_id"],
                "agent_id": row["agent_id"],
            }
            for row in cur.fetchall()
        ]
