"""Hybrid retrieval for facts and episodes (Postgres + pgvector).

Two signals, fused:
  - vector: pgvector cosine distance (embedding <=> query), semantic recall
  - lexical: Postgres full-text search (to_tsvector @@ plainto_tsquery), term recall

Fusion: 0.4*vec + 0.6*lex when both fire (lexical weighted higher — when both
signals agree the row is genuinely relevant); single-signal otherwise. Drop
anything below a fused score of 0.4.

Cascade fallback: hybrid -> vector -> lexical -> like. Each tier is lower
fidelity but keeps retrieval available. If no embedding is supplied (Ollama
down), we start at the lexical tier automatically — the lexical path is
load-bearing, not decorative.

INVARIANT: scope filters (tenant/user/agent/status) run in the WHERE clause,
BEFORE ranking and LIMIT — never as a post-filter. Cross-scope data is never in
the ranking neighbourhood.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import psycopg

_FACT_SCOPE = (
    "tenant_id = %(tid)s AND status = 'active' AND superseded_by IS NULL "
    "AND (user_id IS NULL OR user_id = %(uid)s) "
    "AND (agent_id IS NULL OR agent_id = %(aid)s) "
    "AND (expires_at IS NULL OR expires_at > now())"
)
_EP_SCOPE = "tenant_id = %(tid)s AND status = 'active'"


@dataclass(frozen=True)
class _Cfg:
    table: str
    id_col: str
    text_col: str
    extra_cols: tuple[str, ...]
    scope_sql: str


_FACT_CFG = _Cfg("fact_memory", "fact_id", "content",
                 ("subject", "predicate", "confidence"), _FACT_SCOPE)
_EP_CFG = _Cfg("episodic_memory", "episode_id", "summary",
               ("task_type", "title", "outcome"), _EP_SCOPE)


def relevance_tier(score: float | None) -> str:
    """Map a fused score to a tier the agent reads directly."""
    if score is None:
        return "standard"
    if score >= 0.7:
        return "high"
    if score >= 0.5:
        return "standard"
    return "low"


@dataclass
class RetrievedRow:
    kind: str                       # "fact" | "episodic"
    id: str
    text: str
    score: float | None
    extra: dict = field(default_factory=dict)

    @property
    def relevance(self) -> str:
        return relevance_tier(self.score)


@dataclass
class RetrievalResult:
    rows: list[RetrievedRow]
    mode: str                       # tier that actually served


def _scope_params(tenant_id, user_id, agent_id):
    return {"tid": tenant_id, "uid": user_id, "aid": agent_id}


def _vector_hits(conn, cfg, qvec_literal, params, limit=20):
    cols = ", ".join(cfg.extra_cols)
    sql = (
        f"SELECT {cfg.id_col} AS id, {cfg.text_col} AS text, {cols}, "
        f"(embedding <=> %(qvec)s::vector) AS dist "
        f"FROM {cfg.table} "
        f"WHERE {cfg.scope_sql} AND embedding IS NOT NULL "
        f"ORDER BY embedding <=> %(qvec)s::vector "
        f"LIMIT {limit}"
    )
    cur = conn.execute(sql, {**params, "qvec": qvec_literal})
    return [dict(r) for r in cur.fetchall()]


def _lexical_hits(conn, cfg, query_text, params, limit=20):
    cols = ", ".join(cfg.extra_cols)
    sql = (
        f"SELECT {cfg.id_col} AS id, {cfg.text_col} AS text, {cols}, "
        f"ts_rank(to_tsvector('english', {cfg.text_col}), "
        f"        plainto_tsquery('english', %(q)s)) AS lex "
        f"FROM {cfg.table} "
        f"WHERE {cfg.scope_sql} "
        f"  AND to_tsvector('english', {cfg.text_col}) @@ plainto_tsquery('english', %(q)s) "
        f"ORDER BY lex DESC "
        f"LIMIT {limit}"
    )
    cur = conn.execute(sql, {**params, "q": query_text})
    return [dict(r) for r in cur.fetchall()]


def _like_hits(conn, cfg, query_text, params, limit=5):
    cols = ", ".join(cfg.extra_cols)
    # crude substring fallback: longest word as the needle
    words = [w for w in query_text.split() if len(w) >= 3]
    needle = max(words, key=len) if words else query_text
    sql = (
        f"SELECT {cfg.id_col} AS id, {cfg.text_col} AS text, {cols} "
        f"FROM {cfg.table} "
        f"WHERE {cfg.scope_sql} AND {cfg.text_col} ILIKE %(needle)s "
        f"ORDER BY created_at DESC NULLS LAST "
        f"LIMIT {limit}"
    ) if cfg.table == "fact_memory" else (
        f"SELECT {cfg.id_col} AS id, {cfg.text_col} AS text, {cols} "
        f"FROM {cfg.table} "
        f"WHERE {cfg.scope_sql} AND {cfg.text_col} ILIKE %(needle)s "
        f"ORDER BY completed_at DESC NULLS LAST "
        f"LIMIT {limit}"
    )
    cur = conn.execute(sql, {**params, "needle": f"%{needle}%"})
    return [dict(r) for r in cur.fetchall()]


def _extract(row: dict, cfg: _Cfg) -> dict:
    return {k: row[k] for k in cfg.extra_cols if k in row}


def _fuse(vec_hits, lex_hits, cfg, kind, top_k, floor=0.4):
    max_lex = max((h["lex"] for h in lex_hits), default=0) or None
    by_id: dict[str, dict] = {}
    for h in vec_hits:
        by_id.setdefault(h["id"], {"row": h, "v": None, "l": None})
        by_id[h["id"]]["v"] = 1.0 / (1.0 + float(h["dist"]))
    for h in lex_hits:
        e = by_id.setdefault(h["id"], {"row": h, "v": None, "l": None})
        e["l"] = (float(h["lex"]) / max_lex) if max_lex else 0.0

    out: list[RetrievedRow] = []
    for _id, e in by_id.items():
        v, l = e["v"], e["l"]
        if v is not None and l is not None:
            score = 0.4 * v + 0.6 * l
        elif v is not None:
            score = v
        else:
            score = l
        if score is None or score < floor:
            continue
        out.append(RetrievedRow(kind=kind, id=_id, text=e["row"]["text"],
                                score=score, extra=_extract(e["row"], cfg)))
    out.sort(key=lambda r: r.score, reverse=True)
    return out[:top_k]


def _search(conn, cfg, kind, *, query_text, qvec_literal, tenant_id,
            user_id, agent_id, top_k, mode):
    """Run one tier, cascading down on failure or missing inputs."""
    params = _scope_params(tenant_id, user_id, agent_id)
    cascade = ["hybrid", "vector", "lexical", "like"]
    # No embedding -> can't do vector tiers; jump to lexical.
    start = mode
    if qvec_literal is None and mode in ("hybrid", "vector"):
        start = "lexical"
    for tier in cascade[cascade.index(start):]:
        try:
            if tier == "hybrid":
                vec = _vector_hits(conn, cfg, qvec_literal, params)
                lex = _lexical_hits(conn, cfg, query_text, params)
                rows = _fuse(vec, lex, cfg, kind, top_k)
                return RetrievalResult(rows=rows, mode="hybrid")
            if tier == "vector":
                vec = _vector_hits(conn, cfg, qvec_literal, params)
                rows = _fuse(vec, [], cfg, kind, top_k)
                return RetrievalResult(rows=rows, mode="vector")
            if tier == "lexical":
                lex = _lexical_hits(conn, cfg, query_text, params)
                rows = _fuse([], lex, cfg, kind, top_k)
                return RetrievalResult(rows=rows, mode="lexical")
            # like
            hits = _like_hits(conn, cfg, query_text, params, limit=top_k)
            rows = [RetrievedRow(kind=kind, id=h["id"], text=h["text"],
                                 score=None, extra=_extract(h, cfg)) for h in hits]
            return RetrievalResult(rows=rows, mode="like")
        except psycopg.Error:
            conn.rollback()
            continue
    return RetrievalResult(rows=[], mode="exhausted")


def retrieve_facts(conn, *, query_text, embedding=None, tenant_id="wesa",
                   user_id=None, agent_id=None, top_k=5, mode="hybrid"):
    from src.memory.embeddings import to_pgvector_literal
    qvec = to_pgvector_literal(embedding) if embedding is not None else None
    return _search(conn, _FACT_CFG, "fact", query_text=query_text,
                   qvec_literal=qvec, tenant_id=tenant_id, user_id=user_id,
                   agent_id=agent_id, top_k=top_k, mode=mode)


def retrieve_episodes(conn, *, query_text, embedding=None, tenant_id="wesa",
                      top_k=3, mode="hybrid"):
    from src.memory.embeddings import to_pgvector_literal
    qvec = to_pgvector_literal(embedding) if embedding is not None else None
    # Episodes are tenant-scoped; user/agent not used in the scope predicate.
    return _search(conn, _EP_CFG, "episodic", query_text=query_text,
                   qvec_literal=qvec, tenant_id=tenant_id, user_id=None,
                   agent_id=None, top_k=top_k, mode=mode)
