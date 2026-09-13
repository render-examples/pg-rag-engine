from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path

from app.adapters.gong.adapter import GongAdapter
from app.adapters.json.adapter import JsonAdapter
from app.config import load_config
from app.db import connection, fetch_one
from app.models import RecordRef, SourceLocator
from app.pipeline.load import process_batch
from tests.adapter_contract import (
    assert_discovery_contract,
    assert_document_contract,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_json_adapter_contract():
    config = load_config()
    adapter = JsonAdapter(config.adapter("json"))
    refs = adapter.discover(
        datetime(2026, 9, 1, tzinfo=timezone.utc),
        datetime(2026, 10, 1, tzinfo=timezone.utc),
        None,
    )
    assert adapter.configured()
    assert len(refs) == 6
    records = adapter.fetch_batch(refs)
    assert_discovery_contract(adapter, refs, records)
    assert len(records) == len(refs)
    documents = [adapter.normalize(record) for record in records]
    for raw, document in zip(records, documents, strict=False):
        assert_document_contract(adapter, raw, document)
    assert all(document.source_id == "json" for document in documents)
    assert all(document.units for document in documents)
    assert len({document.external_id for document in documents}) == len(documents)
    assert documents[0].content_hash == adapter.normalize(records[0]).content_hash
    assert documents[0].units[0].locator.value

    updated = copy.deepcopy(records[0])
    updated["title"] = "Changed title"
    assert adapter.normalize(updated).content_hash != documents[0].content_hash
    updated["deleted"] = True
    assert adapter.normalize(updated).status == "deleted"


def test_gong_adapter_normalizes_to_canonical_document():
    config = load_config()
    adapter = GongAdapter(config.adapters["gong"])
    basic = json.loads((FIXTURES / "call_basic.json").read_text())
    extensive = json.loads((FIXTURES / "call_extensive.json").read_text())
    transcript = json.loads((FIXTURES / "transcript.json").read_text())

    document = adapter.normalize(
        {"basic": basic, "extensive": extensive, "transcript": transcript}
    )
    assert_document_contract(
        adapter,
        {"basic": basic, "extensive": extensive, "transcript": transcript},
        document,
    )

    assert document.source_id == "gong"
    assert document.external_id == "5599332235511222779"
    assert document.kind == "conversation"
    assert len(document.units) == 3
    assert document.units[0].author == "Sarah Chen"
    assert document.units[1].started_at_ms == 5500
    assert {entity.type for entity in document.entities} >= {
        "person",
        "account",
        "opportunity",
    }
    assert document.content_hash == adapter.normalize(
        {"basic": basic, "extensive": extensive, "transcript": transcript}
    ).content_hash


def test_gong_adapter_runs_through_canonical_pipeline(monkeypatch):
    config = load_config()
    adapter = GongAdapter(config.adapters["gong"])
    raw = {
        "basic": json.loads((FIXTURES / "call_basic.json").read_text()),
        "extensive": json.loads((FIXTURES / "call_extensive.json").read_text()),
        "transcript": json.loads((FIXTURES / "transcript.json").read_text()),
    }
    with connection() as conn:
        if fetch_one(
            conn, "SELECT to_regclass('public.gong_calls') AS name"
        )["name"] is None:
            conn.execute(
                Path("app/adapters/gong/migrations/001_gong.sql").read_text()
            )
        conn.execute(
            """
            INSERT INTO schema_migrations (id)
            VALUES ('app/adapters/gong/migrations/001_gong.sql')
            ON CONFLICT DO NOTHING
            """
        )
        conn.execute(
            """
            DELETE FROM documents
            WHERE source_id = 'gong' AND external_id = '5599332235511222779'
            """
        )
    monkeypatch.setattr(adapter, "fetch_batch", lambda refs: [raw])
    refs = [
        RecordRef(
            external_id="5599332235511222779",
            locator=SourceLocator({"fixture": True}),
        )
    ]

    first = process_batch(config, adapter, refs)
    second = process_batch(config, adapter, refs)

    assert first["documents"] == 1
    assert first["changed"] == 1
    assert second["documents"] == 1
    assert second["changed"] == 0
    with connection() as conn:
        row = fetch_one(
            conn,
            """
            SELECT gcc.external_id, gcc.scope, gcc.participant_count
            FROM gong_call_context gcc
            WHERE gcc.external_id = '5599332235511222779'
            """,
        )
    assert row == {
        "external_id": "5599332235511222779",
        "scope": "External",
        "participant_count": 2,
    }
