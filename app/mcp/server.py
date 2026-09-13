"""Authenticated stateless Streamable HTTP MCP service."""

from __future__ import annotations

import hmac
import os
from pathlib import Path
from typing import Any

import uvicorn
from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from app.config import IntelConfig, load_config
from app.db import connection, fetch_one
from app.mcp.tools import register_core_tools
from app.registry import create_embedding_provider


def create_mcp(config: IntelConfig) -> MCPServer:
    server = MCPServer(
        name=f"{config.project_name} MCP",
        version="1.0.0",
        instructions=(
            "Use search.hybrid by default. Every document and passage includes "
            "a canonical citation."
        ),
    )
    register_core_tools(server, config)
    for adapter in config.adapters.values():
        if adapter.enabled and adapter.kind == "gong":
            from app.adapters.gong.tools import register_gong_tools

            register_gong_tools(server, config.mcp.namespace)
    return server


def readiness(config: IntelConfig) -> dict[str, Any]:
    try:
        with connection() as conn:
            row = fetch_one(
                conn,
                """
                SELECT
                  ep.id, ep.dimension, ep.fingerprint,
                  format_type(a.atttypid, a.atttypmod) AS vector_type
                FROM embedding_profiles ep
                CROSS JOIN pg_attribute a
                JOIN pg_class c ON c.oid = a.attrelid
                WHERE ep.active
                  AND c.relname = 'chunks'
                  AND a.attname = 'embedding'
                """,
            )
        expected_type = f"vector({config.embedding.dimension})"
        if not row:
            raise RuntimeError("active embedding profile is missing")
        if row["dimension"] != config.embedding.dimension:
            raise RuntimeError("database embedding profile dimension mismatch")
        if row["fingerprint"] != config.embedding.fingerprint:
            raise RuntimeError("database embedding profile fingerprint mismatch")
        if row["vector_type"] != expected_type:
            raise RuntimeError(
                f"database vector type is {row['vector_type']}, expected {expected_type}"
            )
        embedding = create_embedding_provider(config).health()
        if embedding["status"] != "ok":
            raise RuntimeError(f"embedding service is {embedding['status']}")
        return {
            "status": "ok",
            "embedding_profile": config.embedding.profile_id,
            "dimension": config.embedding.dimension,
        }
    except Exception as exc:
        return {"status": "unhealthy", "error": str(exc)}


def create_app(config: IntelConfig):
    mcp = create_mcp(config)
    app = mcp.streamable_http_app(
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
        max_request_body_size=1_048_576,
        host="0.0.0.0",
    )
    token = os.environ.get(config.mcp.token_env, "").strip()
    if not token:
        raise RuntimeError(f"{config.mcp.token_env} is required")

    async def health(_request: Request) -> JSONResponse:
        result = readiness(config)
        public = result if result["status"] == "ok" else {"status": "unhealthy"}
        return JSONResponse(public, status_code=200 if result["status"] == "ok" else 503)

    app.routes.insert(0, Route("/health", health, methods=["GET"]))

    class BearerMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            if request.url.path.startswith("/mcp"):
                header = request.headers.get("authorization", "")
                provided = header[7:] if header.startswith("Bearer ") else ""
                if not hmac.compare_digest(provided, token):
                    return JSONResponse({"error": "unauthorized"}, status_code=401)
            return await call_next(request)

    app.add_middleware(BearerMiddleware)
    return app


config = load_config()
app = create_app(config)


def main() -> None:
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "3011")),
        log_level="info",
    )


if __name__ == "__main__":
    main()
