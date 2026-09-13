"""Idempotent persistence for canonical documents and retrieval chunks."""

from __future__ import annotations

import json
from typing import Any

import psycopg

from app.config import EmbeddingConfig
from app.db import execute, fetch_one
from app.models import Chunk, Entity, NormalizedDocument


def _json(value: Any) -> str:
    return json.dumps(value, default=str)


def upsert_document(
    conn: psycopg.Connection, document: NormalizedDocument
) -> tuple[str, bool]:
    existing = fetch_one(
        conn,
        """
        SELECT id, content_hash
        FROM documents
        WHERE source_id = %s AND external_id = %s
        """,
        (document.source_id, document.external_id),
    )
    changed = existing is None or existing["content_hash"] != document.content_hash
    row = conn.execute(
        """
        INSERT INTO documents (
          source_id, external_id, kind, title, url, occurred_at,
          source_updated_at, status, metadata, raw, content_hash
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (source_id, external_id) DO UPDATE SET
          kind = EXCLUDED.kind,
          title = EXCLUDED.title,
          url = EXCLUDED.url,
          occurred_at = EXCLUDED.occurred_at,
          source_updated_at = EXCLUDED.source_updated_at,
          status = EXCLUDED.status,
          metadata = EXCLUDED.metadata,
          raw = EXCLUDED.raw,
          content_hash = EXCLUDED.content_hash,
          updated_at = now()
        RETURNING id
        """,
        (
            document.source_id,
            document.external_id,
            document.kind,
            document.title,
            document.url,
            document.occurred_at,
            document.updated_at,
            document.status,
            _json(document.metadata),
            _json(document.raw) if document.raw is not None else None,
            document.content_hash,
        ),
    ).fetchone()
    document_id = str(row["id"])
    if changed:
        if document.status == "deleted":
            execute(
                conn,
                "DELETE FROM content_units WHERE document_id = %s",
                (document_id,),
            )
            execute(
                conn,
                "DELETE FROM document_entities WHERE document_id = %s",
                (document_id,),
            )
        else:
            replace_content_units(conn, document_id, document)
            replace_entities(conn, document_id, document.source_id, document.entities)
    return document_id, changed


def replace_content_units(
    conn: psycopg.Connection, document_id: str, document: NormalizedDocument
) -> None:
    execute(conn, "DELETE FROM content_units WHERE document_id = %s", (document_id,))
    for unit in document.units:
        execute(
            conn,
            """
            INSERT INTO content_units (
              document_id, sequence, text, author, started_at_ms, ended_at_ms,
              locator, metadata
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                document_id,
                unit.sequence,
                unit.text,
                unit.author,
                unit.started_at_ms,
                unit.ended_at_ms,
                _json(unit.locator.value),
                _json(unit.metadata),
            ),
        )


def _upsert_entity(
    conn: psycopg.Connection, source_id: str, entity: Entity
) -> str:
    if entity.external_id:
        row = conn.execute(
            """
            INSERT INTO entities (source_id, type, external_id, name, metadata)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (source_id, type, external_id)
              WHERE external_id IS NOT NULL
            DO UPDATE SET
              name = EXCLUDED.name,
              metadata = EXCLUDED.metadata,
              updated_at = now()
            RETURNING id
            """,
            (
                source_id,
                entity.type,
                entity.external_id,
                entity.name,
                _json(entity.metadata),
            ),
        ).fetchone()
        return str(row["id"])

    existing = fetch_one(
        conn,
        """
        SELECT id FROM entities
        WHERE source_id = %s AND type = %s AND lower(name) = lower(%s)
        LIMIT 1
        """,
        (source_id, entity.type, entity.name),
    )
    if existing:
        return str(existing["id"])
    row = conn.execute(
        """
        INSERT INTO entities (source_id, type, name, metadata)
        VALUES (%s, %s, %s, %s)
        RETURNING id
        """,
        (source_id, entity.type, entity.name, _json(entity.metadata)),
    ).fetchone()
    return str(row["id"])


def replace_entities(
    conn: psycopg.Connection,
    document_id: str,
    source_id: str,
    entities: tuple[Entity, ...],
) -> None:
    execute(conn, "DELETE FROM document_entities WHERE document_id = %s", (document_id,))
    for entity in entities:
        if not entity.external_id and not entity.name:
            continue
        entity_id = _upsert_entity(conn, source_id, entity)
        execute(
            conn,
            """
            INSERT INTO document_entities (document_id, entity_id)
            VALUES (%s, %s)
            ON CONFLICT DO NOTHING
            """,
            (document_id, entity_id),
        )


def replace_chunks(
    conn: psycopg.Connection,
    document_id: str,
    chunks: list[Chunk],
    profile: EmbeddingConfig,
) -> None:
    new_hashes = [chunk.content_hash for chunk in chunks]
    execute(
        conn,
        "UPDATE chunks SET sequence = sequence + 1000000 WHERE document_id = %s",
        (document_id,),
    )
    for chunk in chunks:
        existing = fetch_one(
            conn,
            "SELECT id FROM chunks WHERE document_id = %s AND content_hash = %s",
            (document_id, chunk.content_hash),
        )
        if existing:
            execute(
                conn,
                """
                UPDATE chunks SET
                  sequence = %s,
                  start_unit_sequence = %s,
                  end_unit_sequence = %s,
                  text = %s,
                  token_estimate = %s,
                  locator = %s
                WHERE id = %s
                """,
                (
                    chunk.sequence,
                    chunk.start_unit_sequence,
                    chunk.end_unit_sequence,
                    chunk.text,
                    chunk.token_estimate,
                    _json(chunk.locator.value),
                    existing["id"],
                ),
            )
        else:
            execute(
                conn,
                """
                INSERT INTO chunks (
                  document_id, sequence, start_unit_sequence, end_unit_sequence,
                  text, token_estimate, locator, content_hash,
                  embedding_profile_id, embedding_fingerprint, embedding_status
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending')
                """,
                (
                    document_id,
                    chunk.sequence,
                    chunk.start_unit_sequence,
                    chunk.end_unit_sequence,
                    chunk.text,
                    chunk.token_estimate,
                    _json(chunk.locator.value),
                    chunk.content_hash,
                    profile.profile_id,
                    profile.fingerprint,
                ),
            )
    if new_hashes:
        execute(
            conn,
            """
            DELETE FROM chunks
            WHERE document_id = %s AND NOT (content_hash = ANY(%s))
            """,
            (document_id, new_hashes),
        )
    else:
        execute(conn, "DELETE FROM chunks WHERE document_id = %s", (document_id,))
    execute(
        conn,
        """
        INSERT INTO embedding_jobs (
          document_id, profile_id, status, chunk_count, embedded_count
        )
        SELECT
          %s, %s,
          CASE
            WHEN count(*) = 0 THEN 'complete'
            WHEN count(*) FILTER (WHERE embedding_status = 'complete') = count(*)
              THEN 'complete'
            WHEN count(*) FILTER (WHERE embedding_status = 'complete') > 0
              THEN 'partial'
            ELSE 'pending'
          END,
          count(*)::int,
          count(*) FILTER (WHERE embedding_status = 'complete')::int
        FROM chunks WHERE document_id = %s
        ON CONFLICT (document_id, profile_id) DO UPDATE SET
          status = EXCLUDED.status,
          chunk_count = EXCLUDED.chunk_count,
          embedded_count = EXCLUDED.embedded_count,
          last_error = NULL,
          updated_at = now()
        """,
        (document_id, profile.profile_id, document_id),
    )
