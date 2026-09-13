"""Extension contracts for adapters, chunkers, and embedding providers."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol, Sequence

import psycopg

from app.config import AdapterConfig, EmbeddingConfig
from app.models import Chunk, ContentUnit, NormalizedDocument, RecordRef


class AdapterError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


class SourceAdapter(Protocol):
    id: str
    config: AdapterConfig

    def configured(self) -> bool: ...

    def missing_environment(self) -> list[str]: ...

    def discover(
        self,
        from_datetime: datetime | None,
        to_datetime: datetime | None,
        checkpoint: dict[str, Any] | None,
    ) -> list[RecordRef]: ...

    def fetch_batch(self, refs: Sequence[RecordRef]) -> list[Any]: ...

    def normalize(self, raw: Any) -> NormalizedDocument: ...

    def persist_projection(
        self,
        conn: psycopg.Connection,
        document_id: str,
        raw: Any,
        document: NormalizedDocument,
    ) -> None: ...


class Chunker(Protocol):
    def chunk(self, units: Sequence[ContentUnit]) -> list[Chunk]: ...


class EmbeddingProvider(Protocol):
    config: EmbeddingConfig

    def configured(self) -> bool: ...

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...

    def health(self) -> dict[str, Any]: ...
