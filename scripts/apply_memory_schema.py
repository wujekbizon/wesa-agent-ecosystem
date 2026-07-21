#!/usr/bin/env python3
"""Apply a memory-system schema file to Neon Postgres.

Idempotent: every statement is CREATE ... IF NOT EXISTS, so re-running is safe.
By default applies Level 1 (policy + preference). Pass a different --file for
later levels.

Usage:
    python scripts/apply_memory_schema.py --dry-run        # print, don't run
    python scripts/apply_memory_schema.py                  # apply level 1
    python scripts/apply_memory_schema.py --file src/memory/schema_level2.sql

Requires NEON_DATABASE_URL (env or agent-ecosystem/.env), same as
scripts/sync_to_neon.py.
"""
from __future__ import annotations

import argparse
import pathlib
import sys

ECOSYSTEM_DIR = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ECOSYSTEM_DIR))

from src.memory.db import connect, resolve_dsn  # noqa: E402

_DEFAULT_SCHEMA = ECOSYSTEM_DIR / "src" / "memory" / "schema_level1.sql"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--file", default=str(_DEFAULT_SCHEMA), help="schema .sql to apply")
    ap.add_argument("--dry-run", action="store_true", help="print SQL, do not execute")
    ap.add_argument(
        "--force", action="store_true",
        help="allow applying to a database whose name looks like Wolfmed prod",
    )
    args = ap.parse_args()

    sql_path = pathlib.Path(args.file)
    if not sql_path.is_absolute():
        sql_path = ECOSYSTEM_DIR / sql_path
    sql = sql_path.read_text(encoding="utf-8")

    dsn = resolve_dsn()
    dsn_tail = dsn.split("@")[-1]

    if args.dry_run:
        print(f"-- DRY RUN: would apply {sql_path} to {dsn_tail}\n")
        print(sql)
        return 0

    # Safety: memory belongs in its own database. Refuse to write into a DB
    # whose name looks like Wolfmed production unless explicitly forced.
    if "wolfmed" in dsn_tail.lower() and not args.force:
        print(
            "REFUSING: target database looks like Wolfmed prod "
            f"({dsn_tail}).\nSet WESA_MEMORY_DATABASE_URL to the dedicated "
            "memory database, or pass --force if this is intentional.",
            file=sys.stderr,
        )
        return 2

    print(f"Applying {sql_path.name} to {dsn_tail} ...")
    conn = connect()
    try:
        conn.execute(sql)
        conn.commit()
    finally:
        conn.close()
    print("Done. Schema applied (idempotent).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
