"""Canonical pending-chunk embedding pipeline."""

from __future__ import annotations

import math
from typing import Any

from app.config import RagEngineConfig
from app.contracts import EmbeddingProvider
from app.db import connection, execute, fetch_all


def vector_literal(vector: list[float]) -> str:
    if not vector or not all(math.isfinite(value) for value in vector):
        raise ValueError("embedding contains invalid values")
    return "[" + ",".join(format(value, ".9g") for value in vector) + "]"


def _refresh_jobs(document_ids: set[str], profile_id: str) -> None:
    with connection() as conn:
        for document_id in document_ids:
            execute(
                conn,
                """
                INSERT INTO embedding_jobs (
                  document_id, profile_id, status, chunk_count, embedded_count,
                  attempts, last_error
                )
                SELECT
                  %s, %s,
                  CASE
                    WHEN count(*) = 0 THEN 'complete'
                    WHEN count(*) FILTER (WHERE embedding_status = 'complete') = count(*)
                      THEN 'complete'
                    WHEN count(*) FILTER (WHERE embedding_status = 'complete') > 0
                      THEN 'partial'
                    WHEN count(*) FILTER (WHERE embedding_status = 'failed') > 0
                      THEN 'failed'
                    ELSE 'pending'
                  END,
                  count(*)::int,
                  count(*) FILTER (WHERE embedding_status = 'complete')::int,
                  1,
                  max(last_error)
                FROM chunks WHERE document_id = %s
                ON CONFLICT (document_id, profile_id) DO UPDATE SET
                  status = EXCLUDED.status,
                  chunk_count = EXCLUDED.chunk_count,
                  embedded_count = EXCLUDED.embedded_count,
                  attempts = embedding_jobs.attempts + 1,
                  last_error = EXCLUDED.last_error,
                  updated_at = now()
                """,
                (document_id, profile_id, document_id),
            )


def embed_pending(
    config: RagEngineConfig,
    provider: EmbeddingProvider,
    *,
    limit: int = 1000,
) -> dict[str, Any]:
    profile = config.embedding
    with connection() as conn:
        execute(
            conn,
            """
            UPDATE chunks SET
              embedding = NULL,
              embedding_profile_id = %s,
              embedding_fingerprint = %s,
              embedding_status = 'pending',
              embedded_at = NULL,
              last_error = NULL
            WHERE embedding_fingerprint IS DISTINCT FROM %s
            """,
            (profile.profile_id, profile.fingerprint, profile.fingerprint),
        )
        rows = fetch_all(
            conn,
            """
            SELECT id, document_id, text
            FROM chunks
            WHERE embedding_status IN ('pending', 'failed')
            ORDER BY document_id, sequence
            LIMIT %s
            """,
            (limit,),
        )
    if not rows:
        return {"chunks": 0, "documents": 0, "failed": 0}

    document_ids = {str(row["document_id"]) for row in rows}
    try:
        vectors = provider.embed_documents([row["text"] for row in rows])
        if len(vectors) != len(rows):
            raise ValueError("embedding provider returned the wrong number of vectors")
    except Exception as exc:
        with connection() as conn:
            execute(
                conn,
                """
                UPDATE chunks SET embedding_status = 'failed', last_error = %s
                WHERE id = ANY(%s)
                """,
                (str(exc)[:2000], [row["id"] for row in rows]),
            )
        _refresh_jobs(document_ids, profile.profile_id)
        return {
            "chunks": 0,
            "documents": len(document_ids),
            "failed": len(rows),
            "error": str(exc),
        }

    with connection() as conn:
        with conn.transaction():
            for row, vector in zip(rows, vectors, strict=False):
                execute(
                    conn,
                    """
                    UPDATE chunks SET
                      embedding = %s::vector,
                      embedding_profile_id = %s,
                      embedding_fingerprint = %s,
                      embedding_status = 'complete',
                      embedded_at = now(),
                      last_error = NULL
                    WHERE id = %s
                    """,
                    (
                        vector_literal(vector),
                        profile.profile_id,
                        profile.fingerprint,
                        row["id"],
                    ),
                )
    _refresh_jobs(document_ids, profile.profile_id)
    return {
        "chunks": len(rows),
        "documents": len(document_ids),
        "failed": 0,
    }
