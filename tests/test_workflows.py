from __future__ import annotations

from types import SimpleNamespace

from app.workflow_dispatch import start_load_source
from app.workflows.main import app


def test_generic_workflow_tasks_register():
    assert set(app._registry.get_task_names()) == {
        "load_source",
        "process_batch",
        "embed_pending",
    }
    process = app._registry.get_task("process_batch")
    assert process.options.retry.max_retries == 4
    assert process.options.timeout_seconds == 1800
    load = app._registry.get_task("load_source")
    assert load.options.timeout_seconds == 86400


def test_scheduler_dispatches_generic_adapter():
    observed = {}

    class Workflows:
        def start_task(self, slug, inputs):
            observed.update(slug=slug, inputs=inputs)
            return SimpleNamespace(id="run-1", status="pending")

    result = start_load_source(
        "json",
        render_client=SimpleNamespace(workflows=Workflows()),
    )
    assert result["run_id"] == "run-1"
    assert observed == {
        "slug": "intel-pipeline/load_source",
        "inputs": {
            "adapter_id": "json",
            "from_datetime": None,
            "to_datetime": None,
            "update_source_checkpoint": False,
        },
    }
