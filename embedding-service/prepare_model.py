"""Download the manifest-selected GGUF and emit runtime server defaults."""

from __future__ import annotations

import shlex
from pathlib import Path

import yaml
from huggingface_hub import hf_hub_download

manifest = yaml.safe_load(Path("/build/rag-engine.yaml").read_text())
embedding = manifest["embedding"]
artifact = embedding["model_artifact"]

downloaded = hf_hub_download(
    repo_id=artifact["repository"],
    filename=artifact["filename"],
    revision=artifact["revision"],
)
Path("/build/model.gguf").write_bytes(Path(downloaded).read_bytes())

values = {
    "EMBEDDING_MODEL": embedding["model"],
    "EMBEDDING_POOLING": artifact["pooling"],
    "EMBEDDING_CONTEXT_TOKENS": str(artifact["context_tokens"]),
}
Path("/build/model.env").write_text(
    "\n".join(f"{key}={shlex.quote(value)}" for key, value in values.items()) + "\n"
)
