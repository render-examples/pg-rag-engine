#!/usr/bin/env python3
"""Run the generic source pipeline directly without Render Workflows."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from app.config import load_config
from app.pipeline.embed import embed_pending
from app.pipeline.load import (
    create_sync_run,
    finish_sync_run,
    process_batch,
    resolve_window,
    update_checkpoint,
)
from app.registry import create_adapter, create_embedding_provider


def _datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def main() -> int:
    parser = argparse.ArgumentParser(description="Load data through a configured adapter")
    parser.add_argument("--adapter")
    parser.add_argument("--from", dest="from_datetime")
    parser.add_argument("--to", dest="to_datetime")
    parser.add_argument("--update-checkpoint", action="store_true")
    parser.add_argument("--skip-embeddings", action="store_true")
    args = parser.parse_args()

    config = load_config()
    adapter = create_adapter(config, args.adapter)
    if not adapter.configured():
        print(
            f"adapter {adapter.id} is not configured: "
            f"{', '.join(adapter.missing_environment())}",
            file=sys.stderr,
        )
        return 2
    start, end, checkpoint = resolve_window(
        config,
        adapter.id,
        _datetime(args.from_datetime),
        _datetime(args.to_datetime),
    )
    mode = "range" if args.from_datetime or args.to_datetime else "incremental"
    run_id = create_sync_run(adapter.id, mode, start, end)
    counts = {"documents": 0, "changed": 0, "failed": 0, "batches": 0}
    try:
        refs = adapter.discover(start, end, checkpoint)
        for offset in range(0, len(refs), adapter.config.batch_size):
            result = process_batch(
                config,
                adapter,
                refs[offset : offset + adapter.config.batch_size],
            )
            counts["documents"] += result["documents"]
            counts["changed"] += result["changed"]
            counts["failed"] += result["failed"]
            counts["batches"] += 1
            if result["failed"]:
                raise RuntimeError(str(result))
        embedding = (
            {"skipped": True}
            if args.skip_embeddings
            else embed_pending(config, create_embedding_provider(config))
        )
        if embedding.get("failed"):
            raise RuntimeError(str(embedding))
        should_checkpoint = mode == "incremental" or args.update_checkpoint
        if should_checkpoint:
            update_checkpoint(adapter.id, end)
        finish_sync_run(run_id, "complete", {**counts, "embedding": embedding})
        print(
            json.dumps(
                {
                    "run_id": run_id,
                    "adapter": adapter.id,
                    **counts,
                    "embedding": embedding,
                    "checkpoint_updated": should_checkpoint,
                }
            )
        )
        return 0
    except Exception as exc:
        finish_sync_run(run_id, "failed", counts, str(exc))
        print(json.dumps({"run_id": run_id, "error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
