#!/usr/bin/env python3
"""Apply the manifest embedding profile and schedule safe full re-embedding."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from app.config import load_config
from app.db import connection, execute, fetch_one
from scripts.migrate import render_sql, seed_configuration

ROOT = Path(__file__).resolve().parents[1]


def current_dimension(conn) -> int:
    row = fetch_one(
        conn,
        """
        SELECT format_type(a.atttypid, a.atttypmod) AS type
        FROM pg_attribute a
        JOIN pg_class c ON c.oid = a.attrelid
        WHERE c.relname = 'chunks' AND a.attname = 'embedding'
        """,
    )
    if not row:
        raise RuntimeError("chunks.embedding does not exist; run migrations first")
    match = re.fullmatch(r"vector\((\d+)\)", row["type"])
    if not match:
        raise RuntimeError(f"unexpected embedding type: {row['type']}")
    return int(match.group(1))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--confirm-dimension-change",
        action="store_true",
        help="Allow destructive vector/index rebuild when dimensions changed",
    )
    args = parser.parse_args()
    config = load_config()
    target = config.embedding.dimension

    with connection() as conn:
        current = current_dimension(conn)
        if current != target and not args.confirm_dimension_change:
            print(
                f"embedding dimension change {current} → {target} requires "
                "--confirm-dimension-change",
                file=sys.stderr,
            )
            return 2
        with conn.transaction():
            if current != target:
                execute(conn, "DROP INDEX IF EXISTS chunks_embedding_hnsw")
                conn.execute(
                    f"""
                    ALTER TABLE chunks
                    ALTER COLUMN embedding TYPE vector({target})
                    USING NULL::vector({target})
                    """
                )
                conn.execute(
                    """
                    CREATE INDEX chunks_embedding_hnsw ON chunks
                    USING hnsw (embedding vector_cosine_ops)
                    WITH (m = 16, ef_construction = 64)
                    """
                )
                search_migration = ROOT / "migrations" / "core" / "002_search.sql"
                conn.execute(render_sql(search_migration, config))
            seed_configuration(conn, config)
            execute(
                conn,
                """
                UPDATE chunks SET
                  embedding = NULL,
                  embedding_profile_id = %s,
                  embedding_fingerprint = %s,
                  embedding_status = 'pending',
                  embedded_at = NULL,
                  last_error = NULL
                WHERE embedding_fingerprint IS DISTINCT FROM %s
                """,
                (
                    config.embedding.profile_id,
                    config.embedding.fingerprint,
                    config.embedding.fingerprint,
                ),
            )
            execute(conn, "DELETE FROM embedding_jobs")
            execute(
                conn,
                """
                INSERT INTO embedding_jobs (
                  document_id, profile_id, status, chunk_count, embedded_count
                )
                SELECT
                  document_id,
                  %s,
                  CASE
                    WHEN count(*) FILTER (WHERE embedding_status = 'complete') = count(*)
                      THEN 'complete'
                    WHEN count(*) FILTER (WHERE embedding_status = 'complete') > 0
                      THEN 'partial'
                    ELSE 'pending'
                  END,
                  count(*)::int,
                  count(*) FILTER (WHERE embedding_status = 'complete')::int
                FROM chunks
                GROUP BY document_id
                """,
                (config.embedding.profile_id,),
            )

    print(
        f"embedding profile {config.embedding.profile_id} active "
        f"(dimension={target}, fingerprint={config.embedding.fingerprint[:12]})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
