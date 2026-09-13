"""Canonical source-neutral ingestion models."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class SourceLocator:
    value: dict[str, Any]


@dataclass(frozen=True)
class RecordRef:
    external_id: str
    locator: SourceLocator
    updated_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "external_id": self.external_id,
            "locator": self.locator.value,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "RecordRef":
        updated = value.get("updated_at")
        return cls(
            external_id=str(value["external_id"]),
            locator=SourceLocator(dict(value["locator"])),
            updated_at=datetime.fromisoformat(updated) if updated else None,
        )


@dataclass(frozen=True)
class ContentUnit:
    sequence: int
    text: str
    locator: SourceLocator
    author: str | None = None
    started_at_ms: int | None = None
    ended_at_ms: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Entity:
    type: str
    external_id: str | None = None
    name: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NormalizedDocument:
    source_id: str
    external_id: str
    kind: str
    title: str | None
    occurred_at: datetime
    updated_at: datetime | None
    url: str | None
    metadata: dict[str, Any]
    units: tuple[ContentUnit, ...]
    status: str = "ready"
    entities: tuple[Entity, ...] = ()
    raw: dict[str, Any] | list[Any] | None = None

    @property
    def content_hash(self) -> str:
        material = {
            "source_id": self.source_id,
            "external_id": self.external_id,
            "kind": self.kind,
            "title": self.title,
            "occurred_at": self.occurred_at.isoformat(),
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "url": self.url,
            "metadata": self.metadata,
            "status": self.status,
            "units": [asdict(unit) for unit in self.units],
            "entities": [asdict(entity) for entity in self.entities],
        }
        return hashlib.sha256(
            json.dumps(material, sort_keys=True, default=str).encode()
        ).hexdigest()


@dataclass(frozen=True)
class Chunk:
    sequence: int
    text: str
    token_estimate: int
    start_unit_sequence: int
    end_unit_sequence: int
    locator: SourceLocator
    content_hash: str
