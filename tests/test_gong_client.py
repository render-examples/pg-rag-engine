from __future__ import annotations

from datetime import datetime, timezone

import pytest

import app.adapters.gong.client as module
from app.adapters.gong.client import GongClient
from app.contracts import AdapterError


@pytest.fixture
def client(monkeypatch) -> GongClient:
    monkeypatch.setenv("GONG_BASE_URL", "https://example.gong.io")
    monkeypatch.setenv("GONG_ACCESS_KEY", "key")
    monkeypatch.setenv("GONG_ACCESS_KEY_SECRET", "secret")
    return GongClient(rate_limit=0)


def test_pagination_passes_cursor(client, monkeypatch):
    observed = []
    responses = [
        {"calls": [{"id": "1"}], "records": {"cursor": "next"}},
        {"calls": [{"id": "2"}], "records": {}},
    ]

    def request(method, path, *, params=None, body=None):
        observed.append(dict(params or {}))
        return responses.pop(0)

    monkeypatch.setattr(client, "_request", request)
    rows = list(
        client.list_calls(
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 1, 2, tzinfo=timezone.utc),
        )
    )
    assert [row["id"] for row in rows] == ["1", "2"]
    assert observed[1]["cursor"] == "next"


def test_repeated_cursor_fails(client, monkeypatch):
    monkeypatch.setattr(
        client,
        "_request",
        lambda *args, **kwargs: {"calls": [], "records": {"cursor": "same"}},
    )
    with pytest.raises(AdapterError, match="repeated"):
        list(
            client.list_calls(
                datetime(2026, 1, 1, tzinfo=timezone.utc),
                datetime(2026, 1, 2, tzinfo=timezone.utc),
            )
        )


def test_throttle_enforces_rate(client, monkeypatch):
    client.minimum_interval = 0.5
    client.last_request_at = 10.0
    times = iter([10.1, 10.5])
    sleeps = []
    monkeypatch.setattr(module.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(module.time, "sleep", sleeps.append)
    client._throttle()
    assert sleeps == pytest.approx([0.4])
