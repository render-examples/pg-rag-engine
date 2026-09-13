"""Canonical Postgres connection helpers."""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row


def database_url() -> str:
    value = os.environ.get("DATABASE_URL", "").strip()
    if not value:
        raise RuntimeError("DATABASE_URL is not set")
    return value.replace("postgres://", "postgresql://", 1)


@contextmanager
def connection() -> Iterator[psycopg.Connection]:
    with psycopg.connect(database_url(), row_factory=dict_row) as conn:
        yield conn


def execute(
    conn: psycopg.Connection,
    sql: str,
    params: tuple[Any, ...] | dict[str, Any] | None = None,
) -> None:
    conn.execute(sql, params)


def fetch_all(
    conn: psycopg.Connection,
    sql: str,
    params: tuple[Any, ...] | dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    return list(conn.execute(sql, params).fetchall())


def fetch_one(
    conn: psycopg.Connection,
    sql: str,
    params: tuple[Any, ...] | dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    return conn.execute(sql, params).fetchone()
