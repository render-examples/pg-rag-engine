from __future__ import annotations

import json

import app.embeddings.openai_compatible as module
from app.config import load_config
from app.embeddings.openai_compatible import OpenAICompatibleEmbeddingProvider


class Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self):
        return json.dumps(self.payload).encode()


def test_embedding_provider_uses_manifest_prefixes(monkeypatch):
    config = load_config().embedding
    observed = []

    def urlopen(request, timeout):
        body = json.loads(request.data)
        observed.extend(body["input"])
        return Response(
            {
                "data": [
                    {"index": index, "embedding": [0.1] * config.dimension}
                    for index in range(len(body["input"]))
                ]
            }
        )

    monkeypatch.setattr(module, "urlopen", urlopen)
    provider = OpenAICompatibleEmbeddingProvider(config)
    vectors = provider.embed_documents(["one", "two"])
    query = provider.embed_query("three")

    assert len(vectors) == 2
    assert len(query) == config.dimension
    assert observed == [
        f"{config.document_prefix}one",
        f"{config.document_prefix}two",
        f"{config.query_prefix}three",
    ]
