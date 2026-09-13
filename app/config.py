"""Validated application configuration loaded from intel.yaml."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema
import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = ROOT / "intel.yaml"
SCHEMA_PATH = ROOT / "intel.schema.json"


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class AdapterConfig:
    id: str
    kind: str
    enabled: bool
    batch_size: int
    sequential: bool
    required_env: tuple[str, ...] = ()
    config: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ChunkingConfig:
    strategy: str
    target_tokens: int
    overlap_tokens: int
    minimum_tokens: int


@dataclass(frozen=True)
class EmbeddingArtifact:
    repository: str
    filename: str
    revision: str
    pooling: str
    context_tokens: int


@dataclass(frozen=True)
class EmbeddingConfig:
    profile_id: str
    provider: str
    model: str
    dimension: int
    endpoint_env: str
    api_key_env: str
    document_prefix: str
    query_prefix: str
    batch_size: int
    normalized: bool
    model_artifact: EmbeddingArtifact

    @property
    def fingerprint(self) -> str:
        material = json.dumps(
            {
                "provider": self.provider,
                "model": self.model,
                "dimension": self.dimension,
                "document_prefix": self.document_prefix,
                "query_prefix": self.query_prefix,
                "normalized": self.normalized,
                "artifact": self.model_artifact.__dict__,
            },
            sort_keys=True,
        )
        return hashlib.sha256(material.encode()).hexdigest()


@dataclass(frozen=True)
class McpConfig:
    namespace: str
    token_env: str
    filters: tuple[str, ...]


@dataclass(frozen=True)
class IntelConfig:
    version: int
    project_name: str
    resource_prefix: str
    default_adapter: str
    adapters: dict[str, AdapterConfig]
    full_text_language: str
    chunking: ChunkingConfig
    embedding: EmbeddingConfig
    mcp: McpConfig
    path: Path

    def adapter(self, adapter_id: str | None = None) -> AdapterConfig:
        resolved = adapter_id or self.default_adapter
        try:
            adapter = self.adapters[resolved]
        except KeyError as exc:
            raise ConfigError(f"unknown adapter: {resolved}") from exc
        if not adapter.enabled:
            raise ConfigError(f"adapter is disabled: {resolved}")
        return adapter

    def required_environment(self, adapter_id: str | None = None) -> tuple[str, ...]:
        adapter = self.adapter(adapter_id)
        return (
            *adapter.required_env,
            self.embedding.endpoint_env,
            self.embedding.api_key_env,
            self.mcp.token_env,
            "DATABASE_URL",
        )


def load_config(path: str | Path | None = None) -> IntelConfig:
    config_path = Path(
        path or os.environ.get("INTEL_CONFIG_PATH", DEFAULT_CONFIG_PATH)
    ).resolve()
    try:
        raw = yaml.safe_load(config_path.read_text())
        schema = json.loads(SCHEMA_PATH.read_text())
        jsonschema.validate(raw, schema)
    except (OSError, yaml.YAMLError, json.JSONDecodeError) as exc:
        raise ConfigError(f"cannot load configuration: {exc}") from exc
    except jsonschema.ValidationError as exc:
        location = ".".join(str(part) for part in exc.absolute_path) or "<root>"
        raise ConfigError(f"invalid configuration at {location}: {exc.message}") from exc

    default_adapter = raw["default_adapter"]
    if default_adapter not in raw["adapters"]:
        raise ConfigError("default_adapter must reference an adapter")
    chunking = ChunkingConfig(**raw["content"]["chunking"])
    if chunking.minimum_tokens > chunking.target_tokens:
        raise ConfigError("chunking.minimum_tokens cannot exceed target_tokens")
    if chunking.overlap_tokens >= chunking.target_tokens:
        raise ConfigError("chunking.overlap_tokens must be less than target_tokens")

    adapters = {
        adapter_id: AdapterConfig(
            id=adapter_id,
            kind=value["kind"],
            enabled=value["enabled"],
            batch_size=value["batch_size"],
            sequential=value["sequential"],
            required_env=tuple(value.get("required_env", [])),
            config=dict(value.get("config", {})),
        )
        for adapter_id, value in raw["adapters"].items()
    }
    embedding_raw = raw["embedding"]
    embedding = EmbeddingConfig(
        **{
            key: value
            for key, value in embedding_raw.items()
            if key != "model_artifact"
        },
        model_artifact=EmbeddingArtifact(**embedding_raw["model_artifact"]),
    )
    return IntelConfig(
        version=raw["version"],
        project_name=raw["project"]["name"],
        resource_prefix=raw["project"]["resource_prefix"],
        default_adapter=default_adapter,
        adapters=adapters,
        full_text_language=raw["content"]["full_text_language"],
        chunking=chunking,
        embedding=embedding,
        mcp=McpConfig(
            namespace=raw["mcp"]["namespace"],
            token_env=raw["mcp"]["token_env"],
            filters=tuple(raw["mcp"]["filters"]),
        ),
        path=config_path,
    )
