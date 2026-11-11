"""DuckDB connections with modest memory settings."""

from __future__ import annotations

import duckdb

from . import config


def connect(read_only: bool = False) -> duckdb.DuckDBPyConnection:
    config.WAREHOUSE_DIR.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(
        str(config.WAREHOUSE_DB),
        read_only=read_only,
        config={"memory_limit": config.DUCKDB_MEMORY_LIMIT, "threads": config.DUCKDB_THREADS},
    )
    return con


def rows(con: duckdb.DuckDBPyConnection, sql: str, params: list | None = None) -> list[dict]:
    cur = con.execute(sql, params or [])
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]
