"""Promotion gate — the write boundary for durable memory.

Nothing reaches a memory table without passing through here. The gate:
  - preferences (Level 1): confidence floor 0.5, pref_key required, upsert
  - facts (Level 2): confidence floor 0.7, provenance required, dedup by
    content_hash+scope, contradiction -> supersession, trusted-author status
  - policies: never auto-promoted (manual write only)

Two rules that do real work:
  1. Status is computed from scope+type here, never accepted from the caller.
  2. Trusted authors (greg_stated / axel_curated / admin_set) write `active`
     facts; everything else inferred enters `provisional` (stored but invisible
     to semantic retrieval until confirmed). WESA divergence from the Oracle
     demo (plan §13.3) — without it, Axel-authored facts would be hidden.
"""
from __future__ import annotations

import psycopg

from src.memory.embeddings import EmbeddingUnavailable
from src.memory.hashing import content_hash
from src.memory.records import MemoryCandidate, PromotionResult
from src.memory.stores.fact_store import FactStore
from src.memory.stores.preference_store import PreferenceStore

_PREF_CONFIDENCE_FLOOR = 0.5
_FACT_CONFIDENCE_FLOOR = 0.7
_TRUSTED_OR_STATED = frozenset(
    {"greg_stated", "axel_curated", "admin_set", "user_stated"}
)


def resolve_scope(candidate: MemoryCandidate) -> str:
    """Most-specific scope wins: agent > user > tenant."""
    if candidate.agent_id:
        return "agent"
    if candidate.user_id:
        return "user"
    return "tenant"


def compute_status(candidate: MemoryCandidate, scope: str) -> str:
    """Resulting record status — computed here, never from the caller."""
    if candidate.memory_type in ("preference", "episodic"):
        return "active"
    if scope == "agent":
        return "active"
    if candidate.is_trusted_author:
        return "active"
    return "provisional"


class PromotionGate:
    """promote(candidate) -> PromotionResult.

    fact_store and embedder are optional so Level 1 callers (preferences only)
    keep working with PromotionGate(conn, preference_store).
    """

    def __init__(
        self,
        conn: psycopg.Connection,
        preference_store: PreferenceStore,
        fact_store: FactStore | None = None,
        embedder=None,
    ):
        self.conn = conn
        self.pref = preference_store
        self.fact = fact_store
        self.embedder = embedder

    def promote(self, candidate: MemoryCandidate) -> PromotionResult:
        try:
            if candidate.memory_type == "preference":
                return self._promote_preference(candidate)
            if candidate.memory_type == "fact":
                return self._promote_fact(candidate)
            self.conn.rollback()
            return PromotionResult.rejected(
                f"unsupported_type:{candidate.memory_type}",
                confidence=candidate.confidence,
            )
        except Exception:
            self.conn.rollback()
            raise

    # ----------------------------- preferences -----------------------------

    def _promote_preference(self, candidate: MemoryCandidate) -> PromotionResult:
        if not candidate.pref_key:
            self.conn.rollback()
            return PromotionResult.rejected("no_pref_key", confidence=candidate.confidence)
        stated = candidate.source in _TRUSTED_OR_STATED
        if not stated and candidate.confidence is not None:
            if candidate.confidence < _PREF_CONFIDENCE_FLOOR:
                self.conn.rollback()
                return PromotionResult.rejected("low_confidence", confidence=candidate.confidence)
        self.pref.set(
            tenant_id=candidate.tenant_id, user_id=candidate.user_id,
            agent_id=candidate.agent_id, pref_key=candidate.pref_key,
            pref_value=candidate.pref_value, source=candidate.source or "inferred",
            confidence=candidate.confidence, commit=False,
        )
        self.conn.commit()
        return PromotionResult.written(candidate.pref_key, confidence=candidate.confidence)

    # ----------------------------- facts -----------------------------

    def _embed(self, text: str) -> list[float] | None:
        """Embed if an embedder is configured; degrade to None (lexical-only
        retrieval) if it's missing or Ollama is unavailable."""
        if self.embedder is None:
            return None
        try:
            return self.embedder.embed(text)
        except EmbeddingUnavailable:
            return None

    def _promote_fact(self, candidate: MemoryCandidate) -> PromotionResult:
        if self.fact is None:
            self.conn.rollback()
            return PromotionResult.rejected("no_fact_store", confidence=candidate.confidence)
        if candidate.confidence < _FACT_CONFIDENCE_FLOOR:
            self.conn.rollback()
            return PromotionResult.rejected("low_confidence", confidence=candidate.confidence)
        if not candidate.source_run_id:
            self.conn.rollback()
            return PromotionResult.rejected("no_provenance", confidence=candidate.confidence)

        chash = content_hash(candidate.content)

        # Dedup: same content, same scope -> already known.
        existing = self.fact.find_dedup(
            content_hash=chash, tenant_id=candidate.tenant_id,
            user_id=candidate.user_id, agent_id=candidate.agent_id,
        )
        if existing:
            self.conn.rollback()
            return PromotionResult.deduplicated(existing, confidence=candidate.confidence)

        # Contradiction: same (subject, predicate), different content -> supersede.
        if candidate.subject and candidate.predicate:
            contra = self.fact.find_contradiction(
                tenant_id=candidate.tenant_id, subject=candidate.subject,
                predicate=candidate.predicate, content_hash=chash,
                user_id=candidate.user_id,
            )
            if contra:
                return self._supersede(contra["fact_id"], candidate, chash)

        scope = resolve_scope(candidate)
        status = compute_status(candidate, scope)
        embedding = self._embed(candidate.content)
        fid = self.fact.write(
            tenant_id=candidate.tenant_id, subject=candidate.subject,
            predicate=candidate.predicate, content=candidate.content,
            content_hash=chash, source_run_id=candidate.source_run_id,
            confidence=candidate.confidence, status=status,
            user_id=candidate.user_id, agent_id=candidate.agent_id,
            source_turn_id=candidate.source_turn_id, source=candidate.source,
            embedding=embedding, commit=False,
        )
        self.conn.commit()
        return PromotionResult.written(fid, status=status, confidence=candidate.confidence)

    def _supersede(self, old_id: str, candidate: MemoryCandidate, chash: str) -> PromotionResult:
        """Write the new fact and revoke the old one, atomically.

        A superseding fact is written `active`, not provisional: it only reaches
        here as a confirmed contradiction of an existing active fact (the
        extractor never proposes supersession on a hedged statement). Writing it
        provisional would revoke a good fact and replace it with one invisible to
        retrieval."""
        status = "active"
        embedding = self._embed(candidate.content)
        new_id = self.fact.write(
            tenant_id=candidate.tenant_id, subject=candidate.subject,
            predicate=candidate.predicate, content=candidate.content,
            content_hash=chash, source_run_id=candidate.source_run_id,
            confidence=candidate.confidence, status=status,
            user_id=candidate.user_id, agent_id=candidate.agent_id,
            source_turn_id=candidate.source_turn_id, source=candidate.source,
            embedding=embedding, commit=False,
        )
        self.fact.revoke(old_id, superseded_by=new_id, commit=False)
        self.conn.commit()
        return PromotionResult.superseded(new_id, old_id, confidence=candidate.confidence)
