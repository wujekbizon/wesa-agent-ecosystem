"""Postgres connection helper for the memory system.

Sync psycopg3 to match WESA's existing synchronous codebase (router, the
Neon sync/export scripts). Reads NEON_DATABASE_URL the same way
scripts/sync_to_neon.py does, but prefers WesaConfig when the key is modeled
there.

The memory system lives in the `memory` schema of the same Neon project as
Wolfmed (plan §4). We set search_path on every connection so unqualified
table names resolve to memory.* without each query repeating the prefix.
"""
from __future__ import annotations

import os
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

# Memory lives in its own dedicated Neon project (fully separate from the
# Wolfmed project — separation of concerns). Prefer the dedicated key; fall
# back to NEON_DATABASE_URL only so existing tooling keeps working in dev.
_ENV_KEYS = ("WESA_MEMORY_DATABASE_URL", "NEON_DATABASE_URL")
_SEARCH_PATH = "memory, public"


def _load_dsn_from_env_files() -> str | None:
    """Resolve a DSN from process env or a .env file, trying the dedicated
    WESA memory key first, then NEON_DATABASE_URL. Mirrors how
    scripts/sync_to_neon.py reads .env."""
    for key in _ENV_KEYS:
        if os.environ.get(key):
            return os.environ[key]
    here = Path(__file__).resolve()
    ecosystem_dir = here.parents[2]  # src/memory/db.py -> agent-ecosystem/
    wesa_dir = ecosystem_dir.parent
    candidates = [
        ecosystem_dir / ".env",
        wesa_dir / "wolfmed" / ".env",
        wesa_dir / "wolfmed" / ".env.local",
    ]
    for env_path in candidates:
        if not env_path.exists():
            continue
        lines = env_path.read_text(encoding="utf-8").splitlines()
        for key in _ENV_KEYS:
            for line in lines:
                line = line.strip()
                if line.startswith(f"{key}=") and not line.startswith("#"):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def resolve_dsn(dsn: str | None = None) -> str:
    """Resolve the Postgres DSN. Explicit arg > env vars > .env files.

    Raises a clear error rather than letting psycopg fail obscurely when no
    connection string is configured (common in CI / fresh checkouts)."""
    resolved = dsn or _load_dsn_from_env_files()
    if not resolved:
        raise RuntimeError(
            "No Postgres DSN: pass dsn=... or set WESA_MEMORY_DATABASE_URL "
            "(preferred) or NEON_DATABASE_URL in env or agent-ecosystem/.env."
        )
    return resolved


def connect(dsn: str | None = None, *, row_factory=dict_row) -> psycopg.Connection:
    """Open a sync connection with search_path pinned to the memory schema.

    Caller owns the connection lifecycle (commit/rollback/close). Stores take
    a connection in their constructor so the promotion gate can batch several
    store operations into one transaction.
    """
    conn = psycopg.connect(resolve_dsn(dsn), row_factory=row_factory)
    conn.execute(f"SET search_path TO {_SEARCH_PATH}")
    return conn
