"""Connection pool and the FastAPI dependency that hands out connections."""

from __future__ import annotations

import os
from collections.abc import Iterator

from fastapi import HTTPException
from psycopg import Connection
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

_pool: ConnectionPool | None = None


def open_pool(url: str | None = None) -> ConnectionPool:
    global _pool
    _pool = ConnectionPool(
        url or os.environ["DATABASE_URL"],
        min_size=1,
        max_size=10,
        kwargs={"row_factory": dict_row, "autocommit": True},
        open=True,
    )
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


def get_conn() -> Iterator[Connection]:
    if _pool is None:
        raise HTTPException(status_code=503, detail="database not available")
    with _pool.connection() as conn:
        yield conn
