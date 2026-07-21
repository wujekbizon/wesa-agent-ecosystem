-- WESA Agent Memory — Level 2 schema (fact + episodic + trace).
-- Target: the dedicated WESA memory Neon project (WESA_MEMORY_DATABASE_URL).
-- Apply with: python scripts/apply_memory_schema.py --file src/memory/schema_level2.sql
--
-- Adds the compounding-knowledge layer: durable facts, completed-work
-- episodes, and the append-only trace log. Needs pgvector for semantic search.
--
-- EMBEDDING DIMENSION: 768 — matches nomic-embed-text (Ollama). This MUST
-- equal EMBED_DIM in src/memory/embeddings.py. If you switch embedding models,
-- change both and re-embed from the `content` / `summary` columns (the
-- structured row is truth; the vector is a rebuildable cache).
-- (Note: the plan text said 384 — that was the Oracle demo's all-MiniLM model;
-- nomic-embed-text is 768.)

CREATE EXTENSION IF NOT EXISTS vector;

CREATE SCHEMA IF NOT EXISTS memory;
SET search_path TO memory, public;

-- ---------------------------------------------------------------------------
-- fact_memory — durable assertions with provenance. Hybrid retrieval.
-- This is where compounding advantage lives.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS memory.fact_memory (
    fact_id        TEXT PRIMARY KEY DEFAULT ('fact_' || substr(gen_random_uuid()::text, 1, 12)),
    tenant_id      TEXT NOT NULL DEFAULT 'wesa',
    user_id        TEXT,                       -- NULL = tenant-scoped
    agent_id       TEXT,                       -- NULL = shared
    subject        TEXT NOT NULL,              -- entity the fact is about
    predicate      TEXT NOT NULL,              -- kind of fact (infrastructure, model, ...)
    content        TEXT NOT NULL,              -- the fact prose (what the agent reads)
    content_hash   TEXT NOT NULL,              -- SHA-256 of normalized content (dedup)
    embedding      VECTOR(768),                -- nullable; rebuildable from content
    metadata       JSONB,
    status         TEXT NOT NULL DEFAULT 'active'
                     CHECK (status IN ('provisional', 'active', 'revoked')),
    source         TEXT,                       -- greg_stated | axel_curated | inferred | ...
    source_run_id  TEXT NOT NULL,              -- which task produced this
    source_turn_id TEXT,
    confidence     NUMERIC(3, 2) NOT NULL DEFAULT 0.8,
    superseded_by  TEXT REFERENCES memory.fact_memory(fact_id),
    expires_at     TIMESTAMPTZ,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_fact_scope
    ON memory.fact_memory (tenant_id, user_id, agent_id) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS idx_fact_status
    ON memory.fact_memory (tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_fact_dedup
    ON memory.fact_memory (content_hash, tenant_id);
CREATE INDEX IF NOT EXISTS idx_fact_subpred
    ON memory.fact_memory (tenant_id, subject, predicate) WHERE status = 'active';
-- Approximate-nearest-neighbour over the embedding (cosine).
CREATE INDEX IF NOT EXISTS idx_fact_vec
    ON memory.fact_memory USING hnsw (embedding vector_cosine_ops);
-- Full-text search over the prose.
CREATE INDEX IF NOT EXISTS idx_fact_fts
    ON memory.fact_memory USING gin (to_tsvector('english', content));

-- ---------------------------------------------------------------------------
-- episodic_memory — distilled summaries of completed work.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS memory.episodic_memory (
    episode_id   TEXT PRIMARY KEY DEFAULT ('ep_' || substr(gen_random_uuid()::text, 1, 12)),
    tenant_id    TEXT NOT NULL DEFAULT 'wesa',
    user_id      TEXT,
    task_type    TEXT NOT NULL,                -- sprint, incident, design_run, ...
    title        TEXT NOT NULL,
    summary      TEXT NOT NULL,                -- what the agent reads (distilled)
    outcome      TEXT NOT NULL,                -- completed, escalated, blocked, partial
    key_steps    JSONB,
    artifacts    JSONB,
    embedding    VECTOR(768),
    status       TEXT NOT NULL DEFAULT 'active'
                   CHECK (status IN ('provisional', 'active', 'revoked')),
    source_run_id TEXT NOT NULL,
    completed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ep_scope
    ON memory.episodic_memory (tenant_id, task_type, completed_at DESC) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS idx_ep_vec
    ON memory.episodic_memory USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_ep_fts
    ON memory.episodic_memory USING gin (to_tsvector('english', summary));

-- ---------------------------------------------------------------------------
-- trace_memory — append-only execution log. The flight recorder.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS memory.trace_memory (
    trace_id    TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    run_id      TEXT NOT NULL,                 -- maps to daemon task_id
    tenant_id   TEXT NOT NULL DEFAULT 'wesa',
    user_id     TEXT,
    agent_id    TEXT,
    turn_index  INTEGER NOT NULL DEFAULT 0,
    event_type  TEXT NOT NULL CHECK (event_type IN
                    ('task_queued', 'task_started', 'tool_call', 'tool_result',
                     'task_completed', 'task_failed', 'extraction_result',
                     'promotion_result')),
    payload     JSONB NOT NULL,
    token_cost  INTEGER,
    latency_ms  INTEGER,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_trace_run    ON memory.trace_memory (run_id, turn_index);
CREATE INDEX IF NOT EXISTS idx_trace_tenant ON memory.trace_memory (tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_trace_agent  ON memory.trace_memory (agent_id, created_at DESC);

-- ---------------------------------------------------------------------------
-- deletion_events — GDPR erasure audit trail.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS memory.deletion_events (
    deletion_id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    user_id     TEXT,
    scope       TEXT NOT NULL,
    deleted_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    reason      TEXT
);
