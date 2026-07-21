#!/usr/bin/env python3
"""Level 2 live smoke walk-through, against the WESA memory Neon project.

Exercises each Level 2 capability in order, printing a labelled result for each
so you can verify behaviour step by step:

  1. embedder check (real Ollama if available, else lexical-only)
  2. store a trusted fact            -> active
  3. store the same fact again       -> deduplicated
  4. store a contradicting fact      -> superseded (old revoked)
  5. store an inferred fact          -> provisional
  6. hybrid retrieval                -> ranked, scoped, tiered
  7. provisional fact is invisible to retrieval
  8. record + retrieve an episode
  9. append + replay a trace

Repeatable: it clears its own demo rows (source_run_id starting 'smoke_l2')
at the start, then leaves the results in the DB so you can inspect them in the
Neon console afterwards.

Run from agent-ecosystem/:  python scripts/smoke_memory_l2.py
"""
from __future__ import annotations

import pathlib
import sys

ECOSYSTEM_DIR = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ECOSYSTEM_DIR))

from src.memory.db import connect  # noqa: E402
from src.memory.embeddings import EmbeddingUnavailable, default_embedder  # noqa: E402
from src.memory.manager import WesaMemoryManager  # noqa: E402
from src.memory.records import MemoryCandidate  # noqa: E402
from src.memory.retrieval import retrieve_facts  # noqa: E402
from src.memory.stores.trace_store import TraceStore  # noqa: E402

RUN = "smoke_l2"
SUBJ = "demo:lumio"


def hr(step: str) -> None:
    print(f"\n=== {step} ===")


def _fact(content, **kw):
    base = dict(
        memory_type="fact", tenant_id="wesa", content=content, confidence=0.9,
        source_run_id=RUN, subject=SUBJ, predicate="network", source="axel_curated",
    )
    base.update(kw)
    return MemoryCandidate(**base)


def cleanup(conn):
    conn.execute("DELETE FROM fact_memory WHERE subject LIKE 'demo:%'")
    conn.execute("DELETE FROM episodic_memory WHERE source_run_id LIKE 'smoke_l2%'")
    conn.execute("DELETE FROM trace_memory WHERE run_id LIKE 'smoke_l2%'")
    conn.commit()


def main() -> int:
    conn = connect()
    cleanup(conn)

    # 1. embedder
    hr("1. embedder")
    embedder = default_embedder()
    try:
        v = embedder.embed("ping")
        print(f"Ollama OK: {embedder.model}, dim={len(v)} -> semantic search ON")
    except EmbeddingUnavailable as e:
        embedder = None
        print(f"Ollama unavailable ({str(e)[:70]}...) -> lexical-only mode")

    mgr = WesaMemoryManager(conn, embedder=embedder)

    # 2. trusted fact -> active
    hr("2. store trusted fact (axel_curated)")
    r = mgr.remember_fact(_fact("Lumio K3s cluster runs on Proxmox at 192.168.1.62"))
    print(f"outcome={r.outcome} status={r.status} id={r.record_id}")

    # 3. dedup
    hr("3. store the SAME fact again")
    r = mgr.remember_fact(_fact("Lumio K3s cluster runs on Proxmox at 192.168.1.62"))
    print(f"outcome={r.outcome} (points at existing id={r.record_id})")

    # 4. contradiction -> supersede
    hr("4. store a CONTRADICTING fact (same subject/predicate, new value)")
    r = mgr.remember_fact(_fact("Lumio K3s cluster runs on Proxmox at 192.168.1.99"))
    print(f"outcome={r.outcome} new_id={r.record_id} ({r.reason})")
    cur = conn.execute(
        "SELECT count(*) AS n FROM fact_memory WHERE subject=%(s)s AND predicate='network' "
        "AND status='active'", {"s": SUBJ})
    print(f"active facts for {SUBJ}/network: {cur.fetchone()['n']} (expect 1)")

    # 5. inferred -> provisional
    hr("5. store an INFERRED fact")
    r = mgr.remember_fact(_fact("Rumor: staging maybe moved", subject="demo:staging",
                                source="inferred"))
    print(f"outcome={r.outcome} status={r.status} (expect provisional)")

    # 6. retrieval
    hr("6. hybrid retrieval: 'where is the lumio cluster'")
    res = retrieve_facts(conn, query_text="where is the lumio cluster",
                         embedding=(embedder.embed("where is the lumio cluster") if embedder else None))
    print(f"mode={res.mode}")
    for row in res.rows:
        print(f"  [{row.relevance}] {row.text}")

    # 7. provisional invisible
    hr("7. retrieval must NOT surface the provisional 'staging' fact")
    res = retrieve_facts(conn, query_text="staging rumor",
                         embedding=(embedder.embed("staging rumor") if embedder else None))
    hits = [r.text for r in res.rows if "staging" in r.text.lower()]
    print(f"staging hits: {hits} (expect [])")

    # 8. episode
    hr("8. record + retrieve an episode")
    mgr.record_episode(task_type="sprint", title="Memory sprint L2",
                       summary="Built fact, episodic and trace memory with hybrid retrieval.",
                       outcome="completed", source_run_id=RUN)
    from src.memory.retrieval import retrieve_episodes
    res = retrieve_episodes(conn, query_text="hybrid retrieval memory",
                            embedding=(embedder.embed("hybrid retrieval memory") if embedder else None))
    for row in res.rows:
        print(f"  [{row.relevance}] {row.extra.get('title')}: {row.text}")

    # 9. trace
    hr("9. append + replay a trace")
    ts = TraceStore(conn)
    ts.append(run_id=RUN, event_type="task_started", payload={"agent": "demo"}, turn_index=0)
    ts.append(run_id=RUN, event_type="task_completed", payload={"ok": True}, turn_index=1)
    print("replay:", [e["event_type"] for e in ts.replay(RUN)])

    conn.close()
    print("\nDone. Rows left in the DB for inspection (subjects 'demo:%').")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
