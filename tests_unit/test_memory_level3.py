"""Level 3 tests: token-budgeted prompt assembly + post-task capture loop.

Pure assembly tests need no DB. The loop tests run against pgserver+pgvector.
"""
from __future__ import annotations

import hashlib
import math
import pathlib
import re
from dataclasses import dataclass, field

import pytest

from src.memory.assembly import assemble_prompt, token_estimate  # noqa: E402


# ----------------- assembly (no DB) -----------------

@dataclass
class _Row:
    id: str
    text: str
    score: float
    extra: dict = field(default_factory=dict)

    @property
    def relevance(self):
        return "high" if self.score >= 0.7 else "standard"


def _fact(i, text):
    return _Row(id=f"f{i}", text=text, score=0.9,
                extra={"subject": f"s{i}", "predicate": "p"})


def test_reserved_always_included():
    p = assemble_prompt(
        policies=[{"key": "git", "value": {"x": 1}}],
        preferences=[{"key": "verbosity", "value": "terse"}],
        facts=[], episodes=[], token_budget=2000,
    )
    assert "<policies>" in p.text and "git=" in p.text
    assert "<preferences>" in p.text and "verbosity=" in p.text


def test_budget_trims_ranked_keeps_reserved():
    # include_preamble=False isolates the budget math to context content (the
    # preamble alone is larger than a tiny budget, which is fine — it's reserved).
    facts = [_fact(i, "lumio cluster fact " + str(i)) for i in range(30)]
    p = assemble_prompt(
        policies=[{"key": "git", "value": {"never_push": ["_plans/"]}}],
        preferences=[{"key": "verbosity", "value": "terse"}],
        facts=facts, episodes=[], token_budget=80, include_preamble=False,
    )
    # Reserved survived; ranked slots partially filled then trimmed.
    assert "<policies>" in p.text and "<preferences>" in p.text
    assert p.facts_kept > 0 and p.facts_dropped > 0
    assert p.tokens <= 100  # at/under budget (+ small rendering slack)


def test_budget_reserved_kept_even_when_over_budget():
    # Preamble + policies exceed a tiny budget -> reserved still included,
    # all ranked dropped. (Reserved is never dropped.)
    facts = [_fact(i, "x" * 200) for i in range(5)]
    p = assemble_prompt(
        policies=[{"key": "git", "value": {"never_push": ["_plans/"]}}],
        preferences=[{"key": "verbosity", "value": "terse"}],
        facts=facts, episodes=[], token_budget=50,
    )
    assert "<policies>" in p.text and "<preferences>" in p.text
    assert p.facts_kept == 0 and p.facts_dropped == 5


def test_high_budget_keeps_all_facts():
    facts = [_fact(i, "short fact") for i in range(5)]
    p = assemble_prompt(policies=[], preferences=[], facts=facts, episodes=[],
                        token_budget=5000)
    assert p.facts_kept == 5 and p.facts_dropped == 0


def test_token_estimate_monotonic():
    assert token_estimate("a" * 40) > token_estimate("a" * 4)


# ----------------- loop (DB) -----------------

pytest.importorskip("psycopg")
pgserver = pytest.importorskip("pgserver")

import psycopg  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402

from src.memory.loop import build_task_prompt, post_task  # noqa: E402
from src.memory.manager import WesaMemoryManager  # noqa: E402
from src.memory.records import MemoryCandidate  # noqa: E402

_MEM = pathlib.Path(__file__).resolve().parents[1] / "src" / "memory"


class FakeEmbedder:
    dim = 768

    def embed(self, text):
        v = [0.0] * 768
        for tok in re.findall(r"\w+", text.lower()):
            v[int(hashlib.md5(tok.encode()).hexdigest(), 16) % 768] += 1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]


@pytest.fixture(scope="module")
def pg_uri(tmp_path_factory):
    s = pgserver.get_server(str(tmp_path_factory.mktemp("wesa_pg_l3")))
    yield s.get_uri()
    s.cleanup()


@pytest.fixture
def mgr(pg_uri):
    c = psycopg.connect(pg_uri, row_factory=dict_row)
    c.execute("DROP SCHEMA IF EXISTS memory CASCADE")
    c.execute((_MEM / "schema_level1.sql").read_text())
    c.execute((_MEM / "schema_level2.sql").read_text())
    c.execute("SET search_path TO memory, public")
    c.commit()
    m = WesaMemoryManager(c, embedder=FakeEmbedder())
    yield m
    c.close()


def test_build_task_prompt_combines_paths(mgr):
    mgr.policy.set_policy("wesa", "git_protected_paths",
                          {"never_push": ["_plans/"]}, "git")
    mgr.preference.set("wesa", "verbosity", "terse", "greg_stated")
    mgr.remember_fact(MemoryCandidate(
        memory_type="fact", tenant_id="wesa",
        content="Lumio K3s cluster runs at 192.168.1.62", confidence=0.9,
        source_run_id="r", subject="infrastructure:lumio", predicate="network",
        source="axel_curated"))

    prompt, env = build_task_prompt(mgr, query_text="where is the lumio cluster")
    assert "<policies>" in prompt.text
    assert "<preferences>" in prompt.text
    assert "lumio" in prompt.text.lower()
    assert "verbosity" in env.preferences_applied
    assert env.facts_returned  # at least the lumio fact


def test_post_task_extracts_promotes_and_traces(mgr):
    env = post_task(
        mgr, run_id="run_xyz", agent_id="d1",
        task_text="Confirmed: Lumio is at 192.168.1.62 and D1 on qwen3.5-opus",
        result="done",
    )
    # extractor proposed at least one candidate, promoted through the gate
    assert env.promotions, "expected at least one promotion"
    # trace has the completion + one promotion_result per candidate
    events = [e["event_type"] for e in mgr.trace.replay("run_xyz")]
    assert "task_completed" in events
    assert events.count("promotion_result") == len(env.promotions)


def test_post_task_inferred_facts_are_provisional(mgr):
    env = post_task(mgr, run_id="run_prov",
                    task_text="Lumio is at 192.168.1.62", result="")
    # rule-based extractor proposes inferred facts -> provisional (tenant scope)
    statuses = {p["status"] for p in env.promotions if p["type"] == "fact"}
    assert statuses <= {"provisional", None}  # written provisional, or deduped
