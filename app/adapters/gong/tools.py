"""Optional Gong-specific MCP projection tools."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from app.db import connection, fetch_all, fetch_one


def register_gong_tools(server: MCPServer, namespace: str) -> None:
    read_only = ToolAnnotations(readOnlyHint=True)

    @server.tool(
        name=f"{namespace}.gong.calls.list",
        description="List rich Gong call projections linked to canonical documents.",
        annotations=read_only,
        structured_output=True,
    )
    def gong_calls_list(
        scope: str | None = None,
        account_name: str | None = None,
        deal_stage: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        with connection() as conn:
            rows = fetch_all(
                conn,
                """
                SELECT
                  id, source_id, external_id, title, url, occurred_at,
                  duration_sec, direction, scope, primary_user_email,
                  participants, crm, topics, trackers
                FROM gong_call_context gcc
                WHERE (%s::text IS NULL OR gcc.scope = %s)
                  AND (
                    %s::text IS NULL OR EXISTS (
                      SELECT 1 FROM gong_crm_associations crm
                      WHERE crm.document_id = gcc.id
                        AND lower(crm.account_name) = lower(%s)
                    )
                  )
                  AND (
                    %s::text IS NULL OR EXISTS (
                      SELECT 1 FROM gong_crm_associations crm
                      WHERE crm.document_id = gcc.id
                        AND lower(crm.deal_stage) = lower(%s)
                    )
                  )
                ORDER BY occurred_at DESC
                LIMIT %s
                """,
                (
                    scope,
                    scope,
                    account_name,
                    account_name,
                    deal_stage,
                    deal_stage,
                    min(max(limit, 1), 100),
                ),
            )
        return {
            "calls": [
                {
                    **row,
                    "id": str(row["id"]),
                    "citation": {
                        "source_id": row["source_id"],
                        "document_id": str(row["id"]),
                        "external_id": row["external_id"],
                        "url": row["url"],
                    },
                }
                for row in rows
            ]
        }

    @server.tool(
        name=f"{namespace}.gong.calls.get",
        description="Get one rich Gong call projection with transcript and CRM context.",
        annotations=read_only,
        structured_output=True,
    )
    def gong_calls_get(document_id: str) -> dict[str, Any]:
        with connection() as conn:
            row = fetch_one(
                conn, "SELECT * FROM gong_call_context WHERE id = %s", (document_id,)
            )
        if not row:
            raise ValueError(f"Gong call not found: {document_id}")
        return {
            "call": {**row, "id": str(row["id"])},
            "citation": {
                "source_id": row["source_id"],
                "document_id": str(row["id"]),
                "external_id": row["external_id"],
                "url": row["url"],
            },
        }
