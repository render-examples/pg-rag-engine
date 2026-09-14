# Adapter guide

Adapters translate an external source into canonical documents. They do not own
chunking, embeddings, search, checkpoints, Workflow orchestration, or MCP.

## Contract

Implement `SourceAdapter` from `app/contracts.py`:

- `configured()` and `missing_environment()` validate source credentials.
- `discover(from_datetime, to_datetime, checkpoint)` returns stable
  `RecordRef` values without downloading full content.
- `fetch_batch(refs)` fetches raw records. Raise `AdapterError` and classify
  nonretryable source errors where possible.
- `normalize(raw)` is deterministic and returns `NormalizedDocument`.
- `persist_projection(...)` optionally writes source-specific relational data.

Register the constructor once in `app/registry.py`, then add an entry to
`rag-engine.yaml`.

## Canonical requirements

- `(source_id, external_id)` must remain stable across replays.
- Content units are 1-based and ordered.
- Every unit needs a source locator sufficient to reproduce the citation.
- `occurred_at` is required and timezone-aware.
- Metadata must be JSON-serializable and must not contain credentials.
- Normalizing the same raw record must produce the same `content_hash`.

## Adapter migrations

Place optional projection migrations under:

```text
app/adapters/<adapter-id>/migrations/
```

`scripts/migrate.py` applies core migrations first and migrations for enabled
adapters second. During development, use:

```bash
python scripts/migrate.py --include-adapter gong
```

Projection tables must reference `documents.id` and must not duplicate chunk or
embedding storage.

## Included adapters

### JSON

The default adapter reads an array or `{ "documents": [...] }` from a bundled
file or HTTP URL. Each document can provide `units`; otherwise its `text`
becomes one unit. This is the credential-free quick start and the contract
reference.

### Gong

The Gong adapter demonstrates production complexity: authenticated pagination,
rate limiting, batched detail/transcript fetches, people and CRM entities,
transcript locators, and rich projection tables. Enable it in `rag-engine.yaml`, run
migrations, and set the three declared Gong environment variables.

## Contract-test checklist

Every adapter should test:

1. Pagination and deterministic discovery
2. Stable IDs and source locators
3. Deterministic normalization and content hashes
4. Idempotent replay
5. Changed-record replacement
6. Missing-record and retry classification
7. Projection integrity, if projections are provided
