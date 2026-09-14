# RAG Engine
Memory and hybrid search for agents, built on Render.

A fork-and-deploy template for loading external data through Render Workflows,
normalizing it into Postgres and pgvector, generating embeddings over Render's private
network, and exposing citation-backed retrieval through an MCP.



```text
Source adapter → Render Workflow → Postgres/pgvector
                                      ↑       ↓
                         private embeddings  Python MCP → agent
```

The repository is intentionally an application, not a framework. Fork it, edit
`rag-engine.yaml`, and keep the full pipeline visible and debuggable.

## Included adapters

- `json` is enabled by default and loads six credential-free example documents.
- `gong` is a production reference for pagination, rate limiting, calls,
  transcripts, speakers, CRM projections, and retry behavior.

Both produce the same canonical `Document → ContentUnit → Chunk` model and use
the same Workflows, embeddings, search functions, and generic MCP tools.

## Quick start

Requirements: Docker, Python 3.12+, and Render CLI 2.28+ for Workflow testing.

```bash
cp .env.example .env
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt

docker compose up -d postgres
python scripts/migrate.py

# Build and run the manifest-selected private model
docker build -f embedding-service/Dockerfile -t rag-embeddings .
docker run --rm -d --name rag-embeddings \
  -p 10000:10000 \
  -e PORT=10000 \
  -e EMBEDDING_API_KEY=dev-embedding-key \
  rag-embeddings

# Load, chunk, and embed the bundled JSON source
python scripts/load.py \
  --from 2026-09-01T00:00:00Z \
  --to 2026-10-01T00:00:00Z \
  --update-checkpoint

python scripts/doctor.py
pytest -q
```

## Configuration

`rag-engine.yaml` is the only non-secret application configuration. Its schema is
`rag-engine.schema.json`.

It selects:

- Resource naming
- Enabled adapters and their batch/concurrency policies
- Full-text language
- Chunk target, overlap, and minimum size
- Embedding provider, model artifact, revision, prefixes, dimension, and server
  settings
- MCP namespace and generic filters

Adapters declare environment-variable names; secret values stay in Render
environment variables.

## Canonical storage

Core migrations create:

- `sources`
- `documents`
- `content_units`
- `entities` and `document_entities`
- `chunks`
- `sync_runs` and `source_checkpoints`
- `embedding_profiles` and `embedding_jobs`

Adapter migrations add optional projections linked by `document_id`. The Gong
adapter adds users, calls, participants, CRM associations, topics, trackers,
and `gong_call_context`.

The schema is generated for the embedding dimension and full-text language in
`rag-engine.yaml`. This template targets fresh databases and has no legacy
compatibility layer.

## Render Workflows

The all-Python Workflow app registers:

- `load_source(adapter_id, from_datetime, to_datetime, update_source_checkpoint)`
- `process_batch(adapter_id, record_refs)`
- `embed_pending(profile_id)`

The source adapter controls batch size and whether child batches are sequential
or bounded-concurrent. Canonical writes are idempotent. Checkpoints advance only
after every batch and embedding task succeeds and can never move backward.

Run locally:

```bash
render workflows dev -- .venv/bin/python -m app.workflows.main
render workflows tasks list --local
```

Render Workflows are currently created outside Blueprints. Create
`rag-pipeline` from the repository root:

```text
Build command: pip install -r requirements.txt
Start command: python -m app.workflows.main
```

Give the Workflow `DATABASE_URL`, embedding service variables, and credentials
for every enabled adapter.

Start an initial or historical load:

```bash
python scripts/start_load_workflow.py \
  --adapter json \
  --from 2026-09-01 \
  --to 2026-09-30 \
  --update-checkpoint
```

## Embedding models

The default provider calls an OpenAI-compatible private service. The Docker
builder reads the model repository, filename, immutable revision, alias,
pooling, and context directly from `rag-engine.yaml`.

For a same-dimension model change:

```bash
# Edit rag-engine.yaml, rebuild the private service, then:
python scripts/change_embedding_profile.py
python scripts/embed.py
```

The profile fingerprint changes and existing chunks become pending. For a
dimension change:

```bash
python scripts/change_embedding_profile.py --confirm-dimension-change
```

This intentionally rebuilds the vector column, search functions, and HNSW index
before full re-embedding. Readiness fails on profile, dimension, or model-service
mismatch.

## MCP

The authenticated Python MCP service exposes:

- `rag.schema.describe`
- `rag.sources.list`
- `rag.documents.list`
- `rag.documents.get`
- `rag.search.keyword`
- `rag.search.semantic`
- `rag.search.hybrid`

Every document and passage includes a canonical citation with source,
document/external IDs, URL, chunk/unit range, and adapter locator. Enabled
adapters may add tools such as `rag.gong.calls.list` and
`rag.gong.calls.get`.

Run locally:

```bash
python -m app.mcp.server
```

Run locally:

```bash
python -m app.mcp.server
```

## Render deployment

`render.yaml` creates:

- `rag-db`
- `rag-embeddings`
- `rag-mcp`
- `rag-scheduler`

The scheduler only has a Render API key and starts
`rag-pipeline/load_source`; data-source and database credentials remain on the
Workflow service.

```bash
render blueprints validate
render blueprint launch
```

Then create the Workflow service.

## Adding a source

See [docs/ADAPTERS.md](docs/ADAPTERS.md). In short:

1. Implement `SourceAdapter`.
2. Register it in `app/registry.py`.
3. Add its non-secret configuration to `rag-engine.yaml`.
4. Add projection migrations only when generic metadata/entities are
   insufficient.
5. Run the shared adapter contract suite and `scripts/doctor.py`.

## Layout

```text
app/
  adapters/       # json and gong source boundaries
  embeddings/     # provider implementations
  mcp/            # generic Python MCP plus adapter tools
  pipeline/       # load, persistence, chunking, embedding
  workflows/      # Render Workflow tasks
migrations/core/
embedding-service/
examples/
scripts/
tests/
rag-engine.yaml
rag-engine.schema.json
render.yaml
```
