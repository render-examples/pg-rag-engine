# Architecture

## Boundaries

The template has four stable boundaries:

1. An adapter discovers and normalizes external records.
2. The canonical pipeline persists documents, units, entities, and chunks.
3. an embedding profile generates vectors through a private
   OpenAI-compatible endpoint.
4. MCP exposes generic retrieval and optional adapter projections.

Adapters never implement Workflow orchestration, checkpoint storage, chunk
storage, embeddings, or generic search.

## Data flow

```text
rag-scheduler
  → rag-pipeline/load_source
    → Adapter.discover
    → process_batch children
        → Adapter.fetch_batch
        → Adapter.normalize
        → canonical repository
        → Adapter.persist_projection
    → embed_pending
        → rag-embeddings over private network
    → source checkpoint

rag-mcp
  → rag-db
  → rag-embeddings for query vectors
  → existing MCP Toolshed
```

## Delivery guarantees

- Workflow execution is at least once.
- `(source_id, external_id)` makes document writes idempotent.
- Deterministic document and chunk hashes prevent unchanged reprocessing.
- Child batches commit independently and can be retried safely.
- Source checkpoints advance only after all children and embeddings succeed.
- Checkpoints use `GREATEST`, so historical reloads cannot move them backward.
- Model fingerprints invalidate stale vectors automatically.

## Source-specific projections

Canonical metadata and entities should cover most adapters. Rich relational
projections are appropriate when users need stable domain filters or joins.
Projection tables always reference `documents.id`; they never replace canonical
documents or chunks.

The Gong adapter demonstrates this with call, participant, CRM, topic, and
tracker projections.

## Model changes

One embedding profile is active per deployment. The profile fingerprint covers
provider, model, dimension, prefixes, normalization, and pinned model artifact.

- Same dimension: update the profile, rebuild the service, and re-embed.
- Different dimension: explicitly rebuild vector storage/search indexes, then
  re-embed.

MCP readiness compares manifest, database profile, vector typmod, and embedding
service output before accepting traffic.

## Deployment

The Blueprint owns Postgres, private embeddings, Python MCP, and the scheduler.
Render Workflows are deployed separately as `rag-pipeline` because Workflow
services are not currently Blueprint resources.

Only the Workflow receives source credentials and database access. The
scheduler only dispatches a task. Toolshed receives only the MCP URL and shared
service token.
