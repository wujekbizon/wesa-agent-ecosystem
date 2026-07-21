"""Versioned policy storage (Postgres / psycopg3 sync).

Rewritten from the Oracle demo's stores/policy.py:
  - :named binds          -> %(name)s
  - SYSTIMESTAMP          -> now()
  - FETCH FIRST n ROWS    -> LIMIT n
  - LOB .read() handling  -> dropped; psycopg3 returns jsonb as dict

Policies are manual-write only (Axel/Greg); never auto-promoted. An update
is a new version: set_policy(version=N+1) and close the prior row by calling
supersede() so only one row per key has effective_until IS NULL.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Any

import psycopg
from psycopg.types.json import Jsonb


def _pid() -> str:
    return f"pol_{secrets.token_hex(6)}"


class PolicyStore:
    def __init__(self, conn: psycopg.Connection):
        self.conn = conn

    def set_policy(
        self,
        tenant_id: str,
        policy_key: str,
        policy_value: dict[str, Any],
        policy_type: str,
        version: int = 1,
        effective_from: datetime | None = None,
        effective_until: datetime | None = None,
        created_by: str = "axel",
        commit: bool = True,
    ) -> str:
        policy_id = _pid()
        effective_from = effective_from or datetime.now(tz=timezone.utc)
        self.conn.execute(
            """
            INSERT INTO policy_memory
              (policy_id, tenant_id, policy_type, policy_key, policy_value,
               version, effective_from, effective_until, created_by)
            VALUES
              (%(pid)s, %(tid)s, %(ptype)s, %(pkey)s, %(pval)s,
               %(ver)s, %(efrom)s, %(euntil)s, %(cby)s)
            """,
            {
                "pid": policy_id,
                "tid": tenant_id,
                "ptype": policy_type,
                "pkey": policy_key,
                "pval": Jsonb(policy_value),
                "ver": version,
                "efrom": effective_from,
                "euntil": effective_until,
                "cby": created_by,
            },
        )
        if commit:
            self.conn.commit()
        return policy_id

    def ensure_policy(
        self,
        tenant_id: str,
        policy_key: str,
        policy_value: dict[str, Any],
        policy_type: str,
        created_by: str = "axel",
        commit: bool = True,
    ) -> tuple[str | None, str]:
        """Idempotent write. Re-running is safe:

          - no active row for this key  -> insert version 1     ("created")
          - active row, same value      -> no-op                ("unchanged")
          - active row, different value -> supersede old, write
                                           version N+1           ("updated")

        Returns (policy_id, action). This is what seeding scripts and the
        smoke test should call instead of set_policy(), which is a raw insert
        that collides on the (tenant, key, version) unique constraint when run
        twice.
        """
        current = self.get(tenant_id, policy_key)
        if current is None:
            pid = self.set_policy(
                tenant_id, policy_key, policy_value, policy_type,
                version=1, created_by=created_by, commit=commit,
            )
            return pid, "created"
        if current["value"] == policy_value:
            return current["policy_id"], "unchanged"
        # Changed: close the old version and write the next one, atomically.
        self.supersede(tenant_id, policy_key, commit=False)
        pid = self.set_policy(
            tenant_id, policy_key, policy_value, policy_type,
            version=current["version"] + 1, created_by=created_by, commit=commit,
        )
        return pid, "updated"

    def supersede(
        self, tenant_id: str, policy_key: str, commit: bool = True
    ) -> None:
        """Close the currently-active version of a policy by stamping
        effective_until = now(). Used before writing a new version."""
        self.conn.execute(
            """
            UPDATE policy_memory
               SET effective_until = now()
             WHERE tenant_id = %(tid)s AND policy_key = %(pkey)s
               AND effective_until IS NULL
            """,
            {"tid": tenant_id, "pkey": policy_key},
        )
        if commit:
            self.conn.commit()

    def get(self, tenant_id: str, policy_key: str) -> dict | None:
        cur = self.conn.execute(
            """
            SELECT policy_id, policy_value, version, policy_type
              FROM policy_memory
             WHERE tenant_id = %(tid)s AND policy_key = %(pkey)s
               AND (effective_until IS NULL OR effective_until > now())
             ORDER BY version DESC
             LIMIT 1
            """,
            {"tid": tenant_id, "pkey": policy_key},
        )
        row = cur.fetchone()
        if not row:
            return None
        return {
            "policy_id": row["policy_id"],
            "value": row["policy_value"],
            "version": row["version"],
            "type": row["policy_type"],
        }

    def list_for_tenant(self, tenant_id: str) -> list[dict]:
        """All active policies for a tenant. Path A (session startup) load."""
        cur = self.conn.execute(
            """
            SELECT policy_key, policy_value, policy_type, version
              FROM policy_memory
             WHERE tenant_id = %(tid)s
               AND (effective_until IS NULL OR effective_until > now())
             ORDER BY policy_key, version DESC
            """,
            {"tid": tenant_id},
        )
        return [
            {
                "key": row["policy_key"],
                "value": row["policy_value"],
                "type": row["policy_type"],
                "version": row["version"],
            }
            for row in cur.fetchall()
        ]
