"""Level 1 memory tests: schema, stores, promotion gate, session startup.

Runs against a real Postgres via the `pgserver` userspace package (no root
needed). Skips cleanly if pgserver isn't installed, so the rest of the WESA
suite is unaffected in environments without it.
"""
from __future__ import annotations

import pathlib

import pytest

pytest.importorskip("psycopg")
pgserver = pytest.importorskip("pgserver")

import psycopg  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402

from src.memory.manager import WesaMemoryManager  # noqa: E402
from src.memory.promotion import PromotionGate, compute_status, resolve_scope  # noqa: E402
from src.memory.records import MemoryCandidate  # noqa: E402
from src.memory.stores.policy_store import PolicyStore  # noqa: E402
from src.memory.stores.preference_store import PreferenceStore  # noqa: E402

_SCHEMA = (
    pathlib.Path(__file__).resolve().parents[1]
    / "src" / "memory" / "schema_level1.sql"
)


@pytest.fixture(scope="module")
def pg_uri(tmp_path_factory):
    data_dir = tmp_path_factory.mktemp("wesa_pg")
    server = pgserver.get_server(str(data_dir))
    yield server.get_uri()
    server.cleanup()


@pytest.fixture
def conn(pg_uri):
    """Fresh schema per test for isolation."""
    c = psycopg.connect(pg_uri, row_factory=dict_row)
    c.execute("DROP SCHEMA IF EXISTS memory CASCADE")
    c.execute(_SCHEMA.read_text(encoding="utf-8"))
    c.execute("SET search_path TO memory, public")
    c.commit()
    yield c
    c.close()


# --------------------------- schema ---------------------------

def test_schema_creates_tables(conn):
    cur = conn.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'memory' ORDER BY table_name"
    )
    names = {r["table_name"] for r in cur.fetchall()}
    assert {"policy_memory", "preference_memory"} <= names


# --------------------------- policy store ---------------------------

def test_policy_set_and_get(conn):
    store = PolicyStore(conn)
    store.set_policy(
        tenant_id="wesa",
        policy_key="git_protected_paths",
        policy_value={"never_push": ["_plans/", "operations/"]},
        policy_type="git",
    )
    got = store.get("wesa", "git_protected_paths")
    assert got is not None
    assert got["value"]["never_push"] == ["_plans/", "operations/"]
    assert got["version"] == 1
    assert got["type"] == "git"


def test_policy_versioning_supersede(conn):
    store = PolicyStore(conn)
    store.set_policy("wesa", "spending_gate", {"requires_greg_approval": True}, "approval")
    # New version: close the old, write v2.
    store.supersede("wesa", "spending_gate")
    store.set_policy(
        "wesa", "spending_gate", {"requires_greg_approval": True, "examples": ["domains"]},
        "approval", version=2,
    )
    got = store.get("wesa", "spending_gate")
    assert got["version"] == 2
    assert "examples" in got["value"]
    # Only one active row.
    active = store.list_for_tenant("wesa")
    keys = [p["key"] for p in active]
    assert keys.count("spending_gate") == 1


def test_policy_list_only_active(conn):
    store = PolicyStore(conn)
    store.set_policy("wesa", "a", {"x": 1}, "routing")
    store.set_policy("wesa", "b", {"y": 2}, "routing")
    store.supersede("wesa", "b")  # b now inactive, no replacement
    active = {p["key"] for p in store.list_for_tenant("wesa")}
    assert "a" in active
    assert "b" not in active


# --------------------------- preference store ---------------------------

def test_preference_upsert_global(conn):
    store = PreferenceStore(conn)
    store.set("wesa", "verbosity", "terse", "greg_stated")
    store.set("wesa", "verbosity", "verbose", "greg_stated")  # upsert, not dup
    got = store.get("wesa", "verbosity")
    assert got["value"] == "verbose"
    rows = store.list_for_scope("wesa")
    assert sum(1 for r in rows if r["key"] == "verbosity") == 1


def test_preference_scope_isolation(conn):
    store = PreferenceStore(conn)
    store.set("wesa", "lang", "en", "greg_stated")  # global
    store.set("wesa", "lang", "pl", "greg_stated", user_id="greg")  # user-scoped
    # Global lookup and user lookup are distinct rows.
    assert store.get("wesa", "lang")["value"] == "en"
    assert store.get("wesa", "lang", user_id="greg")["value"] == "pl"


def test_preference_agent_scope(conn):
    store = PreferenceStore(conn)
    store.set("wesa", "post_check", {"grep": ":="}, "admin_set", agent_id="gemma4")
    rows = store.list_for_scope("wesa", agent_id="gemma4")
    assert any(r["key"] == "post_check" for r in rows)
    # A different agent does not see gemma4's pref.
    rows_other = store.list_for_scope("wesa", agent_id="d1")
    assert not any(r["key"] == "post_check" for r in rows_other)


# --------------------------- promotion gate ---------------------------

def _pref_candidate(**kw):
    base = dict(
        memory_type="preference", tenant_id="wesa", content="", confidence=1.0,
        source_run_id="run_test", pref_key="verbosity", pref_value="terse",
        source="greg_stated",
    )
    base.update(kw)
    return MemoryCandidate(**base)


def test_gate_writes_stated_preference(conn):
    gate = PromotionGate(conn, PreferenceStore(conn))
    res = gate.promote(_pref_candidate())
    assert res.outcome == "written"
    assert PreferenceStore(conn).get("wesa", "verbosity")["value"] == "terse"


def test_gate_rejects_missing_pref_key(conn):
    gate = PromotionGate(conn, PreferenceStore(conn))
    res = gate.promote(_pref_candidate(pref_key=None))
    assert res.outcome == "rejected"
    assert res.reason == "no_pref_key"


def test_gate_rejects_low_confidence_inferred(conn):
    gate = PromotionGate(conn, PreferenceStore(conn))
    res = gate.promote(_pref_candidate(source="inferred", confidence=0.3))
    assert res.outcome == "rejected"
    assert res.reason == "low_confidence"


def test_gate_stated_skips_confidence_floor(conn):
    gate = PromotionGate(conn, PreferenceStore(conn))
    # Low confidence but user_stated -> still written.
    res = gate.promote(_pref_candidate(source="user_stated", confidence=0.1))
    assert res.outcome == "written"


def test_gate_rejects_non_preference_at_level1(conn):
    gate = PromotionGate(conn, PreferenceStore(conn))
    fact = MemoryCandidate(
        memory_type="fact", tenant_id="wesa", content="x", confidence=0.9,
        source_run_id="r", subject="s", predicate="p",
    )
    res = gate.promote(fact)
    assert res.outcome == "rejected"
    assert res.reason.startswith("unsupported_type_level1")


# --------------------------- status/scope logic ---------------------------

def test_compute_status_trusted_fact_active():
    c = MemoryCandidate(
        memory_type="fact", tenant_id="wesa", content="x", confidence=0.9,
        source_run_id="r", subject="s", predicate="p", source="axel_curated",
    )
    assert compute_status(c, resolve_scope(c)) == "active"


def test_compute_status_inferred_tenant_fact_provisional():
    c = MemoryCandidate(
        memory_type="fact", tenant_id="wesa", content="x", confidence=0.9,
        source_run_id="r", subject="s", predicate="p", source="inferred",
    )
    assert compute_status(c, resolve_scope(c)) == "provisional"


def test_compute_status_agent_fact_active():
    c = MemoryCandidate(
        memory_type="fact", tenant_id="wesa", content="x", confidence=0.9,
        source_run_id="r", subject="s", predicate="p", source="inferred",
        agent_id="d1",
    )
    assert compute_status(c, resolve_scope(c)) == "active"


# --------------------------- manager / session startup ---------------------------

def test_session_startup_loads_policies_and_prefs(conn):
    mgr = WesaMemoryManager(conn)
    mgr.policy.set_policy("wesa", "d1_task_sizing", {"max_files_per_task": 1}, "routing")
    mgr.preference.set("wesa", "verbosity", "terse", "greg_stated")
    mgr.preference.set("wesa", "language", "en", "greg_stated", user_id="greg")

    ctx = mgr.session_startup(user_id="greg")
    pol_keys = {p["key"] for p in ctx.policies}
    pref_keys = {p["key"] for p in ctx.preferences}
    assert "d1_task_sizing" in pol_keys
    assert {"verbosity", "language"} <= pref_keys
    text = ctx.to_prompt_text()
    assert "<policies>" in text and "<preferences>" in text


def test_manager_remember_preference(conn):
    mgr = WesaMemoryManager(conn)
    res = mgr.remember_preference(_pref_candidate(pref_value="brief"))
    assert res.outcome == "written"
    assert mgr.preference.get("wesa", "verbosity")["value"] == "brief"
