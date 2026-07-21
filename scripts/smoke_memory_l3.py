#!/usr/bin/env python3
"""Level 3 live walk-through: assemble a task prompt from memory, then capture
new memory from a finished task.

  1. seed a policy + preference + an authoritative fact (idempotent)
  2. build_task_prompt(query) -> the token-budgeted <context> the agent sees,
     assembled fresh from policies + preferences + retrieved facts/episodes
  3. post_task(...) -> extract candidates from a finished task, push through the
     gate, and emit a trace envelope; then replay the trace

Run from agent-ecosystem/:  python scripts/smoke_memory_l3.py
"""
from __future__ import annotations

import pathlib
import sys

ECOSYSTEM_DIR = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ECOSYSTEM_DIR))

from src.memory.db import connect  # noqa: E402
from src.memory.embeddings import EmbeddingUnavailable, default_embedder  # noqa: E402
from src.memory.loop import build_task_prompt, post_task  # noqa: E402
from src.memory.manager import WesaMemoryManager  # noqa: E402
from src.memory.records import MemoryCandidate  # noqa: E402

RUN = "smoke_l3"


def main() -> int:
    conn = connect()
    conn.execute("DELETE FROM fact_memory WHERE subject LIKE 'demo:%'")
    conn.execute("DELETE FROM trace_memory WHERE run_id LIKE 'smoke_l3%'")
    conn.commit()

    embedder = default_embedder()
    try:
        embedder.embed("ping")
        mode = "semantic ON"
    except EmbeddingUnavailable:
        embedder, mode = None, "lexical-only"
    print(f"embedder: {mode}")

    mgr = WesaMemoryManager(conn, embedder=embedder)

    # 1. seed
    mgr.policy.set_policy("wesa", "smoke_l3_rule", {"demo": True}, "routing")
    mgr.preference.set("wesa", "verbosity", "terse", "greg_stated")
    mgr.remember_fact(MemoryCandidate(
        memory_type="fact", tenant_id="wesa",
        content="Lumio K3s cluster runs on Proxmox at 192.168.1.62",
        confidence=0.95, source_run_id=RUN, subject="demo:lumio",
        predicate="network", source="axel_curated"))

    # 2. assemble a task prompt from memory
    print("\n=== build_task_prompt('where does lumio run') ===")
    prompt, env = build_task_prompt(mgr, query_text="where does lumio run",
                                    agent_id="d1", token_budget=1500)
    print(prompt.text)
    print(f"\ntokens~{prompt.tokens} mode={prompt.retrieval_mode} "
          f"facts_kept={prompt.facts_kept} dropped={prompt.facts_dropped}")
    print(f"prefs_applied={env.preferences_applied} facts_returned={env.facts_returned}")

    # 3. capture memory from a finished task
    print("\n=== post_task: capture from a finished task ===")
    env2 = post_task(
        mgr, run_id=RUN, agent_id="d1",
        task_text="Confirmed D1 on qwen3.5-opus and the box is at 192.168.1.77",
        result="task done")
    for p in env2.promotions:
        print(f"  promoted: type={p['type']} subject={p['subject']} "
              f"outcome={p['outcome']} status={p['status']}")
    print("trace replay:", [e["event_type"] for e in mgr.trace.replay(RUN)])

    conn.close()
    print("\nDone. (Demo rows left for inspection; re-running is safe.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
