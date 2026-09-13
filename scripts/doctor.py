#!/usr/bin/env python3
"""Validate a template deployment from manifest through MCP retrieval."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

from mcp import Client

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from app.config import load_config
from app.db import connection, fetch_one
from app.mcp.server import create_mcp, readiness
from app.registry import create_adapter, create_embedding_provider


def result(name: str, ok: bool, detail: Any) -> dict[str, Any]:
    return {"check": name, "status": "ok" if ok else "failed", "detail": detail}


async def check_mcp() -> dict[str, Any]:
    config = load_config()
    async with Client(create_mcp(config)) as client:
        tools = await client.list_tools()
        names = [tool.name for tool in tools.tools]
        response = await client.call_tool(
            f"{config.mcp.namespace}.search.keyword",
            {"query": "workflow", "limit": 1},
        )
        passages = (response.structured_content or {}).get("passages", [])
        citation_ok = not passages or bool(passages[0].get("citation"))
        return {"tools": names, "citation_ok": citation_ok}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check-workflow",
        action="store_true",
        help="Also require local Render Workflow task registration",
    )
    parser.add_argument("--workflow-port", type=int, default=8120)
    args = parser.parse_args()
    checks: list[dict[str, Any]] = []

    try:
        config = load_config()
        checks.append(result("manifest", True, str(config.path)))
    except Exception as exc:
        print(json.dumps({"status": "failed", "checks": [result("manifest", False, str(exc))]}))
        return 1

    adapter = create_adapter(config)
    missing = adapter.missing_environment()
    checks.append(result("adapter", adapter.configured(), {"id": adapter.id, "missing": missing}))

    missing_env = [name for name in config.required_environment() if not os.environ.get(name)]
    checks.append(result("environment", not missing_env, {"missing": missing_env}))

    try:
        with connection() as conn:
            database = fetch_one(
                conn,
                """
                SELECT
                  (SELECT count(*) FROM schema_migrations)::int AS migrations,
                  (SELECT count(*) FROM documents)::int AS documents,
                  (SELECT count(*) FROM chunks)::int AS chunks
                """,
            )
        checks.append(result("database", True, database))
    except Exception as exc:
        checks.append(result("database", False, str(exc)))

    ready = readiness(config)
    checks.append(result("readiness", ready["status"] == "ok", ready))

    try:
        provider = create_embedding_provider(config)
        vector = provider.embed_query("template health check")
        checks.append(
            result(
                "embedding",
                len(vector) == config.embedding.dimension,
                {"dimension": len(vector), "fingerprint": config.embedding.fingerprint},
            )
        )
    except Exception as exc:
        checks.append(result("embedding", False, str(exc)))

    try:
        mcp_result = asyncio.run(check_mcp())
        checks.append(result("mcp", mcp_result["citation_ok"], mcp_result))
    except Exception as exc:
        checks.append(result("mcp", False, str(exc)))

    if args.check_workflow:
        try:
            completed = subprocess.run(
                [
                    os.environ.get("RENDER_CLI", "render"),
                    "workflows",
                    "tasks",
                    "list",
                    "--local",
                    "--port",
                    str(args.workflow_port),
                    "-o",
                    "json",
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=15,
            )
            tasks = [row["name"] for row in json.loads(completed.stdout)]
            expected = {"load_source", "process_batch", "embed_pending"}
            checks.append(result("workflow", expected.issubset(tasks), {"tasks": tasks}))
        except Exception as exc:
            checks.append(result("workflow", False, str(exc)))

    ok = all(check["status"] == "ok" for check in checks)
    print(json.dumps({"status": "ok" if ok else "failed", "checks": checks}, indent=2, default=str))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
