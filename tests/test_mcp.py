from __future__ import annotations

import asyncio
from dataclasses import replace

import httpx

from app.config import load_config
from app.mcp.server import create_app, create_mcp, readiness
from mcp import Client


def test_generic_mcp_tool_surface_and_search():
    async def run():
        async with Client(create_mcp(load_config())) as client:
            listed = await client.list_tools()
            assert [tool.name for tool in listed.tools] == [
                "rag.schema.describe",
                "rag.sources.list",
                "rag.documents.list",
                "rag.documents.get",
                "rag.search.keyword",
                "rag.search.semantic",
                "rag.search.hybrid",
            ]
            keyword = await client.call_tool(
                "rag.search.keyword", {"query": "budget", "source_id": "json"}
            )
            passage = keyword.structured_content["passages"][0]
            assert passage["external_id"] == "sales-note-017"
            assert passage["citation"]["locator"]

            semantic = await client.call_tool(
                "rag.search.semantic",
                {"query": "customer onboarding difficulties", "limit": 3},
            )
            assert semantic.structured_content["passages"]

            hybrid = await client.call_tool(
                "rag.search.hybrid",
                {"query": "webhook queue delay", "limit": 3},
            )
            assert hybrid.structured_content["passages"][0]["external_id"] == (
                "support-incident-042"
            )

    asyncio.run(run())


def test_documents_list_and_get_include_citations():
    async def run():
        async with Client(create_mcp(load_config())) as client:
            listed = await client.call_tool(
                "rag.documents.list", {"source_id": "json", "limit": 1}
            )
            document = listed.structured_content["documents"][0]
            assert document["citation"]["source_id"] == "json"
            detail = await client.call_tool(
                "rag.documents.get", {"document_id": document["id"]}
            )
            assert detail.structured_content["units"]
            assert detail.structured_content["citation"]["document_id"] == document["id"]

    asyncio.run(run())


def test_gong_adapter_adds_optional_domain_tools():
    async def run():
        config = load_config()
        adapters = {
            **config.adapters,
            "gong": replace(config.adapters["gong"], enabled=True),
        }
        async with Client(create_mcp(replace(config, adapters=adapters))) as client:
            listed = await client.list_tools()
            names = {tool.name for tool in listed.tools}
            assert {"rag.gong.calls.list", "rag.gong.calls.get"} <= names

    asyncio.run(run())


def test_manifest_namespace_controls_tool_names():
    async def run():
        config = load_config()
        renamed = replace(config, mcp=replace(config.mcp, namespace="knowledge"))
        async with Client(create_mcp(renamed)) as client:
            listed = await client.list_tools()
            assert all(tool.name.startswith("knowledge.") for tool in listed.tools)

    asyncio.run(run())


def test_http_auth_and_readiness():
    config = load_config()
    assert readiness(config)["status"] == "ok"

    async def run():
        transport = httpx.ASGITransport(app=create_app(config))
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            return await client.post("/mcp", json={})

    response = asyncio.run(run())
    assert response.status_code == 401


def test_readiness_rejects_manifest_dimension_mismatch():
    config = load_config()
    mismatched = replace(
        config,
        embedding=replace(config.embedding, dimension=384),
    )
    result = readiness(mismatched)
    assert result["status"] == "unhealthy"
    assert "dimension" in result["error"]
