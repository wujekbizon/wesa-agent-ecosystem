"""Level 2 memory tests: facts, episodes, traces, hybrid retrieval, promotion
(dedup / contradiction / supersession / provisional), and GDPR cascade.

Runs against a real Postgres + pgvector via pgserver (bundled). A deterministic
FakeEmbedder stands in for Ollama so the vector path is exercised without a
running model.
"""
from __future__ import annotations

import hashlib
import math
import pathlib
import re

import pytest

pytest.importorskip("psycopg")
pgserver = pytest.importorskip("pgserver")

import psycopg  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402

from src.memory.manager import WesaMemoryManager  # noqa: E402
from src.memory.records import MemoryCandidate  # noqa: E402
from src.memory.retrieval import retrieve_facts  # noqa: E402
from src.memory.stores.trace_store import TraceStore  # noqa: E402

_MEM = pathlib.Path(__file__).resolve().parents[1] / "src" / "memory"
_SCHEMA_L1 = _MEM / "schema_level1.sql"
_SCHEMA_L2 = _MEM / "schema_level2.sql"


class FakeEmbedder:
    """Deterministic bag-of-words hashing embedder. Similar text -> similar
    vectors, so semantic retrieval is meaningfully exercised."""

    dim = 768

    def embed(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        for tok in re.findall(r"\w+", text.lower()):
            idx = int(hashlib.md5(tok.encode()).hexdigest(), 16) % self.dim
            v[idx] += 1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]


@pytest.fixture(scope="module")
def pg_uri(tmp_path_factory):
    server = pgserver.get_server(str(tmp_path_factory.mktemp("wesa_pg_l2")))
    yield server.get_uri()
    server.cleanup()


@pytest.fixture
def conn(pg_uri):
    c = psycopg.connect(pg_uri, row_factory=dict_row)
    c.execute("DROP SCHEMA IF EXISTS memory CASCADE")
    c.execute(_SCHEMA_L1.read_text(encoding="utf-8"))
    c.execute(_SCHEMA_L2.read_text(encoding="utf-8"))
    c.execute("SET search_path TO memory, public")
    c.commit()
    yield c
    c.close()


@pytest.fixture
def mgr(conn):
    return WesaMemoryManager(conn, embedder=FakeEmbedder())


def _fact(**kw):
    base = dict(
        memory_type="fact", tenant_id="wesa", content="A fact.", confidence=0.9,
        source_run_id="run1", subject="infrastructure:lumio", predicate="network",
        source="axel_curated",
    )
    base.update(kw)
    return MemoryCandidate(**base)


# --------------------------- schema ---------------------------

def test_schema_has_vector_and_tables(conn):
    cur = conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='memory'"
    )
    names = {r["table_name"] for r in cur.fetchall()}
    assert {"fact_memory", "episodic_memory", "trace_memory", "deletion_events"} <= names
    cur = conn.execute("SELECT 1 FROM pg_extension WHERE extname='vector'")
    assert cur.fetchone() is not None


# --------------------------- promotion: facts ---------------------------

def test_fact_trusted_is_active(mgr):
    res = mgr.remember_fact(_fact(source="axel_curated"))
    assert res.outcome == "written"
    assert res.status == "active"
    assert mgr.fact.get(res.record_id)["status"] == "active"


def test_fact_inferred_tenant_is_provisional(mgr):
    res = mgr.remember_fact(_fact(source="inferred"))
    assert res.outcome == "written"
    assert res.status == "provisional"


def test_fact_agent_scoped_is_active(mgr):
    res = mgr.remember_fact(_fact(source="inferred", agent_id="d1"))
    assert res.status == "active"


def test_fact_low_confidence_rejected(mgr):
    res = mgr.remember_fact(_fact(confidence=0.5))
    assert res.outcome == "rejected"
    assert res.reason == "low_confidence"


def test_fact_no_provenance_rejected(mgr):
    res = mgr.remember_fact(_fact(source_run_id=""))
    assert res.outcome == "rejected"
    assert res.reason == "no_provenance"


def test_fact_dedup(mgr):
    a = mgr.remember_fact(_fact(content="Lumio K3s at 192.168.1.62"))
    b = mgr.remember_fact(_fact(content="Lumio K3s at 192.168.1.62"))
    assert a.outcome == "written"
    assert b.outcome == "deduplicated"
    assert b.record_id == a.record_id


def test_fact_contradiction_supersedes(mgr):
    a = mgr.remember_fact(_fact(content="Lumio K3s at 192.168.1.62"))
    b = mgr.remember_fact(_fact(content="Lumio K3s at 192.168.1.99"))
    assert b.outcome == "superseded"
    # old revoked and pointing at the new one
    old = mgr.fact.get(a.record_id)
    assert old["status"] == "revoked"
    assert old["superseded_by"] == b.record_id
    # exactly one active fact for this subject+predicate
    cur = mgr.conn.execute(
        "SELECT count(*) AS n FROM fact_memory WHERE subject=%(s)s AND predicate=%(p)s "
        "AND status='active'",
        {"s": "infrastructure:lumio", "p": "network"},
    )
    assert cur.fetchone()["n"] == 1


# --------------------------- retrieval ---------------------------

def test_hybrid_retrieval_finds_relevant_fact(mgr):
    mgr.remember_fact(_fact(content="Lumio K3s cluster runs on Proxmox at 192.168.1.62",
                            subject="infrastructure:lumio", predicate="network"))
    mgr.remember_fact(_fact(content="Wolfmed Neon database is in eu-central-1",
                            subject="infrastructure:wolfmed", predicate="network"))
    res = retrieve_facts(mgr.conn, query_text="where is the lumio cluster",
                         embedding=FakeEmbedder().embed("where is the lumio cluster"))
    assert res.mode == "hybrid"
    assert res.rows
    assert "lumio" in res.rows[0].text.lower()


def test_lexical_fallback_when_no_embedding(mgr):
    mgr.remember_fact(_fact(content="GOOGLE_API_KEY expires 2026-09-20",
                            subject="api:google", predicate="key_lifecycle"))
    res = retrieve_facts(mgr.conn, query_text="GOOGLE_API_KEY", embedding=None)
    assert res.mode == "lexical"
    assert any("google_api_key" in r.text.lower() for r in res.rows)


def test_provisional_fact_not_retrieved(mgr):
    # inferred tenant fact -> provisional -> must not appear in retrieval
    mgr.remember_fact(_fact(content="Unconfirmed rumor about staging",
                            subject="infrastructure:staging", predicate="network",
                            source="inferred"))
    res = retrieve_facts(mgr.conn, query_text="staging rumor",
                         embedding=FakeEmbedder().embed("staging rumor"))
    assert all("rumor" not in r.text.lower() for r in res.rows)


def test_scope_before_rank_agent_isolation(mgr):
    mgr.remember_fact(_fact(content="d1 secret note", subject="agent:d1",
                            predicate="note", source="inferred", agent_id="d1"))
    # Another agent must not see d1's agent-scoped fact.
    res = retrieve_facts(mgr.conn, query_text="secret note",
                         embedding=FakeEmbedder().embed("secret note"), agent_id="r1")
    assert all("secret note" not in r.text.lower() for r in res.rows)


# --------------------------- episodes + traces ---------------------------

def test_episode_write_and_retrieve(mgr):
    mgr.record_episode(task_type="sprint",
                       title="E0 Repo Hygiene",
                       summary="Rewrote pyproject.toml and added WesaConfig singleton.",
                       outcome="completed", source_run_id="run_e0")
    from src.memory.retrieval import retrieve_episodes
    res = retrieve_episodes(mgr.conn, query_text="pyproject WesaConfig",
                            embedding=FakeEmbedder().embed("pyproject WesaConfig"))
    assert res.rows
    assert "wesaconfig" in res.rows[0].text.lower()


def test_trace_append_and_replay(conn):
    ts = TraceStore(conn)
    ts.append(run_id="t1", event_type="task_started", payload={"agent": "d1"}, turn_index=0)
    ts.append(run_id="t1", event_type="task_completed", payload={"ok": True}, turn_index=1)
    rows = ts.replay("t1")
    assert [r["event_type"] for r in rows] == ["task_started", "task_completed"]


def test_trace_rejects_bad_event(conn):
    ts = TraceStore(conn)
    with pytest.raises(ValueError):
        ts.append(run_id="t1", event_type="nonsense", payload={})


# --------------------------- GDPR ---------------------------

def test_gdpr_cascade_erase(mgr):
    mgr.remember_fact(_fact(content="user fact", user_id="jane", source="user_stated",
                            confidence=0.9))
    mgr.preference.set("wesa", "verbosity", "terse", "user_stated", user_id="jane")
    TraceStore(mgr.conn).append(run_id="r", event_type="task_started",
                                payload={}, user_id="jane")
    mgr.erase_user("jane")
    # facts blanked + revoked
    cur = mgr.conn.execute("SELECT content, status FROM fact_memory WHERE user_id='jane'")
    row = cur.fetchone()
    assert row["content"] == "[erased]" and row["status"] == "revoked"
    # preferences + traces hard-deleted
    assert mgr.conn.execute("SELECT count(*) AS n FROM preference_memory WHERE user_id='jane'").fetchone()["n"] == 0
    assert mgr.conn.execute("SELECT count(*) AS n FROM trace_memory WHERE user_id='jane'").fetchone()["n"] == 0
    # audit event written
    assert mgr.conn.execute("SELECT count(*) AS n FROM deletion_events WHERE user_id='jane'").fetchone()["n"] == 1
