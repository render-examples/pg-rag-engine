#!/usr/bin/env python3
"""Embed pending chunks for the active manifest profile."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from app.config import load_config
from app.pipeline.embed import embed_pending
from app.registry import create_embedding_provider


def main() -> int:
    config = load_config()
    result = embed_pending(config, create_embedding_provider(config))
    print(json.dumps(result))
    return 1 if result["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
