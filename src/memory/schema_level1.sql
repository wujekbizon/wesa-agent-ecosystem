-- WESA Agent Memory — Level 1 schema (policy + preference).
-- Target: a DEDICATED Neon project for WESA memory, fully separate from the
-- Wolfmed project (decided 2026-06-23 — separation of concerns: independent
-- billing, roles, and backups). Connection string lives in
-- WESA_MEMORY_DATABASE_URL. No pgvector, no FTS — Level 1 has no embeddings.
-- Apply with: python scripts/apply_memory_schema.py   (or psql -f this file)
--
-- The `memory` schema namespaces the tables within that project's database.

CREATE SCHEMA IF NOT EXISTS memory;

SET search_path TO memory, public;

-- gen_random_uuid() is in core Postgres since v13; no extension needed.

-- ---------------------------------------------------------------------------
-- policy_memory — binding rules. Exact match only, never similarity.
-- Versioned: an update inserts a new version and closes the prior one with
-- effective_until. Manual-write only (Axel/Greg); never auto-promoted.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS memory.policy_memory (
    policy_id       TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL DEFAULT 'wesa',
    policy_type     TEXT NOT NULL CHECK (policy_type IN
                        ('routing', 'git', 'approval', 'autonomy', 'security')),
    policy_key      TEXT NOT NULL,
    policy_value    JSONB NOT NULL,
    version         INTEGER NOT NULL DEFAULT 1,
    effective_from  TIMESTAMPTZ NOT NULL DEFAULT now(),
    effective_until TIMESTAMPTZ,
    created_by      TEXT NOT NULL DEFAULT 'axel',
    UNIQUE (tenant_id, policy_key, version)
);

-- Active-policy lookup: one row per key where effective_until IS NULL.
CREATE INDEX IF NOT EXISTS idx_policy_lookup
    ON memory.policy_memory (tenant_id, policy_key)
    WHERE effective_until IS NULL;

-- ---------------------------------------------------------------------------
-- preference_memory — stable personalization. Exact lookup, loaded in full
-- every session. PK-upsert on (tenant, user, agent, pref_key).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS memory.preference_memory (
    pref_id     TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id   TEXT NOT NULL DEFAULT 'wesa',
    user_id     TEXT,                         -- NULL = all users / global
    agent_id    TEXT,                         -- NULL = not agent-specific
    pref_key    TEXT NOT NULL,
    pref_value  JSONB NOT NULL,
    source      TEXT NOT NULL CHECK (source IN
                    ('greg_stated', 'axel_inferred', 'axel_curated',
                     'admin_set', 'user_stated', 'inferred')),
    confidence  NUMERIC(3, 2),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Plain UNIQUE treats NULL scope columns as distinct, which would let
-- duplicate global/agent prefs through and break ON CONFLICT. COALESCE the
-- nullable scope columns into a well-defined upsert key. The store's
-- INSERT ... ON CONFLICT targets this exact expression index.
CREATE UNIQUE INDEX IF NOT EXISTS uq_pref_scope_key
    ON memory.preference_memory
       (tenant_id, COALESCE(user_id, ''), COALESCE(agent_id, ''), pref_key);

CREATE INDEX IF NOT EXISTS idx_pref_lookup
    ON memory.preference_memory (tenant_id, user_id, agent_id);
