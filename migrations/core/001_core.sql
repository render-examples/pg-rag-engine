CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE sources (
  id              TEXT PRIMARY KEY,
  adapter_kind    TEXT NOT NULL,
  display_name    TEXT NOT NULL,
  config          JSONB NOT NULL DEFAULT '{}',
  enabled         BOOLEAN NOT NULL DEFAULT true,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE embedding_profiles (
  id                TEXT PRIMARY KEY,
  provider          TEXT NOT NULL,
  model             TEXT NOT NULL,
  dimension         INT NOT NULL CHECK (dimension > 0),
  document_prefix   TEXT NOT NULL DEFAULT '',
  query_prefix      TEXT NOT NULL DEFAULT '',
  normalized        BOOLEAN NOT NULL DEFAULT false,
  fingerprint       TEXT NOT NULL,
  active            BOOLEAN NOT NULL DEFAULT false,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX one_active_embedding_profile
  ON embedding_profiles (active) WHERE active;
CREATE INDEX embedding_profiles_fingerprint
  ON embedding_profiles (fingerprint);

CREATE TABLE documents (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  source_id       TEXT NOT NULL REFERENCES sources(id),
  external_id     TEXT NOT NULL,
  kind            TEXT NOT NULL,
  title           TEXT,
  url             TEXT,
  occurred_at     TIMESTAMPTZ NOT NULL,
  source_updated_at TIMESTAMPTZ,
  status          TEXT NOT NULL DEFAULT 'ready'
                  CHECK (status IN ('pending', 'ready', 'failed', 'deleted')),
  metadata        JSONB NOT NULL DEFAULT '{}',
  raw             JSONB,
  content_hash    TEXT NOT NULL,
  ingested_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (source_id, external_id)
);

CREATE INDEX documents_source_time ON documents (source_id, occurred_at DESC);
CREATE INDEX documents_kind ON documents (kind);
CREATE INDEX documents_metadata ON documents USING GIN (metadata);

CREATE TABLE content_units (
  id              BIGSERIAL PRIMARY KEY,
  document_id     UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  sequence        INT NOT NULL CHECK (sequence >= 1),
  text            TEXT NOT NULL,
  author          TEXT,
  started_at_ms   INT,
  ended_at_ms     INT,
  locator         JSONB NOT NULL DEFAULT '{}',
  metadata        JSONB NOT NULL DEFAULT '{}',
  search_vector   TSVECTOR GENERATED ALWAYS AS
                  (to_tsvector('{{FULL_TEXT_LANGUAGE}}', text)) STORED,
  UNIQUE (document_id, sequence)
);

CREATE INDEX content_units_document ON content_units (document_id, sequence);
CREATE INDEX content_units_search ON content_units USING GIN (search_vector);

CREATE TABLE entities (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  source_id       TEXT NOT NULL REFERENCES sources(id),
  type            TEXT NOT NULL,
  external_id     TEXT,
  name            TEXT,
  metadata        JSONB NOT NULL DEFAULT '{}',
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX entities_source_external
  ON entities (source_id, type, external_id)
  WHERE external_id IS NOT NULL;
CREATE INDEX entities_name ON entities (lower(name));

CREATE TABLE document_entities (
  document_id     UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  entity_id       UUID NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  role            TEXT NOT NULL DEFAULT 'related',
  metadata        JSONB NOT NULL DEFAULT '{}',
  PRIMARY KEY (document_id, entity_id, role)
);

CREATE TABLE chunks (
  id                  BIGSERIAL PRIMARY KEY,
  document_id         UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  sequence            INT NOT NULL CHECK (sequence >= 1),
  start_unit_sequence INT NOT NULL,
  end_unit_sequence   INT NOT NULL,
  text                TEXT NOT NULL,
  token_estimate      INT NOT NULL,
  locator             JSONB NOT NULL DEFAULT '{}',
  content_hash        TEXT NOT NULL,
  search_vector       TSVECTOR GENERATED ALWAYS AS
                      (to_tsvector('{{FULL_TEXT_LANGUAGE}}', text)) STORED,
  embedding           vector({{EMBEDDING_DIMENSION}}),
  embedding_profile_id TEXT REFERENCES embedding_profiles(id),
  embedding_fingerprint TEXT,
  embedding_status    TEXT NOT NULL DEFAULT 'pending'
                      CHECK (embedding_status IN ('pending', 'complete', 'failed')),
  embedded_at         TIMESTAMPTZ,
  last_error          TEXT,
  UNIQUE (document_id, sequence),
  UNIQUE (document_id, content_hash)
);

CREATE INDEX chunks_document ON chunks (document_id, sequence);
CREATE INDEX chunks_search ON chunks USING GIN (search_vector);
CREATE INDEX chunks_embedding_pending ON chunks (embedding_status)
  WHERE embedding_status IN ('pending', 'failed');
CREATE INDEX chunks_embedding_hnsw ON chunks
  USING hnsw (embedding vector_cosine_ops)
  WITH (m = 16, ef_construction = 64);

CREATE TABLE sync_runs (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  source_id       TEXT NOT NULL REFERENCES sources(id),
  mode            TEXT NOT NULL CHECK (mode IN ('incremental', 'range', 'records')),
  from_datetime   TIMESTAMPTZ,
  to_datetime     TIMESTAMPTZ,
  status          TEXT NOT NULL DEFAULT 'running'
                  CHECK (status IN ('running', 'complete', 'partial', 'failed')),
  counts          JSONB NOT NULL DEFAULT '{}',
  error           TEXT,
  started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at     TIMESTAMPTZ
);

CREATE INDEX sync_runs_source_started ON sync_runs (source_id, started_at DESC);

CREATE TABLE source_checkpoints (
  source_id       TEXT PRIMARY KEY REFERENCES sources(id) ON DELETE CASCADE,
  cursor          JSONB,
  watermark_at    TIMESTAMPTZ,
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE embedding_jobs (
  document_id     UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  profile_id      TEXT NOT NULL REFERENCES embedding_profiles(id),
  status          TEXT NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending', 'partial', 'complete', 'failed')),
  chunk_count     INT NOT NULL DEFAULT 0,
  embedded_count  INT NOT NULL DEFAULT 0,
  attempts        INT NOT NULL DEFAULT 0,
  last_error      TEXT,
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (document_id, profile_id)
);

CREATE VIEW document_context AS
SELECT
  d.*,
  COALESCE(
    (
      SELECT string_agg(
        CASE WHEN u.author IS NULL THEN u.text ELSE u.author || ': ' || u.text END,
        E'\n' ORDER BY u.sequence
      )
      FROM content_units u
      WHERE u.document_id = d.id
    ),
    ''
  ) AS full_text,
  (SELECT count(*) FROM content_units u WHERE u.document_id = d.id) AS unit_count,
  (
    SELECT jsonb_agg(
      jsonb_build_object(
        'id', e.id,
        'type', e.type,
        'external_id', e.external_id,
        'name', e.name,
        'role', de.role,
        'metadata', e.metadata
      )
      ORDER BY e.type, e.name
    )
    FROM document_entities de
    JOIN entities e ON e.id = de.entity_id
    WHERE de.document_id = d.id
  ) AS entities
FROM documents d;

CREATE VIEW agent_schema_catalog AS
SELECT
  n.nspname AS schema_name,
  c.relname AS object_name,
  CASE c.relkind
    WHEN 'r' THEN 'table'
    WHEN 'v' THEN 'view'
    WHEN 'm' THEN 'materialized_view'
  END AS object_type,
  obj_description(c.oid, 'pg_class') AS object_description,
  a.attname AS column_name,
  a.attnum AS ordinal_position,
  pg_catalog.format_type(a.atttypid, a.atttypmod) AS data_type,
  NOT a.attnotnull AS is_nullable,
  col_description(c.oid, a.attnum) AS description
FROM pg_catalog.pg_class c
JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
LEFT JOIN pg_catalog.pg_attribute a
  ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
WHERE n.nspname = 'public'
  AND c.relkind IN ('r', 'v', 'm')
  AND c.relname NOT IN ('schema_migrations', 'agent_schema_catalog')
ORDER BY c.relname, a.attnum NULLS FIRST;

COMMENT ON TABLE documents IS
  'Canonical source-neutral records. Adapter-specific projections link by document_id.';
COMMENT ON TABLE content_units IS
  'Ordered source content with stable citation locators.';
COMMENT ON TABLE chunks IS
  'Citation-preserving retrieval chunks with keyword and semantic indexes.';
COMMENT ON TABLE embedding_profiles IS
  'Versioned embedding model contracts. Exactly one profile is active.';
COMMENT ON VIEW document_context IS
  'Analysis-ready canonical document with joined text and entities.';
