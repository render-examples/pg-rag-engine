"""Minimal Gong v2 API client used by the Gong adapter."""

from __future__ import annotations

import base64
import json
import os
import time
from datetime import datetime
from typing import Any, Iterator
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.contracts import AdapterError


class GongClient:
    def __init__(self, rate_limit: float = 3.0) -> None:
        self.base_url = os.environ.get("GONG_BASE_URL", "").strip().rstrip("/")
        self.access_key = os.environ.get("GONG_ACCESS_KEY", "").strip()
        self.secret = os.environ.get("GONG_ACCESS_KEY_SECRET", "").strip()
        self.minimum_interval = 1.0 / rate_limit if rate_limit > 0 else 0
        self.last_request_at = 0.0
        self.request_count = 0

    def configured(self) -> bool:
        return bool(self.base_url and self.access_key and self.secret)

    @staticmethod
    def _iso(value: datetime) -> str:
        return value.isoformat()

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self.last_request_at
        if elapsed < self.minimum_interval:
            time.sleep(self.minimum_interval - elapsed)
        self.last_request_at = time.monotonic()

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.configured():
            raise AdapterError("Gong credentials are not configured", retryable=False)
        url = f"{self.base_url}{path}"
        if params:
            url += f"?{urlencode(params)}"
        encoded = json.dumps(body).encode() if body is not None else None
        token = base64.b64encode(
            f"{self.access_key}:{self.secret}".encode()
        ).decode()
        for attempt in range(5):
            self._throttle()
            self.request_count += 1
            request = Request(
                url,
                method=method,
                data=encoded,
                headers={
                    "Authorization": f"Basic {token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
            try:
                with urlopen(request, timeout=60) as response:
                    payload = json.loads(response.read().decode())
                if not isinstance(payload, dict):
                    raise AdapterError("Gong returned non-object JSON", retryable=False)
                return payload
            except HTTPError as exc:
                detail = exc.read().decode(errors="replace") if exc.fp else ""
                retryable = exc.code == 429 or 500 <= exc.code < 600
                if not retryable or attempt == 4:
                    raise AdapterError(
                        f"Gong HTTP {exc.code}: {detail[:300]}",
                        retryable=retryable,
                    ) from exc
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                time.sleep(float(retry_after) if retry_after else 2**attempt)
            except (URLError, TimeoutError) as exc:
                if attempt == 4:
                    raise AdapterError(f"Gong unavailable: {exc}") from exc
                time.sleep(2**attempt)
            except json.JSONDecodeError as exc:
                raise AdapterError("Gong returned invalid JSON", retryable=False) from exc
        raise AdapterError("Gong request exhausted retries")

    def _paginate(
        self,
        method: str,
        path: str,
        result_key: str,
        *,
        params: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Iterator[dict[str, Any]]:
        cursor: str | None = None
        seen: set[str] = set()
        while True:
            page_params = dict(params or {})
            page_body = dict(body or {})
            if cursor:
                if method == "GET":
                    page_params["cursor"] = cursor
                else:
                    page_body["cursor"] = cursor
            payload = self._request(
                method,
                path,
                params=page_params or None,
                body=page_body if method != "GET" else None,
            )
            yield from payload.get(result_key, [])
            cursor = (payload.get("records") or {}).get("cursor")
            if not cursor:
                return
            if cursor in seen:
                raise AdapterError("Gong repeated a pagination cursor", retryable=False)
            seen.add(cursor)

    def list_calls(
        self, from_datetime: datetime, to_datetime: datetime
    ) -> Iterator[dict[str, Any]]:
        return self._paginate(
            "GET",
            "/v2/calls",
            "calls",
            params={
                "fromDateTime": self._iso(from_datetime),
                "toDateTime": self._iso(to_datetime),
            },
        )

    def extensive(self, call_ids: list[str]) -> list[dict[str, Any]]:
        return list(
            self._paginate(
                "POST",
                "/v2/calls/extensive",
                "calls",
                body={
                    "filter": {"callIds": call_ids},
                    "contentSelector": {
                        "context": "Extended",
                        "exposedFields": {
                            "parties": True,
                            "content": {"topics": True, "trackers": True},
                            "interaction": {"speakers": True},
                        },
                    },
                },
            )
        )

    def transcripts(self, call_ids: list[str]) -> list[dict[str, Any]]:
        return list(
            self._paginate(
                "POST",
                "/v2/calls/transcript",
                "callTranscripts",
                body={"filter": {"callIds": call_ids}},
            )
        )
