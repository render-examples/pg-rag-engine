from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from app.config import ConfigError, load_config


def test_default_manifest_loads():
    config = load_config()
    assert config.default_adapter == "json"
    assert config.adapter().kind == "json"
    assert config.embedding.dimension == 768
    assert len(config.embedding.fingerprint) == 64
    assert config.mcp.namespace == "rag"


def test_disabled_adapter_cannot_be_selected():
    config = load_config()
    with pytest.raises(ConfigError, match="disabled"):
        config.adapter("gong")


def test_cross_field_chunking_validation(tmp_path: Path):
    raw = yaml.safe_load(Path("rag-engine.yaml").read_text())
    invalid = copy.deepcopy(raw)
    invalid["content"]["chunking"]["overlap_tokens"] = 500
    path = tmp_path / "rag-engine.yaml"
    path.write_text(yaml.safe_dump(invalid))

    with pytest.raises(ConfigError, match="overlap_tokens"):
        load_config(path)
