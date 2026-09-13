#!/usr/bin/env python3
"""Scheduled dispatcher for the configured default source adapter."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from app.config import load_config
from app.workflow_dispatch import start_load_source


def main() -> int:
    config = load_config()
    try:
        run = start_load_source(config.default_adapter)
    except Exception as exc:
        print(
            json.dumps(
                {
                    "step": "dispatch_load_source",
                    "status": "failed",
                    "error": str(exc),
                }
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps({"step": "dispatch_load_source", "status": "started", **run}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
