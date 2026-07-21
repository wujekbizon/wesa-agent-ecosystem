"""WesaMemoryManager — orchestrator across all memory types.

Level 1: session_startup (Path A), preference promotion.
Level 2: task_context (Path B — hybrid retrieval of facts + episodes),
         fact/episode promotion, trace logging, GDPR cascade erase.

The manager owns the embedder so the one Ollama round-trip per query lives in
one place. If the embedder is absent or Ollama is down, retrieval and promotion
degrade to lexical/no-embedding rather than failing.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import psycopg

from src.memory.embeddings import EmbeddingUnavailable
from src.memory.promotion import PromotionGate
from src.memory.records import MemoryCandidate, PromotionResult
from src.memory.retrieval import retrieve_episodes, retrieve_facts
from src.memory.stores.episodic_store import EpisodicStore
from src.memory.stores.fact_store import FactStore
from src.memory.stores.policy_store import PolicyStore
from src.memory.stores.preference_store import PreferenceStore
from src.memory.stores.trace_store import TraceStore

_DEFAULT_TENANT = "wesa"


@dataclass
class PromptContext:
    """Assembled context for a session (Path A) and/or a task (Path B)."""

    policies: list[dict] = field(default_factory=list)
    preferences: list[dict] = field(default_factory=list)
    facts: list = field(default_factory=list)        # list[RetrievedRow]
    episodes: list = field(default_factory=list)     # list[RetrievedRow]
    retrieval_mode: str = "none"

    def to_prompt_text(self) -> str:
        parts: list[str] = ["<context>"]
        if self.policies:
            parts.append("  <policies>")
            for p in self.policies:
                parts.append(f"    {p['key']}={p['value']}")
            parts.append("  </policies>")
        if self.preferences:
            parts.append("  <preferences>")
            for p in self.preferences:
                parts.append(f"    {p['key']}={p['value']}")
            parts.append("  </preferences>")
        if self.facts:
            parts.append("  <facts>")
            for f in self.facts:
                subj = f.extra.get("subject", "")
                pred = f.extra.get("predicate", "")
                parts.append(f"    [{f.relevance}] subject={subj} predicate={pred} :: {f.text}")
            parts.append("  </facts>")
        if self.episodes:
            parts.append("  <episodes>")
            for e in self.episodes:
                tt = e.extra.get("task_type", "")
                title = e.extra.get("title", "")
                parts.append(f"    [{e.relevance}] [{tt}] {title}: {e.text}")
            parts.append("  </episodes>")
        parts.append("</context>")
        return "\n".join(parts)


class WesaMemoryManager:
    def __init__(self, conn: psycopg.Connection, tenant_id: str = _DEFAULT_TENANT,
                 embedder=None):
        self.conn = conn
        self.tenant_id = tenant_id
        self.embedder = embedder
        self.policy = PolicyStore(conn)
        self.preference = PreferenceStore(conn)
        self.fact = FactStore(conn)
        self.episodic = EpisodicStore(conn)
        self.trace = TraceStore(conn)
        self.gate = PromotionGate(conn, self.preference, self.fact, embedder)

    # --------------------------- Path A ---------------------------

    def session_startup(self, user_id: str | None = "greg",
                        agent_id: str | None = None) -> PromptContext:
        """Load all active policies + applicable preferences. Exhaustive."""
        policies = self.policy.list_for_tenant(self.tenant_id)
        prefs = self.preference.list_for_scope(self.tenant_id, user_id=user_id, agent_id=agent_id)
        return PromptContext(policies=policies, preferences=prefs)

    # --------------------------- Path B ---------------------------

    def _embed_query(self, text: str) -> list[float] | None:
        if self.embedder is None:
            return None
        try:
            return self.embedder.embed(text)
        except EmbeddingUnavailable:
            return None

    def task_context(self, query_text: str, agent_id: str | None = None,
                     task_type: str | None = None, top_k_facts: int = 5,
                     top_k_episodes: int = 3) -> PromptContext:
        """Retrieve facts + episodes relevant to a task (hybrid, scope-filtered)."""
        qvec = self._embed_query(query_text)
        facts = retrieve_facts(
            self.conn, query_text=query_text, embedding=qvec,
            tenant_id=self.tenant_id, agent_id=agent_id, top_k=top_k_facts,
        )
        episodes = retrieve_episodes(
            self.conn, query_text=query_text, embedding=qvec,
            tenant_id=self.tenant_id, top_k=top_k_episodes,
        )
        mode = facts.mode if facts.rows or facts.mode != "exhausted" else episodes.mode
        return PromptContext(facts=facts.rows, episodes=episodes.rows, retrieval_mode=mode)

    # --------------------------- writes ---------------------------

    def remember_preference(self, candidate: MemoryCandidate) -> PromotionResult:
        return self.gate.promote(candidate)

    def remember_fact(self, candidate: MemoryCandidate) -> PromotionResult:
        return self.gate.promote(candidate)

    def record_episode(self, *, task_type: str, title: str, summary: str,
                       outcome: str, source_run_id: str,
                       key_steps: list[str] | None = None,
                       artifacts: dict | None = None) -> str:
        """Episodes promote on task completion. Embedded if possible."""
        embedding = None
        if self.embedder is not None:
            try:
                embedding = self.embedder.embed(summary)
            except EmbeddingUnavailable:
                embedding = None
        return self.episodic.write(
            tenant_id=self.tenant_id, task_type=task_type, title=title,
            summary=summary, outcome=outcome, source_run_id=source_run_id,
            key_steps=key_steps, artifacts=artifacts, embedding=embedding,
        )

    # --------------------------- GDPR ---------------------------

    def erase_user(self, user_id: str, reason: str = "gdpr_erasure") -> None:
        """Atomic cascade erase of a user's memory across all tables."""
        self.conn.execute(
            "UPDATE fact_memory SET content = '[erased]', content_hash = 'erased', "
            "embedding = NULL, status = 'revoked' "
            "WHERE user_id = %(u)s AND tenant_id = %(t)s",
            {"u": user_id, "t": self.tenant_id},
        )
        self.conn.execute(
            "UPDATE episodic_memory SET summary = '[erased]', embedding = NULL, "
            "status = 'revoked' WHERE user_id = %(u)s AND tenant_id = %(t)s",
            {"u": user_id, "t": self.tenant_id},
        )
        self.conn.execute(
            "DELETE FROM preference_memory WHERE user_id = %(u)s AND tenant_id = %(t)s",
            {"u": user_id, "t": self.tenant_id},
        )
        self.conn.execute(
            "DELETE FROM trace_memory WHERE user_id = %(u)s AND tenant_id = %(t)s",
            {"u": user_id, "t": self.tenant_id},
        )
        self.conn.execute(
            "INSERT INTO deletion_events (user_id, scope, reason) VALUES (%(u)s, 'all', %(r)s)",
            {"u": user_id, "r": reason},
        )
        self.conn.commit()
