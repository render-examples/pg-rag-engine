#!/usr/bin/env python3
"""Apply core and enabled-adapter migrations for a fresh template deployment."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from app.config import RagEngineConfig, load_config
from app.db import connection, execute, fetch_all

ROOT = Path(__file__).resolve().parents[1]


def migration_files(config: RagEngineConfig, include_adapters: set[str]) -> list[Path]:
    files = sorted((ROOT / "migrations" / "core").glob("*.sql"))
    enabled = {
        adapter.kind for adapter in config.adapters.values() if adapter.enabled
    } | include_adapters
    for adapter_id in sorted(enabled):
        files.extend(
            sorted((ROOT / "app" / "adapters" / adapter_id / "migrations").glob("*.sql"))
        )
    return files


def migration_id(path: Path) -> str:
    return str(path.relative_to(ROOT))


def render_sql(path: Path, config: RagEngineConfig) -> str:
    return (
        path.read_text()
        .replace("{{EMBEDDING_DIMENSION}}", str(config.embedding.dimension))
        .replace("{{FULL_TEXT_LANGUAGE}}", config.full_text_language)
    )


def ensure_migration_catalog(conn) -> None:
    legacy = conn.execute(
        """
        SELECT EXISTS (
          SELECT 1 FROM information_schema.columns
          WHERE table_schema = 'public'
            AND table_name = 'schema_migrations'
            AND column_name = 'filename'
        ) AS legacy
        """
    ).fetchone()["legacy"]
    if legacy:
        raise RuntimeError(
            "legacy schema detected; this clean-slate template requires a fresh database"
        )
    execute(
        conn,
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
          id TEXT PRIMARY KEY,
          applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
    )


def seed_configuration(conn, config: RagEngineConfig) -> None:
    for adapter in config.adapters.values():
        execute(
            conn,
            """
            INSERT INTO sources (id, adapter_kind, display_name, config, enabled)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
              adapter_kind = EXCLUDED.adapter_kind,
              display_name = EXCLUDED.display_name,
              config = EXCLUDED.config,
              enabled = EXCLUDED.enabled,
              updated_at = now()
            """,
            (
                adapter.id,
                adapter.kind,
                adapter.id.replace("-", " ").title(),
                json.dumps(adapter.config),
                adapter.enabled,
            ),
        )
    embedding = config.embedding
    execute(conn, "UPDATE embedding_profiles SET active = false WHERE active")
    execute(
        conn,
        """
        INSERT INTO embedding_profiles (
          id, provider, model, dimension, document_prefix, query_prefix,
          normalized, fingerprint, active
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, true)
        ON CONFLICT (id) DO UPDATE SET
          provider = EXCLUDED.provider,
          model = EXCLUDED.model,
          dimension = EXCLUDED.dimension,
          document_prefix = EXCLUDED.document_prefix,
          query_prefix = EXCLUDED.query_prefix,
          normalized = EXCLUDED.normalized,
          fingerprint = EXCLUDED.fingerprint,
          active = true
        """,
        (
            embedding.profile_id,
            embedding.provider,
            embedding.model,
            embedding.dimension,
            embedding.document_prefix,
            embedding.query_prefix,
            embedding.normalized,
            embedding.fingerprint,
        ),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--include-adapter",
        action="append",
        default=[],
        help="Apply an adapter migration even when disabled (repeatable)",
    )
    args = parser.parse_args()
    config = load_config()
    files = migration_files(config, set(args.include_adapter))
    if not files:
        print("No migrations found", file=sys.stderr)
        return 1

    with connection() as conn:
        ensure_migration_catalog(conn)
        applied = {
            row["id"] for row in fetch_all(conn, "SELECT id FROM schema_migrations")
        }
        pending = [path for path in files if migration_id(path) not in applied]
        for path in pending:
            identifier = migration_id(path)
            print(f"Applying {identifier}...")
            with conn.transaction():
                conn.execute(render_sql(path, config))
                execute(
                    conn,
                    "INSERT INTO schema_migrations (id) VALUES (%s)",
                    (identifier,),
                )
        with conn.transaction():
            seed_configuration(conn, config)

    print(f"Applied {len(pending)} migration(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
