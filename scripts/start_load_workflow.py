#!/usr/bin/env python3
"""Start an asynchronous generic source-load Workflow."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from app.config import load_config
from app.workflow_dispatch import start_load_source


def _normalize(value: str, *, inclusive_end: bool = False) -> str:
    if len(value) == 10:
        parsed = datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
        if inclusive_end:
            parsed += timedelta(days=1)
    else:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat()


def main() -> int:
    config = load_config()
    parser = argparse.ArgumentParser()
    parser.add_argument("--adapter", default=config.default_adapter)
    parser.add_argument("--from", dest="from_datetime")
    parser.add_argument("--to", dest="to_datetime")
    parser.add_argument("--update-checkpoint", action="store_true")
    args = parser.parse_args()
    if bool(args.from_datetime) != bool(args.to_datetime):
        parser.error("--from and --to must be supplied together")

    start = _normalize(args.from_datetime) if args.from_datetime else None
    end = (
        _normalize(args.to_datetime, inclusive_end=len(args.to_datetime) == 10)
        if args.to_datetime
        else None
    )
    if start and end and datetime.fromisoformat(start) >= datetime.fromisoformat(end):
        parser.error("--from must be before --to")
    try:
        run = start_load_source(
            args.adapter,
            from_datetime=start,
            to_datetime=end,
            update_source_checkpoint=args.update_checkpoint,
        )
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps({"status": "started", **run}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
