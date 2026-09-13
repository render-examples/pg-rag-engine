"""Thin scheduler-to-Workflow dispatch."""

from __future__ import annotations

import os
from typing import Any

from render import Render


def start_load_source(
    adapter_id: str,
    *,
    from_datetime: str | None = None,
    to_datetime: str | None = None,
    update_source_checkpoint: bool = False,
    render_client: Any | None = None,
) -> dict[str, Any]:
    client = render_client or Render()
    slug = os.environ.get(
        "INTEL_WORKFLOW_TASK_SLUG", "intel-pipeline/load_source"
    ).strip()
    run = client.workflows.start_task(
        slug,
        {
            "adapter_id": adapter_id,
            "from_datetime": from_datetime,
            "to_datetime": to_datetime,
            "update_source_checkpoint": update_source_checkpoint,
        },
    )
    return {
        "task": slug,
        "run_id": str(run.id),
        "status": getattr(run, "status", "pending"),
    }
