"""WESA agent memory system.

Typed, scoped, governed memory for the WESA agent ecosystem. Based on
"From RAG to Memory Systems" (Jeremy Daly, Oracle, 2026) and the
oracle-ai-developer-hub reference implementation, re-targeted from Oracle
DB to Neon Postgres (pgvector + native FTS) and from async to WESA's
synchronous psycopg3 style.

Build order (see _plans/2026-06-23_wesa_agent-memory-system-plan.md):
  Level 1 — policy + preference (this increment). No embeddings.
  Level 2 — fact + episodic + trace, hybrid retrieval, promotion gate.
  Level 3 — full prompt assembly from memory.
"""
from __future__ import annotations

from src.memory.hashing import content_hash, normalize
from src.memory.records import (
    EpisodeRecord,
    FactRecord,
    MemoryCandidate,
    PromotionResult,
)

__all__ = [
    "FactRecord",
    "EpisodeRecord",
    "MemoryCandidate",
    "PromotionResult",
    "content_hash",
    "normalize",
]
