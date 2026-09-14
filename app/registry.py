"""Explicit extension registry used by Workflows, MCP, and doctor."""

from __future__ import annotations

from collections.abc import Callable

from app.config import AdapterConfig, EmbeddingConfig, RagEngineConfig
from app.contracts import EmbeddingProvider, SourceAdapter

AdapterFactory = Callable[[AdapterConfig], SourceAdapter]
EmbeddingFactory = Callable[[EmbeddingConfig], EmbeddingProvider]


def _json_adapter(config: AdapterConfig) -> SourceAdapter:
    from app.adapters.json.adapter import JsonAdapter

    return JsonAdapter(config)


def _gong_adapter(config: AdapterConfig) -> SourceAdapter:
    from app.adapters.gong.adapter import GongAdapter

    return GongAdapter(config)


def _openai_compatible(config: EmbeddingConfig) -> EmbeddingProvider:
    from app.embeddings.openai_compatible import OpenAICompatibleEmbeddingProvider

    return OpenAICompatibleEmbeddingProvider(config)


ADAPTERS: dict[str, AdapterFactory] = {
    "json": _json_adapter,
    "gong": _gong_adapter,
}

EMBEDDING_PROVIDERS: dict[str, EmbeddingFactory] = {
    "openai-compatible-private": _openai_compatible,
}


def create_adapter(config: RagEngineConfig, adapter_id: str | None = None) -> SourceAdapter:
    adapter_config = config.adapter(adapter_id)
    try:
        factory = ADAPTERS[adapter_config.kind]
    except KeyError as exc:
        raise ValueError(f"unregistered adapter kind: {adapter_config.kind}") from exc
    return factory(adapter_config)


def create_embedding_provider(config: RagEngineConfig) -> EmbeddingProvider:
    try:
        factory = EMBEDDING_PROVIDERS[config.embedding.provider]
    except KeyError as exc:
        raise ValueError(
            f"unregistered embedding provider: {config.embedding.provider}"
        ) from exc
    return factory(config.embedding)
