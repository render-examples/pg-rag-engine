"""Generic source-neutral MCP tool implementations."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from app.config import RagEngineConfig
from app.db import connection, fetch_all, fetch_one
from app.pipeline.embed import vector_literal
from app.registry import create_embedding_provider

READ_ONLY = ToolAnnotations(readOnlyHint=True)


def _citation(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_id": row["source_id"],
        "document_id": str(row["document_id"]),
        "external_id": row["external_id"],
        "url": row.get("url"),
        "chunk_id": str(row["chunk_id"]) if row.get("chunk_id") else None,
        "unit_range": {
            "start": row.get("start_unit_sequence"),
            "end": row.get("end_unit_sequence"),
        },
        "locator": row.get("locator") or {},
    }


def _passage(row: dict[str, Any]) -> dict[str, Any]:
    score_keys = (
        "keyword_rank",
        "similarity",
        "hybrid_score",
        "keyword_position",
        "semantic_position",
    )
    return {
        "document_id": str(row["document_id"]),
        "source_id": row["source_id"],
        "external_id": row["external_id"],
        "kind": row["kind"],
        "title": row["title"],
        "url": row["url"],
        "occurred_at": row["occurred_at"],
        "chunk_id": str(row["chunk_id"]),
        "text": row["chunk_text"],
        "scores": {key: row[key] for key in score_keys if row.get(key) is not None},
        "citation": _citation(row),
    }


def register_core_tools(server: MCPServer, config: RagEngineConfig) -> None:
    namespace = config.mcp.namespace

    @server.tool(
        name=f"{namespace}.schema.describe",
        description="Describe canonical intelligence tables, views, and search functions.",
        annotations=READ_ONLY,
        structured_output=True,
    )
    def schema_describe(
        object_name: str | None = None, limit: int = 200
    ) -> dict[str, Any]:
        limit = min(max(limit, 1), 500)
        with connection() as conn:
            objects = fetch_all(
                conn,
                """
                SELECT * FROM agent_schema_catalog
                WHERE (%s::text IS NULL OR object_name = %s)
                ORDER BY object_name, ordinal_position NULLS FIRST
                LIMIT %s
                """,
                (object_name, object_name, limit),
            )
            functions = fetch_all(
                conn,
                """
                SELECT
                  p.proname AS name,
                  pg_get_function_identity_arguments(p.oid) AS arguments,
                  pg_get_function_result(p.oid) AS result_type,
                  obj_description(p.oid, 'pg_proc') AS description
                FROM pg_proc p
                JOIN pg_namespace n ON n.oid = p.pronamespace
                WHERE n.nspname = 'public'
                  AND (%s::text IS NULL OR p.proname = %s)
                ORDER BY p.proname
                LIMIT %s
                """,
                (object_name, object_name, limit),
            )
        return {"objects": objects, "functions": functions}

    @server.tool(
        name=f"{namespace}.sources.list",
        description="List configured ingestion sources and their adapter kinds.",
        annotations=READ_ONLY,
        structured_output=True,
    )
    def sources_list() -> dict[str, Any]:
        with connection() as conn:
            rows = fetch_all(
                conn,
                """
                SELECT id, adapter_kind, display_name, enabled, created_at, updated_at
                FROM sources ORDER BY id
                """,
            )
        return {"sources": rows}

    @server.tool(
        name=f"{namespace}.documents.list",
        description="List canonical documents using source, kind, date, and entity filters.",
        annotations=READ_ONLY,
        structured_output=True,
    )
    def documents_list(
        source_id: str | None = None,
        kind: str | None = None,
        occurred_since: datetime | None = None,
        occurred_until: datetime | None = None,
        entity: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        limit = min(max(limit, 1), 100)
        with connection() as conn:
            rows = fetch_all(
                conn,
                """
                SELECT
                  dc.id, dc.source_id, dc.external_id, dc.kind, dc.title, dc.url,
                  dc.occurred_at, dc.source_updated_at, dc.metadata, dc.unit_count,
                  dc.entities
                FROM document_context dc
                WHERE dc.status = 'ready'
                  AND (%s::text IS NULL OR dc.source_id = %s)
                  AND (%s::text IS NULL OR dc.kind = %s)
                  AND (%s::timestamptz IS NULL OR dc.occurred_at >= %s)
                  AND (%s::timestamptz IS NULL OR dc.occurred_at < %s)
                  AND (
                    %s::text IS NULL OR EXISTS (
                      SELECT 1
                      FROM document_entities de
                      JOIN entities e ON e.id = de.entity_id
                      WHERE de.document_id = dc.id
                        AND (
                          lower(e.name) = lower(%s)
                          OR lower(e.external_id) = lower(%s)
                        )
                    )
                  )
                ORDER BY dc.occurred_at DESC, dc.id
                LIMIT %s
                """,
                (
                    source_id,
                    source_id,
                    kind,
                    kind,
                    occurred_since,
                    occurred_since,
                    occurred_until,
                    occurred_until,
                    entity,
                    entity,
                    entity,
                    limit,
                ),
            )
        return {
            "documents": [
                {
                    **row,
                    "id": str(row["id"]),
                    "citation": {
                        "source_id": row["source_id"],
                        "document_id": str(row["id"]),
                        "external_id": row["external_id"],
                        "url": row["url"],
                    },
                }
                for row in rows
            ]
        }

    @server.tool(
        name=f"{namespace}.documents.get",
        description="Get a canonical document, its ordered content, entities, and citation.",
        annotations=READ_ONLY,
        structured_output=True,
    )
    def documents_get(
        document_id: str, include_units: bool = True
    ) -> dict[str, Any]:
        with connection() as conn:
            document = fetch_one(
                conn, "SELECT * FROM document_context WHERE id = %s", (document_id,)
            )
            if not document:
                raise ValueError(f"document not found: {document_id}")
            units = (
                fetch_all(
                    conn,
                    """
                    SELECT sequence, text, author, started_at_ms, ended_at_ms,
                           locator, metadata
                    FROM content_units
                    WHERE document_id = %s
                    ORDER BY sequence
                    """,
                    (document_id,),
                )
                if include_units
                else []
            )
        return {
            "document": {**document, "id": str(document["id"])},
            "units": units,
            "citation": {
                "source_id": document["source_id"],
                "document_id": str(document["id"]),
                "external_id": document["external_id"],
                "url": document["url"],
            },
        }

    def search(
        mode: str,
        query: str,
        source_id: str | None,
        kind: str | None,
        occurred_since: datetime | None,
        occurred_until: datetime | None,
        entity: str | None,
        limit: int,
        min_similarity: float,
    ) -> dict[str, Any]:
        query = query.strip()
        if not query:
            raise ValueError("query must not be empty")
        limit = min(max(limit, 1), 100)
        common = (
            limit,
            source_id,
            kind,
            occurred_since,
            occurred_until,
            entity,
        )
        with connection() as conn:
            if mode == "keyword":
                rows = fetch_all(
                    conn,
                    "SELECT * FROM search_chunks_keyword(%s, %s, %s, %s, %s, %s, %s)",
                    (query, *common),
                )
            else:
                embedding = create_embedding_provider(config).embed_query(query)
                if mode == "semantic":
                    rows = fetch_all(
                        conn,
                        "SELECT * FROM search_chunks_semantic(%s::vector, %s, %s, %s, %s, %s, %s, %s)",
                        (vector_literal(embedding), limit, min_similarity, *common[1:]),
                    )
                else:
                    rows = fetch_all(
                        conn,
                        "SELECT * FROM search_chunks_hybrid(%s, %s::vector, %s, %s, %s, %s, %s, %s, %s)",
                        (
                            query,
                            vector_literal(embedding),
                            limit,
                            min_similarity,
                            *common[1:],
                        ),
                    )
        return {"passages": [_passage(row) for row in rows]}

    def arguments(
        query: str,
        source_id: str | None,
        kind: str | None,
        occurred_since: datetime | None,
        occurred_until: datetime | None,
        entity: str | None,
        limit: int,
    ) -> tuple[Any, ...]:
        return (
            query,
            source_id,
            kind,
            occurred_since,
            occurred_until,
            entity,
            limit,
        )

    @server.tool(
        name=f"{namespace}.search.keyword",
        description="Search canonical chunks for exact terms and phrases with citations.",
        annotations=READ_ONLY,
        structured_output=True,
    )
    def search_keyword(
        query: str,
        source_id: str | None = None,
        kind: str | None = None,
        occurred_since: datetime | None = None,
        occurred_until: datetime | None = None,
        entity: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        return search("keyword", *arguments(query, source_id, kind, occurred_since, occurred_until, entity, limit), 0.0)

    @server.tool(
        name=f"{namespace}.search.semantic",
        description="Search canonical chunks by meaning using the configured private embedding profile.",
        annotations=READ_ONLY,
        structured_output=True,
    )
    def search_semantic(
        query: str,
        source_id: str | None = None,
        kind: str | None = None,
        occurred_since: datetime | None = None,
        occurred_until: datetime | None = None,
        entity: str | None = None,
        limit: int = 20,
        min_similarity: float = 0.5,
    ) -> dict[str, Any]:
        return search("semantic", *arguments(query, source_id, kind, occurred_since, occurred_until, entity, limit), min_similarity)

    @server.tool(
        name=f"{namespace}.search.hybrid",
        description="Default retrieval: reciprocal-rank fusion of keyword and semantic search.",
        annotations=READ_ONLY,
        structured_output=True,
    )
    def search_hybrid(
        query: str,
        source_id: str | None = None,
        kind: str | None = None,
        occurred_since: datetime | None = None,
        occurred_until: datetime | None = None,
        entity: str | None = None,
        limit: int = 20,
        min_similarity: float = 0.3,
    ) -> dict[str, Any]:
        return search("hybrid", *arguments(query, source_id, kind, occurred_since, occurred_until, entity, limit), min_similarity)
