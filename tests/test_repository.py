from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from app.adapters.gong.adapter import GongAdapter
from app.adapters.json.adapter import JsonAdapter
from app.chunking import ConsecutiveUnitChunker
from app.config import load_config
from app.db import connection, fetch_one
from app.repository import replace_chunks, upsert_document

FIXTURES = Path(__file__).parent / "fixtures"


def test_canonical_document_upsert_is_idempotent():
    config = load_config()
    adapter = JsonAdapter(config.adapter("json"))
    raw = adapter.fetch_batch(
        adapter.discover(
            None,
            None,
            None,
        )
    )[0]
    raw = {**raw, "id": f"{raw['id']}-repository-test"}
    document = adapter.normalize(raw)
    chunker = ConsecutiveUnitChunker(config.chunking)

    with connection() as conn:
        with conn.transaction(force_rollback=True):
            document_id, changed = upsert_document(conn, document)
            replace_chunks(
                conn,
                document_id,
                chunker.chunk(document.units),
                config.embedding,
            )
            second_id, second_changed = upsert_document(conn, document)
            assert second_id == document_id
            assert changed is True
            assert second_changed is False
            counts = fetch_one(
                conn,
                """
                SELECT
                  (SELECT count(*) FROM content_units WHERE document_id = %s)::int AS units,
                  (SELECT count(*) FROM chunks WHERE document_id = %s)::int AS chunks
                """,
                (document_id, document_id),
            )
            assert counts["units"] == len(document.units)
            assert counts["chunks"] > 0

            deleted_id, deleted_changed = upsert_document(
                conn, replace(document, status="deleted")
            )
            replace_chunks(conn, deleted_id, [], config.embedding)
            deleted = fetch_one(
                conn,
                """
                SELECT
                  d.status,
                  (SELECT count(*) FROM content_units WHERE document_id = d.id)::int AS units,
                  (SELECT count(*) FROM chunks WHERE document_id = d.id)::int AS chunks
                FROM documents d WHERE d.id = %s
                """,
                (deleted_id,),
            )
            assert deleted_changed is True
            assert deleted == {"status": "deleted", "units": 0, "chunks": 0}


def test_gong_projection_links_to_canonical_document():
    config = load_config()
    adapter = GongAdapter(config.adapters["gong"])
    document = adapter.normalize(
        {
            "basic": json.loads((FIXTURES / "call_basic.json").read_text()),
            "extensive": json.loads((FIXTURES / "call_extensive.json").read_text()),
            "transcript": json.loads((FIXTURES / "transcript.json").read_text()),
        }
    )
    with connection() as conn:
        with conn.transaction(force_rollback=True):
            if fetch_one(
                conn, "SELECT to_regclass('public.gong_calls') AS name"
            )["name"] is None:
                conn.execute(
                    (
                        Path("app/adapters/gong/migrations/001_gong.sql")
                    ).read_text()
                )
            document_id, _ = upsert_document(conn, document)
            replace_chunks(
                conn,
                document_id,
                ConsecutiveUnitChunker(config.chunking).chunk(document.units),
                config.embedding,
            )
            adapter.persist_projection(conn, document_id, document.raw, document)
            row = fetch_one(
                conn,
                """
                SELECT external_id, scope, participant_count, crm, topics
                FROM gong_call_context WHERE id = %s
                """,
                (document_id,),
            )
            assert row["external_id"] == "5599332235511222779"
            assert row["scope"] == "External"
            assert row["participant_count"] == 2
            assert len(row["crm"]) == 2
            assert row["topics"][0]["name"] == "Pricing"
