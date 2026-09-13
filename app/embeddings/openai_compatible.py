"""OpenAI-compatible private embedding provider."""

from __future__ import annotations

import json
import math
import os
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.config import EmbeddingConfig
from app.contracts import AdapterError


class OpenAICompatibleEmbeddingProvider:
    def __init__(self, config: EmbeddingConfig) -> None:
        self.config = config
        endpoint = os.environ.get(config.endpoint_env, "").strip().rstrip("/")
        self.endpoint = (
            endpoint if not endpoint or "://" in endpoint else f"http://{endpoint}"
        )
        self.api_key = os.environ.get(config.api_key_env, "").strip()

    def configured(self) -> bool:
        return bool(self.endpoint and self.api_key)

    def _request(self, texts: list[str], attempts: int = 4) -> list[list[float]]:
        if not self.configured():
            raise RuntimeError(
                f"{self.config.endpoint_env} and {self.config.api_key_env} are required"
            )
        body = json.dumps({"model": self.config.model, "input": texts}).encode()
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        for attempt in range(attempts):
            request = Request(
                f"{self.endpoint}/v1/embeddings",
                method="POST",
                data=body,
                headers=headers,
            )
            try:
                with urlopen(request, timeout=120) as response:
                    payload = json.loads(response.read().decode())
                return self._parse(payload, len(texts))
            except HTTPError as exc:
                detail = exc.read().decode(errors="replace") if exc.fp else ""
                if exc.code not in (429, 500, 502, 503, 504) or attempt == attempts - 1:
                    raise AdapterError(
                        f"embedding HTTP {exc.code}: {detail[:300]}",
                        retryable=exc.code in (429, 500, 502, 503, 504),
                    ) from exc
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                time.sleep(float(retry_after) if retry_after else 2**attempt)
            except (URLError, TimeoutError) as exc:
                if attempt == attempts - 1:
                    raise AdapterError(
                        f"embedding service unavailable: {exc}", retryable=True
                    ) from exc
                time.sleep(2**attempt)
        raise AdapterError("embedding request exhausted retries")

    def _parse(self, payload: dict[str, Any], count: int) -> list[list[float]]:
        rows = payload.get("data")
        if not isinstance(rows, list) or len(rows) != count:
            raise AdapterError(f"expected {count} embedding rows", retryable=False)
        ordered = sorted(rows, key=lambda row: int(row["index"]))
        vectors: list[list[float]] = []
        for index, row in enumerate(ordered):
            if int(row["index"]) != index:
                raise AdapterError("embedding indexes are invalid", retryable=False)
            vector = [float(value) for value in row["embedding"]]
            if len(vector) != self.config.dimension:
                raise AdapterError(
                    f"expected dimension {self.config.dimension}, got {len(vector)}",
                    retryable=False,
                )
            if not all(math.isfinite(value) for value in vector):
                raise AdapterError("embedding contains non-finite values", retryable=False)
            vectors.append(vector)
        return vectors

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for offset in range(0, len(texts), self.config.batch_size):
            batch = texts[offset : offset + self.config.batch_size]
            vectors.extend(
                self._request(
                    [f"{self.config.document_prefix}{text.strip()}" for text in batch]
                )
            )
        return vectors

    def embed_query(self, text: str) -> list[float]:
        query = text.strip()
        if not query:
            raise ValueError("query must not be empty")
        return self._request([f"{self.config.query_prefix}{query}"])[0]

    def health(self) -> dict[str, Any]:
        if not self.configured():
            return {"status": "unconfigured"}
        request = Request(
            f"{self.endpoint}/health",
            headers={"Authorization": f"Bearer {self.api_key}"},
        )
        try:
            with urlopen(request, timeout=10) as response:
                payload = json.loads(response.read().decode())
            probe = self.embed_query("embedding health check")
            return {
                "status": "ok",
                "service": payload,
                "model": self.config.model,
                "dimension": len(probe),
                "fingerprint": self.config.fingerprint,
            }
        except Exception as exc:
            return {"status": "unhealthy", "error": str(exc)}
