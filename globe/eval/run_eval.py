"""Score Readout Globe's plain-English questions against gold answers.

Run it from the project root with the project's virtual environment, after `make sites`:

    .venv/bin/python -m globe.eval.run_eval

For every question in globe/eval/questions.json:
  1. the gold SQL runs on a read-only sandbox connection (readout.sqlguard.sandbox_connection);
  2. the question goes through globe.ask.answer, the same code POST /api/ask runs: the benchmarked prompt plus
     the site notes, the SQL guard and one repair attempt after a failed query;
  3. readout.qa.matches compares the two answers, with the answer kinds readout.qa uses.
Results go to results/globe_qa/records.json (one record per question) and results/globe_qa/metrics.json
(correct count, by answer kind, median seconds, model). Nothing is written to results/qa/ or to
results/llm_usage.json: globe.ask.configure_llm() moves the model call log, cache and call budget to files the
globe owns (data/globe/llm_usage.json, data/cache/globe_llm, GLOBE_LLM_BUDGET). The run needs at most 40 model
calls (20 questions, each with at most one repair). Answers are cached, so a rerun asks the model nothing new.

A gold answer that cannot be graded is reported as invalid_gold and left out of the score: SQL that fails, no
rows, a NULL, a count of zero, a list longer than the 50 rows the globe shows, or a tie for first place in a
"text" question. Text questions return the winner's count as a second column and a second row (limit 2) so
that tie can be seen.

The run refuses to start when marts.fct_trial_sites or marts.dim_site_location is missing or empty, or when the
warehouse snapshot is not the one the questions were written for.
"""

from __future__ import annotations

import datetime as dt
import json
import statistics
import sys
import time
from collections import Counter
from collections.abc import Callable
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:  # lets `python globe/eval/run_eval.py` work as well as `python -m globe.eval.run_eval`
    sys.path.insert(0, str(ROOT))

import duckdb  # noqa: E402

from readout import config, qa, sqlguard  # noqa: E402

QUESTIONS_FILE = Path(__file__).with_name("questions.json")
OUT = config.RESULTS / "globe_qa"
SITE_TABLES = ("marts.fct_trial_sites", "marts.dim_site_location")
KINDS = frozenset(kind for _, kind, _, _ in qa.QUESTIONS)  # the answer kinds readout.qa knows
MAX_LIST = 50  # the globe shows at most 50 rows (globe.ask.DISPLAY_ROWS), so a longer list cannot be compared
SAVED_ROWS = 20  # answer rows kept in records.json, as in readout.qa


def load_questions(path: Path = QUESTIONS_FILE) -> list[dict]:
    questions = json.loads(path.read_text())
    ids = [q["id"] for q in questions]
    if len(ids) != len(set(ids)):
        raise SystemExit(f"{path.name}: question ids are not unique")
    unknown = sorted({q["kind"] for q in questions} - KINDS)
    if unknown:
        raise SystemExit(f"{path.name}: unknown answer kinds {unknown}; readout.qa uses {sorted(KINDS)}")
    return questions


def missing_site_tables(con) -> list[str]:
    have = {f"{schema}.{name}" for schema, name in con.execute(
        "select table_schema, table_name from information_schema.tables").fetchall()}
    return [t for t in SITE_TABLES if t not in have]


def empty_site_tables(con) -> list[str]:
    return [t for t in SITE_TABLES if con.execute(f"select count(*) from {t}").fetchone()[0] == 0]


def plain(value):
    """A DuckDB value as plain JSON: dates as ISO text, decimals as numbers."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()[:10]
    return value


def gold_problem(kind: str, rows: list[tuple]) -> str | None:
    """Why a gold answer cannot be graded, or None when it can."""
    if not rows:
        return "gold SQL returned no rows"
    if kind == "list":
        if len(rows) > MAX_LIST:
            return f"gold list has {len(rows)} rows, more than the {MAX_LIST} the globe shows"
        if any(row[0] is None for row in rows):
            return "gold list contains a NULL"
        return None
    first = rows[0][0]
    if first is None:
        return "gold answer is NULL"
    if kind == "count" and first == 0:
        return "gold count is zero, so any empty answer would pass"
    if kind == "text" and len(rows) > 1 and len(rows[0]) > 1 and rows[0][1] == rows[1][1]:
        return f"tie for first place at {rows[0][1]}"
    return None


def compute_gold(con, questions: list[dict]) -> dict[str, dict]:
    """Run every gold SQL. Returns {id: {"rows": [...], "problem": reason or None}}."""
    gold = {}
    for q in questions:
        try:
            _, rows = sqlguard.run(con, q["gold_sql"])
        except (sqlguard.UnsafeSQL, duckdb.Error) as exc:
            gold[q["id"]] = {"rows": [], "problem": f"gold SQL failed: {str(exc).splitlines()[0][:200]}"}
        else:
            gold[q["id"]] = {"rows": rows, "problem": gold_problem(q["kind"], rows)}
    return gold


def grade(kind: str, gold_rows: list[tuple], answer: dict) -> bool:
    """readout.qa.matches on the rows the globe returned. For lists the full row count must match too, because
    the globe returns at most 50 rows for display."""
    rows = [tuple(r) for r in answer.get("rows") or []]
    if kind == "list" and answer.get("row_count", len(rows)) != len(gold_rows):
        return False
    return qa.matches(kind, gold_rows, rows)


def outcome(rec: dict) -> str:
    if rec["status"] == "invalid_gold":
        return "invalid_gold"
    if rec["correct"]:
        return "correct"
    return "wrong_result" if rec["status"] == "ok" else rec["status"]


def _progress(message: str) -> None:
    print(message, flush=True)  # flushed, so a long run shows progress even when piped to tee or a file


def evaluate(questions: list[dict], gold: dict[str, dict], ask_one: Callable[[str], dict],
             log: Callable[[str], None] = _progress) -> list[dict]:
    """Ask every gradable question through ask_one(question) -> the /api/ask response dict, and grade it.
    Returns one record per question, in order."""
    records = []
    for q in questions:
        g = gold[q["id"]]
        rec = {"id": q["id"], "kind": q["kind"], "question": q["question"], "note": q.get("note"),
               "gold": [[plain(v) for v in row] for row in g["rows"]]}
        if g["problem"]:
            rec.update(status="invalid_gold", correct=None, error=g["problem"])
            records.append(rec)
            log(f"{q['id']} {q['kind']:6} invalid_gold: {g['problem']}")
            continue
        started = time.monotonic()
        try:
            answer = ask_one(q["question"])
        except Exception as exc:  # globe.ask.AskError has kind, message and sql; anything else is a bug to record
            rec.update(status=getattr(exc, "kind", "error"), correct=False, sql=getattr(exc, "sql", None),
                       error=str(getattr(exc, "message", None) or exc)[:300])
        else:
            rec.update(status="ok", correct=grade(q["kind"], g["rows"], answer), sql=answer.get("sql"),
                       columns=answer.get("columns"), rows=(answer.get("rows") or [])[:SAVED_ROWS],
                       row_count=answer.get("row_count"), model=answer.get("model"),
                       seconds=answer.get("seconds"), cached=answer.get("cached"))
        rec["wall_seconds"] = round(time.monotonic() - started, 1)
        records.append(rec)
        log(f"{q['id']} {q['kind']:6} {outcome(rec):14} {rec['wall_seconds']:6.1f} s")
    return records


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 1) if values else None


def summarize(records: list[dict], as_of: str) -> dict:
    """The numbers to report. Questions with an invalid gold answer are counted in `questions` but not scored.
    Seconds are the ones the globe reports for an answer; a cached answer is not a fair timing, so the median is
    also given over uncached answers only."""
    scored = [r for r in records if r["status"] != "invalid_gold"]
    answered = [r for r in scored if r["status"] == "ok"]
    by_kind: dict[str, dict[str, int]] = {}
    for r in scored:
        entry = by_kind.setdefault(r["kind"], {"questions": 0, "correct": 0})
        entry["questions"] += 1
        entry["correct"] += 1 if r["correct"] else 0
    correct = sum(e["correct"] for e in by_kind.values())
    timed = [r["seconds"] for r in answered if r.get("seconds") is not None]
    fresh = [r["seconds"] for r in answered if r.get("seconds") is not None and not r.get("cached")]
    return {
        "as_of": as_of,
        "questions": len(records),
        "scored": len(scored),
        "correct": correct,
        "accuracy": round(correct / len(scored), 3) if scored else 0.0,
        "by_kind": dict(sorted(by_kind.items())),
        "outcomes": dict(sorted(Counter(outcome(r) for r in records).items())),
        "answered": len(answered),
        "median_seconds": _median(timed),
        "median_seconds_uncached": _median(fresh),
        "cached_answers": sum(1 for r in answered if r.get("cached")),
        "models": sorted({r["model"] for r in answered if r.get("model")}),
        "invalid_gold": [{"id": r["id"], "reason": r["error"]} for r in records if r["status"] == "invalid_gold"],
    }


def run() -> dict:
    try:
        con = sqlguard.sandbox_connection()
    except duckdb.Error as exc:
        raise SystemExit(f"Cannot open {config.WAREHOUSE_DB} read-only: {str(exc).splitlines()[0]}") from None
    try:
        missing = missing_site_tables(con)
        if missing:
            raise SystemExit(f"Refusing to run: {' and '.join(missing)} not in the warehouse. Run `make sites` first.")
        empty = empty_site_tables(con)
        if empty:
            raise SystemExit(f"Refusing to run: {' and '.join(empty)} has no rows. Run `make sites` first.")
        as_of = config.as_of_date()
        if as_of != qa.AS_OF:
            raise SystemExit(f"Refusing to run: the questions are written for the {qa.AS_OF} snapshot, "
                             f"but this warehouse is {as_of}.")
        questions = load_questions()
        gold = compute_gold(con, questions)

        from globe import ask  # imported late: nothing from the globe loads before the table checks pass

        try:
            ask.configure_llm()
        except ValueError as exc:
            raise SystemExit(str(exc)) from None
        schema = ask.schema_text(con)

        @contextmanager
        def cursor():
            cur = con.cursor()
            try:
                yield cur
            finally:
                cur.close()

        records = evaluate(questions, gold, lambda text: ask.answer(text, cursor, schema, as_of))
    finally:
        con.close()
    metrics = summarize(records, as_of)
    if not metrics["answered"]:
        first = next((r["error"] for r in records if r["status"] not in ("invalid_gold", "ok")), "nothing was asked")
        raise SystemExit(f"No question was answered, so nothing was written: {first}")
    metrics["run_at"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "records.json").write_text(json.dumps(records, indent=1) + "\n")
    (OUT / "metrics.json").write_text(json.dumps(metrics, indent=2) + "\n")
    return metrics


def main() -> None:
    metrics = run()
    print(f"\n{metrics['correct']} of {metrics['scored']} correct ({metrics['accuracy']:.1%}); "
          f"median {metrics['median_seconds']} s; model {', '.join(metrics['models']) or 'unknown'}")
    for kind, entry in metrics["by_kind"].items():
        print(f"  {kind:6} {entry['correct']}/{entry['questions']}")
    for bad in metrics["invalid_gold"]:
        print(f"  invalid gold {bad['id']}: {bad['reason']}")
    print(f"wrote {OUT / 'records.json'} and {OUT / 'metrics.json'}")


if __name__ == "__main__":
    main()
