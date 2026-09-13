"""Adapter for bundled files or HTTP JSON document collections."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Sequence
from urllib.request import urlopen

import psycopg

from app.config import ROOT, AdapterConfig
from app.models import (
    ContentUnit,
    Entity,
    NormalizedDocument,
    RecordRef,
    SourceLocator,
)


def _datetime(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class JsonAdapter:
    def __init__(self, config: AdapterConfig) -> None:
        self.id = config.id
        self.config = config
        self.location = str(config.config.get("path") or config.config.get("url") or "")
        self.mapping = dict(config.config.get("mapping", {}))

    def _get(self, record: dict[str, Any], field: str, default: Any = None) -> Any:
        path = str(self.mapping.get(field, field))
        value: Any = record
        for part in path.split("."):
            if not isinstance(value, dict) or part not in value:
                return default
            value = value[part]
        return value

    def configured(self) -> bool:
        if self.location.startswith(("http://", "https://")):
            return True
        return bool(self.location and (ROOT / self.location).exists())

    def missing_environment(self) -> list[str]:
        return [] if self.configured() else [f"JSON source {self.location!r}"]

    def _records(self) -> list[dict[str, Any]]:
        if self.location.startswith(("http://", "https://")):
            with urlopen(self.location, timeout=30) as response:
                payload = json.loads(response.read().decode())
        else:
            payload = json.loads((ROOT / self.location).read_text())
        records = payload.get("documents") if isinstance(payload, dict) else payload
        if not isinstance(records, list) or not all(isinstance(row, dict) for row in records):
            raise ValueError("JSON adapter expects an array or {documents: [...]} object")
        return records

    def discover(
        self,
        from_datetime: datetime | None,
        to_datetime: datetime | None,
        checkpoint: dict[str, Any] | None,
    ) -> list[RecordRef]:
        del checkpoint
        refs: list[RecordRef] = []
        ignore_window = bool(self.config.config.get("ignore_window", False))
        for index, record in enumerate(self._records()):
            occurred_at = _datetime(self._get(record, "occurred_at"))
            if not ignore_window and from_datetime and occurred_at < from_datetime:
                continue
            if not ignore_window and to_datetime and occurred_at >= to_datetime:
                continue
            external_id = str(self._get(record, "external_id", record.get("id")) or "")
            if not external_id:
                raise ValueError(f"JSON record at index {index} is missing id")
            refs.append(
                RecordRef(
                    external_id=external_id,
                    locator=SourceLocator({"index": index}),
                    updated_at=_datetime(self._get(record, "updated_at"))
                    if self._get(record, "updated_at")
                    else None,
                )
            )
        return refs

    def fetch_batch(self, refs: Sequence[RecordRef]) -> list[dict[str, Any]]:
        records = self._records()
        by_id = {
            str(self._get(record, "external_id", record.get("id"))): record
            for record in records
        }
        return [by_id[ref.external_id] for ref in refs if ref.external_id in by_id]

    def normalize(self, raw: dict[str, Any]) -> NormalizedDocument:
        external_id = str(self._get(raw, "external_id", raw.get("id")))
        raw_units = self._get(raw, "units")
        if raw_units is None:
            raw_units = [{"text": self._get(raw, "text", "")}]
        units = tuple(
            ContentUnit(
                sequence=index,
                text=str(unit["text"]),
                author=unit.get("author"),
                started_at_ms=unit.get("started_at_ms"),
                ended_at_ms=unit.get("ended_at_ms"),
                metadata=dict(unit.get("metadata", {})),
                locator=SourceLocator(
                    unit.get("locator")
                    or {"document": external_id, "unit": index}
                ),
            )
            for index, unit in enumerate(raw_units, start=1)
            if str(unit.get("text", "")).strip()
        )
        entities = tuple(
            Entity(
                type=str(entity["type"]),
                external_id=str(entity["external_id"])
                if entity.get("external_id") is not None
                else None,
                name=entity.get("name"),
                metadata=dict(entity.get("metadata", {})),
            )
            for entity in self._get(raw, "entities", [])
        )
        return NormalizedDocument(
            source_id=self.id,
            external_id=external_id,
            kind=str(self._get(raw, "kind", "document")),
            title=self._get(raw, "title"),
            occurred_at=_datetime(self._get(raw, "occurred_at")),
            updated_at=_datetime(self._get(raw, "updated_at"))
            if self._get(raw, "updated_at")
            else None,
            url=self._get(raw, "url"),
            metadata=dict(self._get(raw, "metadata", {})),
            units=units,
            status="deleted" if self._get(raw, "deleted", False) else "ready",
            entities=entities,
            raw=raw,
        )

    def persist_projection(
        self,
        conn: psycopg.Connection,
        document_id: str,
        raw: Any,
        document: NormalizedDocument,
    ) -> None:
        del conn, document_id, raw, document
