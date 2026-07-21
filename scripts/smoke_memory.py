#!/usr/bin/env python3
"""Level 1 live smoke test: write one policy + one preference to the WESA
memory DB, then read them back via session_startup().

Run from anywhere:  python scripts/smoke_memory.py
Targets WESA_MEMORY_DATABASE_URL (see src/memory/db.py).
"""
from __future__ import annotations

import pathlib
import sys

# Put the repo root (agent-ecosystem/) on sys.path so `src.*` imports resolve
# when this file is run directly as a script.
ECOSYSTEM_DIR = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ECOSYSTEM_DIR))

from src.memory.db import connect  # noqa: E402
from src.memory.manager import WesaMemoryManager  # noqa: E402
from src.memory.records import MemoryCandidate  # noqa: E402


def main() -> int:
    conn = connect()
    try:
        mgr = WesaMemoryManager(conn)

        _pid, action = mgr.policy.ensure_policy(
            tenant_id="wesa",
            policy_key="git_protected_paths",
            policy_value={"never_push": ["_plans/", "operations/"]},
            policy_type="git",
        )
        print(f"policy ensure -> {action}")

        res = mgr.remember_preference(
            MemoryCandidate(
                memory_type="preference", tenant_id="wesa", content="",
                confidence=1.0, source_run_id="smoke",
                pref_key="verbosity", pref_value="terse", source="greg_stated",
            )
        )
        print(f"preference promotion -> {res.outcome}")

        ctx = mgr.session_startup(user_id="greg")
        print("--- session_startup() ---")
        print(ctx.to_prompt_text())
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
