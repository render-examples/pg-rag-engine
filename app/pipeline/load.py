"""Source-neutral discovery, persistence, and checkpoint orchestration."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

from app.chunking import ConsecutiveUnitChunker
from app.config import RagEngineConfig
from app.contracts import SourceAdapter
from app.db import connection, execute, fetch_one
from app.models import RecordRef
from app.repository import replace_chunks, upsert_document


def resolve_window(
    config: RagEngineConfig,
    adapter_id: str,
    from_datetime: datetime | None,
    to_datetime: datetime | None,
) -> tuple[datetime, datetime, dict[str, Any] | None]:
    now = datetime.now(timezone.utc)
    resolved_to = to_datetime or now
    with connection() as conn:
        checkpoint = fetch_one(
            conn,
            "SELECT cursor, watermark_at FROM source_checkpoints WHERE source_id = %s",
            (adapter_id,),
        )
    if from_datetime:
        resolved_from = from_datetime
    elif checkpoint and checkpoint["watermark_at"]:
        lookback = int(config.adapter(adapter_id).config.get("lookback_hours", 24))
        watermark = min(checkpoint["watermark_at"], resolved_to)
        resolved_from = watermark - timedelta(hours=lookback)
    else:
        initial_days = int(
            config.adapter(adapter_id).config.get("initial_load_days", 90)
        )
        resolved_from = resolved_to - timedelta(days=initial_days)
    if resolved_from >= resolved_to:
        raise ValueError("from_datetime must be before to_datetime")
    return resolved_from, resolved_to, checkpoint


def create_sync_run(
    source_id: str,
    mode: str,
    from_datetime: datetime | None,
    to_datetime: datetime | None,
) -> str:
    with connection() as conn:
        row = conn.execute(
            """
            INSERT INTO sync_runs (source_id, mode, from_datetime, to_datetime)
            VALUES (%s, %s, %s, %s)
            RETURNING id
            """,
            (source_id, mode, from_datetime, to_datetime),
        ).fetchone()
        return str(row["id"])


def finish_sync_run(
    run_id: str,
    status: str,
    counts: dict[str, Any],
    error: str | None = None,
) -> None:
    with connection() as conn:
        execute(
            conn,
            """
            UPDATE sync_runs SET
              status = %s,
              counts = %s,
              error = %s,
              finished_at = now()
            WHERE id = %s
            """,
            (status, json.dumps(counts), error[:4000] if error else None, run_id),
        )


def process_batch(
    config: RagEngineConfig,
    adapter: SourceAdapter,
    refs: Sequence[RecordRef],
) -> dict[str, Any]:
    raw_records = adapter.fetch_batch(refs)
    chunker = ConsecutiveUnitChunker(config.chunking)
    stats: dict[str, Any] = {
        "requested": len(refs),
        "fetched": len(raw_records),
        "documents": 0,
        "changed": 0,
        "failed": 0,
        "errors": [],
    }
    for raw in raw_records:
        try:
            document = adapter.normalize(raw)
            with connection() as conn:
                with conn.transaction():
                    document_id, changed = upsert_document(conn, document)
                    if changed:
                        replace_chunks(
                            conn,
                            document_id,
                            []
                            if document.status == "deleted"
                            else chunker.chunk(document.units),
                            config.embedding,
                        )
                    adapter.persist_projection(conn, document_id, raw, document)
            stats["documents"] += 1
            stats["changed"] += int(changed)
        except Exception as exc:
            stats["failed"] += 1
            stats["errors"].append(
                {
                    "error": str(exc),
                    "external_id": getattr(raw, "external_id", None)
                    or (raw.get("id") if isinstance(raw, dict) else None),
                }
            )
    missing = len(refs) - len(raw_records)
    if missing > 0:
        stats["failed"] += missing
        stats["errors"].append({"error": f"adapter omitted {missing} records"})
    stats["errors"] = stats["errors"][:10]
    return stats


def update_checkpoint(
    source_id: str,
    watermark_at: datetime,
    cursor: dict[str, Any] | None = None,
) -> None:
    watermark_at = min(watermark_at, datetime.now(timezone.utc))
    with connection() as conn:
        execute(
            conn,
            """
            INSERT INTO source_checkpoints (source_id, cursor, watermark_at)
            VALUES (%s, %s, %s)
            ON CONFLICT (source_id) DO UPDATE SET
              cursor = COALESCE(EXCLUDED.cursor, source_checkpoints.cursor),
              watermark_at = CASE
                WHEN source_checkpoints.watermark_at IS NULL THEN EXCLUDED.watermark_at
                ELSE GREATEST(
                  LEAST(source_checkpoints.watermark_at, now()),
                  EXCLUDED.watermark_at
                )
              END,
              updated_at = now()
            """,
            (
                source_id,
                json.dumps(cursor) if cursor is not None else None,
                watermark_at,
            ),
        )


def deserialize_refs(values: Sequence[dict[str, Any]]) -> list[RecordRef]:
    return [RecordRef.from_dict(value) for value in values]
