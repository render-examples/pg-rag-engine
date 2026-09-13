"""Generic Render Workflow tasks for every registered source adapter."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from render import Retry, TaskContext, Workflows

from app.config import load_config
from app.pipeline.embed import embed_pending
from app.pipeline.load import (
    create_sync_run,
    deserialize_refs,
    finish_sync_run,
    process_batch,
    resolve_window,
    update_checkpoint,
)
from app.registry import create_adapter, create_embedding_provider

app = Workflows(
    default_retry=Retry(
        max_retries=3,
        wait_duration_ms=5_000,
        backoff_scaling=2.0,
    ),
    default_timeout=7_200,
    default_plan="starter",
)


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


@app.task(
    name="process_batch",
    retry=Retry(max_retries=4, wait_duration_ms=5_000, backoff_scaling=2.0),
    timeout_seconds=1_800,
)
def process_batch_task(
    ctx: TaskContext,
    adapter_id: str,
    record_refs: list[dict[str, Any]],
) -> dict[str, Any]:
    config = load_config()
    adapter = create_adapter(config, adapter_id)
    result = process_batch(config, adapter, deserialize_refs(record_refs))
    if result["failed"]:
        raise RuntimeError(f"adapter batch failed: {result}")
    return result


@app.task(
    name="embed_pending",
    retry=Retry(max_retries=3, wait_duration_ms=10_000, backoff_scaling=2.0),
    timeout_seconds=3_600,
)
def embed_pending_task(
    ctx: TaskContext,
    profile_id: str | None = None,
) -> dict[str, Any]:
    config = load_config()
    if profile_id and profile_id != config.embedding.profile_id:
        raise ValueError(f"unknown embedding profile: {profile_id}")
    result = embed_pending(config, create_embedding_provider(config))
    if result["failed"]:
        raise RuntimeError(f"embedding failed: {result}")
    return result


@app.task(
    name="load_source",
    retry=Retry(max_retries=2, wait_duration_ms=30_000, backoff_scaling=2.0),
    timeout_seconds=86_400,
)
async def load_source(
    ctx: TaskContext,
    adapter_id: str | None = None,
    from_datetime: str | None = None,
    to_datetime: str | None = None,
    update_source_checkpoint: bool = False,
) -> dict[str, Any]:
    config = load_config()
    adapter_config = config.adapter(adapter_id)
    adapter = create_adapter(config, adapter_config.id)
    if not adapter.configured():
        raise RuntimeError(
            f"adapter {adapter.id} is not configured; missing "
            f"{', '.join(adapter.missing_environment())}"
        )

    resolved_from, resolved_to, checkpoint = resolve_window(
        config,
        adapter.id,
        _parse_datetime(from_datetime),
        _parse_datetime(to_datetime),
    )
    incremental = from_datetime is None and to_datetime is None
    mode = "incremental" if incremental else "range"
    run_id = create_sync_run(adapter.id, mode, resolved_from, resolved_to)
    counts: dict[str, Any] = {"batches": 0, "documents": 0, "changed": 0}

    try:
        refs = adapter.discover(resolved_from, resolved_to, checkpoint)
        batches = [
            refs[offset : offset + adapter_config.batch_size]
            for offset in range(0, len(refs), adapter_config.batch_size)
        ]
        results: list[dict[str, Any]] = []
        if adapter_config.sequential:
            for batch in batches:
                results.append(
                    await ctx.run(
                        process_batch_task,
                        adapter.id,
                        [ref.to_dict() for ref in batch],
                    )
                )
        else:
            concurrency = max(
                1, min(int(adapter_config.config.get("concurrency", 4)), 20)
            )
            for offset in range(0, len(batches), concurrency):
                group = batches[offset : offset + concurrency]
                results.extend(
                    await asyncio.gather(
                        *(
                            ctx.run(
                                process_batch_task,
                                adapter.id,
                                [ref.to_dict() for ref in batch],
                            )
                            for batch in group
                        )
                    )
                )

        counts = {
            "discovered": len(refs),
            "batches": len(results),
            "documents": sum(result["documents"] for result in results),
            "changed": sum(result["changed"] for result in results),
        }
        embedding = await ctx.run(embed_pending_task, config.embedding.profile_id)
        should_checkpoint = incremental or update_source_checkpoint
        if should_checkpoint:
            update_checkpoint(adapter.id, resolved_to)
        finish_sync_run(run_id, "complete", {**counts, "embedding": embedding})
        return {
            "run_id": run_id,
            "adapter_id": adapter.id,
            "from_datetime": resolved_from.isoformat(),
            "to_datetime": resolved_to.isoformat(),
            "checkpoint_updated": should_checkpoint,
            **counts,
            "embedding": embedding,
        }
    except Exception as exc:
        finish_sync_run(run_id, "failed", counts, str(exc))
        raise


if __name__ == "__main__":
    app.start()
