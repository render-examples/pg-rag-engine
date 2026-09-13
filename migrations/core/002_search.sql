CREATE OR REPLACE FUNCTION search_chunks_keyword(
  p_query_text       TEXT,
  p_match_count      INT DEFAULT 20,
  p_source_id        TEXT DEFAULT NULL,
  p_kind             TEXT DEFAULT NULL,
  p_occurred_since   TIMESTAMPTZ DEFAULT NULL,
  p_occurred_until   TIMESTAMPTZ DEFAULT NULL,
  p_entity            TEXT DEFAULT NULL
)
RETURNS TABLE (
  chunk_id BIGINT,
  document_id UUID,
  source_id TEXT,
  external_id TEXT,
  kind TEXT,
  title TEXT,
  url TEXT,
  occurred_at TIMESTAMPTZ,
  chunk_text TEXT,
  locator JSONB,
  start_unit_sequence INT,
  end_unit_sequence INT,
  keyword_rank FLOAT
)
LANGUAGE sql STABLE AS $$
  WITH query AS (
    SELECT websearch_to_tsquery('{{FULL_TEXT_LANGUAGE}}', p_query_text) AS value
  )
  SELECT
    c.id,
    d.id,
    d.source_id,
    d.external_id,
    d.kind,
    d.title,
    d.url,
    d.occurred_at,
    c.text,
    c.locator,
    c.start_unit_sequence,
    c.end_unit_sequence,
    ts_rank_cd(c.search_vector, query.value)::FLOAT AS keyword_rank
  FROM chunks c
  JOIN documents d ON d.id = c.document_id
  CROSS JOIN query
  WHERE c.search_vector @@ query.value
    AND d.status = 'ready'
    AND (p_source_id IS NULL OR d.source_id = p_source_id)
    AND (p_kind IS NULL OR d.kind = p_kind)
    AND (p_occurred_since IS NULL OR d.occurred_at >= p_occurred_since)
    AND (p_occurred_until IS NULL OR d.occurred_at < p_occurred_until)
    AND (
      p_entity IS NULL OR EXISTS (
        SELECT 1
        FROM document_entities de
        JOIN entities e ON e.id = de.entity_id
        WHERE de.document_id = d.id
          AND (
            lower(e.name) = lower(p_entity)
            OR lower(e.external_id) = lower(p_entity)
          )
      )
    )
  ORDER BY keyword_rank DESC, d.occurred_at DESC
  LIMIT LEAST(GREATEST(p_match_count, 1), 100);
$$;

CREATE OR REPLACE FUNCTION search_chunks_semantic(
  p_query_embedding  vector({{EMBEDDING_DIMENSION}}),
  p_match_count      INT DEFAULT 20,
  p_min_similarity   FLOAT DEFAULT 0.5,
  p_source_id        TEXT DEFAULT NULL,
  p_kind             TEXT DEFAULT NULL,
  p_occurred_since   TIMESTAMPTZ DEFAULT NULL,
  p_occurred_until   TIMESTAMPTZ DEFAULT NULL,
  p_entity            TEXT DEFAULT NULL
)
RETURNS TABLE (
  chunk_id BIGINT,
  document_id UUID,
  source_id TEXT,
  external_id TEXT,
  kind TEXT,
  title TEXT,
  url TEXT,
  occurred_at TIMESTAMPTZ,
  chunk_text TEXT,
  locator JSONB,
  start_unit_sequence INT,
  end_unit_sequence INT,
  similarity FLOAT
)
LANGUAGE sql STABLE AS $$
  SELECT
    c.id,
    d.id,
    d.source_id,
    d.external_id,
    d.kind,
    d.title,
    d.url,
    d.occurred_at,
    c.text,
    c.locator,
    c.start_unit_sequence,
    c.end_unit_sequence,
    (1 - (c.embedding <=> p_query_embedding))::FLOAT AS similarity
  FROM chunks c
  JOIN documents d ON d.id = c.document_id
  WHERE c.embedding IS NOT NULL
    AND c.embedding_status = 'complete'
    AND d.status = 'ready'
    AND (p_source_id IS NULL OR d.source_id = p_source_id)
    AND (p_kind IS NULL OR d.kind = p_kind)
    AND (p_occurred_since IS NULL OR d.occurred_at >= p_occurred_since)
    AND (p_occurred_until IS NULL OR d.occurred_at < p_occurred_until)
    AND (
      p_entity IS NULL OR EXISTS (
        SELECT 1
        FROM document_entities de
        JOIN entities e ON e.id = de.entity_id
        WHERE de.document_id = d.id
          AND (
            lower(e.name) = lower(p_entity)
            OR lower(e.external_id) = lower(p_entity)
          )
      )
    )
    AND 1 - (c.embedding <=> p_query_embedding) >= p_min_similarity
  ORDER BY c.embedding <=> p_query_embedding
  LIMIT LEAST(GREATEST(p_match_count, 1), 100);
$$;

CREATE OR REPLACE FUNCTION search_chunks_hybrid(
  p_query_text       TEXT,
  p_query_embedding  vector({{EMBEDDING_DIMENSION}}),
  p_match_count      INT DEFAULT 20,
  p_min_similarity   FLOAT DEFAULT 0.3,
  p_source_id        TEXT DEFAULT NULL,
  p_kind             TEXT DEFAULT NULL,
  p_occurred_since   TIMESTAMPTZ DEFAULT NULL,
  p_occurred_until   TIMESTAMPTZ DEFAULT NULL,
  p_entity            TEXT DEFAULT NULL,
  p_rrf_k             INT DEFAULT 60
)
RETURNS TABLE (
  chunk_id BIGINT,
  document_id UUID,
  source_id TEXT,
  external_id TEXT,
  kind TEXT,
  title TEXT,
  url TEXT,
  occurred_at TIMESTAMPTZ,
  chunk_text TEXT,
  locator JSONB,
  start_unit_sequence INT,
  end_unit_sequence INT,
  hybrid_score FLOAT,
  keyword_position BIGINT,
  semantic_position BIGINT,
  similarity FLOAT
)
LANGUAGE sql STABLE AS $$
  WITH keyword AS (
    SELECT
      result.chunk_id,
      row_number() OVER (ORDER BY result.keyword_rank DESC, result.chunk_id) AS position
    FROM search_chunks_keyword(
      p_query_text,
      LEAST(GREATEST(p_match_count * 4, 40), 400),
      p_source_id,
      p_kind,
      p_occurred_since,
      p_occurred_until,
      p_entity
    ) result
  ),
  semantic AS (
    SELECT
      result.chunk_id,
      row_number() OVER (ORDER BY result.similarity DESC, result.chunk_id) AS position,
      result.similarity
    FROM search_chunks_semantic(
      p_query_embedding,
      LEAST(GREATEST(p_match_count * 4, 40), 400),
      p_min_similarity,
      p_source_id,
      p_kind,
      p_occurred_since,
      p_occurred_until,
      p_entity
    ) result
  ),
  combined AS (
    SELECT chunk_id FROM keyword
    UNION
    SELECT chunk_id FROM semantic
  )
  SELECT
    c.id,
    d.id,
    d.source_id,
    d.external_id,
    d.kind,
    d.title,
    d.url,
    d.occurred_at,
    c.text,
    c.locator,
    c.start_unit_sequence,
    c.end_unit_sequence,
    (
      COALESCE(1.0 / (GREATEST(p_rrf_k, 1) + keyword.position), 0.0)
      + COALESCE(1.0 / (GREATEST(p_rrf_k, 1) + semantic.position), 0.0)
    )::FLOAT,
    keyword.position,
    semantic.position,
    semantic.similarity
  FROM combined
  JOIN chunks c ON c.id = combined.chunk_id
  JOIN documents d ON d.id = c.document_id
  LEFT JOIN keyword ON keyword.chunk_id = c.id
  LEFT JOIN semantic ON semantic.chunk_id = c.id
  ORDER BY
    (
      COALESCE(1.0 / (GREATEST(p_rrf_k, 1) + keyword.position), 0.0)
      + COALESCE(1.0 / (GREATEST(p_rrf_k, 1) + semantic.position), 0.0)
    ) DESC,
    semantic.similarity DESC NULLS LAST,
    c.id
  LIMIT LEAST(GREATEST(p_match_count, 1), 100);
$$;

COMMENT ON FUNCTION search_chunks_keyword IS
  'Source-neutral chunk keyword retrieval with canonical filters and citation locators.';
COMMENT ON FUNCTION search_chunks_semantic IS
  'Source-neutral cosine retrieval for the active embedding dimension.';
COMMENT ON FUNCTION search_chunks_hybrid IS
  'Reciprocal-rank fusion of canonical keyword and semantic retrieval.';
