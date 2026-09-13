from datetime import datetime, timedelta, timezone

from app.config import load_config
from app.db import connection, fetch_one
from app.pipeline.load import resolve_window, update_checkpoint


def test_checkpoint_is_monotonic_and_never_future():
    now = datetime.now(timezone.utc)
    update_checkpoint("json", now + timedelta(days=30))
    with connection() as conn:
        first = fetch_one(
            conn,
            "SELECT watermark_at FROM source_checkpoints WHERE source_id = 'json'",
        )["watermark_at"]
    assert first <= datetime.now(timezone.utc)

    update_checkpoint("json", now - timedelta(days=30))
    with connection() as conn:
        second = fetch_one(
            conn,
            "SELECT watermark_at FROM source_checkpoints WHERE source_id = 'json'",
        )["watermark_at"]
    assert second >= first


def test_window_resolution_handles_existing_checkpoint():
    start, end, _ = resolve_window(load_config(), "json", None, None)
    assert start < end
    assert end <= datetime.now(timezone.utc)
