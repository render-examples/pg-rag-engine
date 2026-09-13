"""Reusable assertions every source adapter must satisfy."""

from __future__ import annotations

import json
from typing import Any, Sequence

from app.contracts import SourceAdapter
from app.models import NormalizedDocument, RecordRef


def assert_discovery_contract(
    adapter: SourceAdapter,
    refs: Sequence[RecordRef],
    raw_records: Sequence[Any],
) -> None:
    assert len({ref.external_id for ref in refs}) == len(refs)
    assert all(ref.locator.value for ref in refs)
    assert len(raw_records) == len(refs)
    json.dumps([ref.to_dict() for ref in refs])


def assert_document_contract(
    adapter: SourceAdapter,
    raw: Any,
    document: NormalizedDocument,
) -> None:
    repeated = adapter.normalize(raw)
    assert document.source_id == adapter.id
    assert document.external_id
    assert document.kind
    assert document.occurred_at.tzinfo is not None
    assert document.content_hash == repeated.content_hash
    assert [unit.sequence for unit in document.units] == list(
        range(1, len(document.units) + 1)
    )
    assert all(unit.locator.value for unit in document.units)
    json.dumps(document.metadata, default=str)
