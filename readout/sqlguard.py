"""Run model-written SQL safely.

Fail closed, in layers:
  1. sqlglot must parse the text as exactly one read-only query (SELECT, WITH or set operation).
  2. A deny list rejects file and network table functions and settings changes even inside a query.
  3. DuckDB opens the warehouse read only, with external access disabled and the configuration locked,
     so even a query that slipped past 1 and 2 cannot read files, reach the network or write.
  4. A timeout interrupts long queries and results are capped at MAX_ROWS rows.
"""

from __future__ import annotations

import re
import threading

import duckdb
import sqlglot
from sqlglot import exp

from . import config

MAX_ROWS = 500
TIMEOUT_S = 20
DENY = re.compile(
    r"\b(read_csv\w*|read_json\w*|read_parquet|read_text|read_blob|glob|parquet_scan|csv_scan|json_scan|"
    r"sniff_csv|httpfs|attach|detach|copy|export|import|install|load|pragma|set|reset|call|checkpoint|"
    r"create|insert|update|delete|drop|alter|vacuum|getenv|current_setting|duckdb_secrets)\b",
    re.I,
)


class UnsafeSQL(Exception):
    pass


def check(sql: str) -> str:
    """Return the query if it is one read-only statement, else raise UnsafeSQL."""
    text = sql.strip().rstrip(";").strip()
    if not text:
        raise UnsafeSQL("empty query")
    try:
        statements = [s for s in sqlglot.parse(text, read="duckdb") if s is not None]
    except sqlglot.errors.ParseError as exc:
        raise UnsafeSQL(f"does not parse: {str(exc).splitlines()[0][:160]}") from exc
    if len(statements) != 1:
        raise UnsafeSQL("exactly one statement is allowed")
    root = statements[0]
    if not isinstance(root, (exp.Select, exp.Union, exp.Intersect, exp.Except, exp.Subquery)):
        raise UnsafeSQL(f"only SELECT queries are allowed, got {root.key}")
    for node in root.walk():
        if isinstance(node, (exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create, exp.Alter, exp.Command)):
            raise UnsafeSQL(f"{node.key} is not allowed")
    stripped = re.sub(r"'(?:[^']|'')*'", "''", text)  # ignore words inside string literals
    hit = DENY.search(stripped)
    if hit:
        raise UnsafeSQL(f"'{hit.group(0)}' is not allowed")
    return text


def sandbox_connection(path=None) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(
        str(path or config.WAREHOUSE_DB),
        read_only=True,
        config={
            "memory_limit": config.DUCKDB_MEMORY_LIMIT,
            "threads": config.DUCKDB_THREADS,
            "enable_external_access": False,
            "autoload_known_extensions": False,
            "autoinstall_known_extensions": False,
            "lock_configuration": True,
        },
    )


def run(con: duckdb.DuckDBPyConnection, sql: str) -> tuple[list[str], list[tuple]]:
    """Check, then execute with a timeout. Returns column names and at most MAX_ROWS rows."""
    query = check(sql)
    timer = threading.Timer(TIMEOUT_S, con.interrupt)
    timer.start()
    try:
        cur = con.execute(query)
        cols = [d[0] for d in cur.description]
        rows = cur.fetchmany(MAX_ROWS)
    finally:
        timer.cancel()
    return cols, rows
