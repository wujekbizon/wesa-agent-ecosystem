"""Trace storage (Postgres / psycopg3 sync).

Append-only execution log — the flight recorder. High volume, never retrieved
for prompt assembly; replayed by run_id for forensics and distilled into
episodic/fact memory on task completion.
"""
from __future__ import annotations

from typing import Any

import psycopg
from psycopg.types.json import Jsonb

_VALID_EVENTS = frozenset({
    "task_queued", "task_started", "tool_call", "tool_result",
    "task_completed", "task_failed", "extraction_result", "promotion_result",
})


class TraceStore:
    def __init__(self, conn: psycopg.Connection):
        self.conn = conn

    def append(
        self,
        *,
        run_id: str,
        event_type: str,
        payload: dict[str, Any],
        tenant_id: str = "wesa",
        agent_id: str | None = None,
        user_id: str | None = None,
        turn_index: int = 0,
        token_cost: int | None = None,
        latency_ms: int | None = None,
        commit: bool = True,
    ) -> str:
        if event_type not in _VALID_EVENTS:
            raise ValueError(f"invalid trace event_type {event_type!r}")
        cur = self.conn.execute(
            """
            INSERT INTO trace_memory
              (run_id, tenant_id, user_id, agent_id, turn_index, event_type,
               payload, token_cost, latency_ms)
            VALUES
              (%(run)s, %(tid)s, %(uid)s, %(aid)s, %(turn)s, %(etype)s,
               %(payload)s, %(tok)s, %(lat)s)
            RETURNING trace_id
            """,
            {
                "run": run_id, "tid": tenant_id, "uid": user_id, "aid": agent_id,
                "turn": turn_index, "etype": event_type, "payload": Jsonb(payload),
                "tok": token_cost, "lat": latency_ms,
            },
        )
        trace_id = cur.fetchone()["trace_id"]
        if commit:
            self.conn.commit()
        return trace_id

    def replay(self, run_id: str) -> list[dict]:
        """All events for a run, in order — the forensic view."""
        cur = self.conn.execute(
            """
            SELECT turn_index, event_type, payload, token_cost, latency_ms, created_at
              FROM trace_memory
             WHERE run_id = %(run)s
             ORDER BY turn_index, created_at
            """,
            {"run": run_id},
        )
        return [dict(r) for r in cur.fetchall()]

    def recent(self, run_id: str, limit: int = 5) -> list[dict]:
        """The last few events of a run — feeds the <recent> prompt tail."""
        cur = self.conn.execute(
            """
            SELECT turn_index, event_type, payload
              FROM trace_memory
             WHERE run_id = %(run)s
             ORDER BY turn_index DESC
             LIMIT %(lim)s
            """,
            {"run": run_id, "lim": limit},
        )
        return [dict(r) for r in cur.fetchall()]

    def sweep_expired(self, days: int = 90, commit: bool = True) -> int:
        """Delete standard traces older than `days`. Traces backing promoted
        records should be protected by referencing source_run_id — for now this
        is a simple age sweep (nightly job)."""
        cur = self.conn.execute(
            "DELETE FROM trace_memory WHERE created_at < now() - make_interval(days => %(d)s)",
            {"d": days},
        )
        if commit:
            self.conn.commit()
        return cur.rowcount
