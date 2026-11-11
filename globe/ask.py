"""Plain-English questions for Readout Globe, answered with Readout's benchmarked text-to-SQL setup.

The prompt is readout.qa's schema_docs variant (the one that scored 48 of 48) plus notes on the two site
tables. The model's SQL goes through readout.sqlguard: one SELECT, deny-listed functions refused, run on a
read-only connection with external access off, a 20 second timeout and a 500 row cap. As in the benchmark,
a query that fails in DuckDB gets one repair attempt with the error message; refused SQL gets none.

readout.llm is pointed at files the globe owns (configure_llm), so results/llm_usage.json, a published
result, is never touched, and the globe has its own call budget (GLOBE_LLM_BUDGET, default 200).
"""

from __future__ import annotations

import datetime as dt
import math
import os
import re
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from decimal import Decimal

import duckdb

from readout import config, llm, qa, sqlguard

from .queries import NCT_RE, add_months, has_bad_chars

USAGE_FILE = config.DATA / "globe" / "llm_usage.json"
CACHE_DIR = config.CACHE / "globe_llm"
BUDGET_ENV = "GLOBE_LLM_BUDGET"
DEFAULT_BUDGET = 200
TASK = "globe_ask"
MAX_QUESTION = 300
DISPLAY_ROWS = 50
SHORT = 200  # longest error text passed back from the model layer

SITE_TABLES = {
    "marts.fct_trial_sites": ["nct_id", "site_order", "facility", "city", "state", "zip", "country", "site_status",
                              "lat", "lon", "geo_key"],
    "marts.dim_site_location": ["geo_key", "lat", "lon", "city", "state", "country", "n_sites", "n_trials"],
}

SITE_NOTES = """Site tables (for questions about places, countries, cities, hospitals or the map):
- marts.fct_trial_sites has one row per site listed on a study, only for studies in marts.dim_trial.
  (nct_id, site_order) is unique; site_order is the site's 1-based position in the registry record.
  facility, city, state, zip and country are registry text, for example city 'Boston', state 'Massachusetts',
  country 'United States'. site_status is the registry recruitment status of that site and may be null.
  lat and lon are city-level coordinates, null when the registry has none.
  geo_key is printf('%.4f,%.4f', lat, lon), null when there are no coordinates.
- marts.dim_site_location has one row per geo_key, a city point on the globe: lat and lon (read back from
  geo_key, so rounded to 4 decimals), the most common city, state and country combination at that point,
  n_sites (site rows there) and n_trials (distinct studies there).
- A study can list several sites in one city or country, so count studies with count(distinct nct_id).
  Match place names with ILIKE. For distances use the haversine formula on lat and lon with an Earth radius
  of 6371 km; no spatial extension is available.
- Answer at exactly the level the question asks about:
  - country questions: one row per country;
  - state or province questions: one row per state;
  - city, hospital or site questions: one row per place, grouped by geo_key (from marts.fct_trial_sites or
    marts.dim_site_location), with its city and country.
- Add a geo_key column only for city, hospital or site level answers. Never add it to country, state or
  organization level answers.
- When the answer is a list of trials, return one row per trial with a plain nct_id column.
- Never pack nct_id or geo_key into lists, arrays or strings.
- A question that asks for a count or another single number still returns that number.
"""

SYSTEM = (qa.SYSTEM + " Answer at exactly the level the question asks about: country questions get one row per"
          " country, state or province questions one row per state, and city, hospital or site questions one row"
          " per place, grouped by geo_key, with its city and country. Add a geo_key column only for city, hospital"
          " or site level answers; never add it to country, state or organization level answers. When the answer"
          " is a list of trials, return one row per trial with a plain nct_id column. Never pack nct_id or geo_key"
          " into lists, arrays or strings.")

# What the globe highlights: well-formed values of the nct_id and geo_key columns, at most 500 of each.
GEO_KEY_VALUE_RE = re.compile(r"-?\d+\.\d{4},-?\d+\.\d{4}", re.ASCII)
MAX_HIGHLIGHTS = 500


class AskError(Exception):
    """An answer the API turns into {"error": ..., "kind": ...} with this HTTP status."""

    def __init__(self, status: int, kind: str, message: str, sql: str | None = None):
        super().__init__(message)
        self.status, self.kind, self.message, self.sql = status, kind, message, sql


class _QueryFailed(Exception):
    pass


def budget() -> int:
    raw = os.environ.get(BUDGET_ENV, "").strip()
    if not raw:
        return DEFAULT_BUDGET
    if not re.fullmatch(r"[0-9]{1,6}", raw):
        raise ValueError(f"{BUDGET_ENV} must be a whole number of model calls, got {raw[:20]!r}")
    return int(raw)


def configure_llm() -> None:
    """Point readout.llm at globe-owned files and budget. Call before the first model call."""
    llm.USAGE_FILE = USAGE_FILE
    llm.CACHE_DIR = CACHE_DIR
    llm.MAX_CALLS = budget()


def check_question(value) -> str:
    if not isinstance(value, str):
        raise AskError(400, "bad_question", "question must be a string")
    question = value.strip()
    if not 1 <= len(question) <= MAX_QUESTION:
        raise AskError(400, "bad_question", f"question must be 1 to {MAX_QUESTION} characters")
    if has_bad_chars(question):
        raise AskError(400, "bad_question", "question must be one line of plain text, without control characters")
    return question


def schema_text(cur) -> str:
    """qa.schema_text's table lines plus the two site tables, in the same format."""
    lines = [qa.schema_text(cur)]
    for table, cols in SITE_TABLES.items():
        schema, name = table.split(".")
        types = dict(cur.execute(
            "select column_name, data_type from information_schema.columns where table_schema = ? and table_name = ?",
            [schema, name]).fetchall())
        lines.append(f"{table}(" + ", ".join(f"{c} {types.get(c, '?')}" for c in cols) + ")")
    return "\n".join(lines)


def _docs_for(as_of: str) -> str:
    """qa.DOCS with its snapshot date (and the 12-month window end) moved to `as_of`."""
    if as_of == qa.AS_OF:
        return qa.DOCS
    old, new = dt.date.fromisoformat(qa.AS_OF), dt.date.fromisoformat(as_of)
    swap = {qa.AS_OF: as_of, add_months(old, 12).isoformat(): add_months(new, 12).isoformat()}
    return re.sub("|".join(map(re.escape, swap)), lambda m: swap[m.group(0)], qa.DOCS)


def build_prompt(schema: str, question: str, as_of: str) -> str:
    """qa.prompt('schema_docs', ...) with the site notes added after the conventions and worked examples."""
    message = qa.prompt("schema_docs", schema, question)
    if qa.DOCS not in message:  # qa.prompt always includes DOCS for schema_docs; never send a prompt without them
        raise RuntimeError("readout.qa.prompt no longer includes DOCS for schema_docs")
    return message.replace(qa.DOCS, _docs_for(as_of) + "\n" + SITE_NOTES, 1)


def _write_sql(message: str) -> tuple[str, str | None]:
    try:
        answer = llm.complete_json(SYSTEM, message, qa.SCHEMA, task=TASK)
    except llm.LLMUnavailable as exc:
        raise AskError(503, "llm_unavailable", str(exc)[:SHORT] or "no model is available") from exc
    except (llm.LLMError, ValueError, OSError) as exc:
        raise AskError(503, "llm_unavailable", "the model did not return a usable answer; try again") from exc
    if not isinstance(answer, dict):
        raise AskError(503, "llm_unavailable", "the model did not return a usable answer; try again")
    sql = answer.get("sql")
    meta = answer.get("_meta") if isinstance(answer.get("_meta"), dict) else {}
    return (sql.strip() if isinstance(sql, str) else ""), meta.get("model")


def _execute(cursor: Callable[[], AbstractContextManager], sql: str) -> tuple[list[str], list[tuple]]:
    with cursor() as cur:
        try:
            return sqlguard.run(cur, sql)
        except sqlguard.UnsafeSQL as exc:
            raise AskError(422, "unsafe_sql", f"refused by the SQL guard: {exc}", sql=sql) from exc
        except duckdb.InterruptException as exc:
            raise AskError(504, "timeout", f"the query ran longer than {sqlguard.TIMEOUT_S} seconds", sql=sql) from exc
        except (duckdb.Error, ValueError, OverflowError, TypeError) as exc:
            raise _QueryFailed(str(exc).splitlines()[0][:300] if str(exc) else exc.__class__.__name__) from exc


def jsonable(value):
    """A DuckDB result value as plain JSON: dates as ISO text, decimals as numbers, NaN and infinity as null."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, (float, Decimal)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, (dt.date, dt.datetime, dt.time)):
        return value.isoformat()
    if isinstance(value, (list, tuple, set)):
        return [jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    return str(value)  # timedelta, UUID and anything else


def _column_key(name: str) -> str:
    return re.sub(r"[\s_]+", "", str(name).lower())


def _flatten(value):
    if isinstance(value, (list, tuple)):
        for item in value:
            yield from _flatten(item)
    else:
        yield value


def highlight_values(cols: list[str], rows: list[tuple], column: str, pattern: re.Pattern) -> list[str]:
    """Distinct well-formed values from every column named like `column` (case, spaces and underscores ignored,
    so "NCT ID" counts as nct_id), with list cells flattened, in order of appearance, at most MAX_HIGHLIGHTS."""
    want = _column_key(column)
    picked = [i for i, c in enumerate(cols) if _column_key(c) == want]
    found: dict[str, None] = {}
    for row in rows:
        for i in picked:
            for value in _flatten(row[i]):
                if isinstance(value, str) and pattern.fullmatch(value) and value not in found:
                    found[value] = None
                    if len(found) >= MAX_HIGHLIGHTS:
                        return list(found)
    return list(found)


def answer(question: str, cursor: Callable[[], AbstractContextManager], schema: str, as_of: str) -> dict:
    """Write SQL for the question, run it in the sandbox and shape the API response. Raises AskError."""
    started = time.monotonic()
    calls_before = llm.calls_made()
    message = build_prompt(schema, question, as_of)
    sql, model = _write_sql(message)
    try:
        cols, rows = _execute(cursor, sql)
    except _QueryFailed as first:
        # one repair attempt with the error message, worded as in the benchmark (readout.qa)
        repair = message + f"\n\nYour previous SQL was:\n{sql}\nIt failed with: {first}\nFix it."
        sql, model = _write_sql(repair)
        try:
            cols, rows = _execute(cursor, sql)
        except _QueryFailed as second:
            raise AskError(500, "query_failed", f"the query failed: {second}", sql=sql) from second
    return {
        "question": question,
        "sql": sql,
        "columns": cols,
        "rows": [[jsonable(v) for v in r] for r in rows[:DISPLAY_ROWS]],
        "row_count": len(rows),
        "nct_ids": highlight_values(cols, rows, "nct_id", NCT_RE),
        "geo_keys": highlight_values(cols, rows, "geo_key", GEO_KEY_VALUE_RE),
        "model": model or llm.MODEL,
        "seconds": round(time.monotonic() - started, 1),
        "cached": llm.calls_made() == calls_before,
    }
