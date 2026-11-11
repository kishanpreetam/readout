"""Checks for globe/eval that need no warehouse file, no globe server and no model.

questions.json is validated statically (ids, fields, answer kinds, the SQL guard, the tables and columns of the data
contract, the as-of dates, the /api/ask length limit) and its gold SQL is bound and run on empty in-memory stand-ins
for the five contract tables, so a typo in a column or function name fails here rather than during the evaluation.
run_eval's grading and scoring helpers are tested with made-up answers.
"""

import datetime as dt
import json
import re

import duckdb
import pytest
import sqlglot
from sqlglot import exp
from sqlglot.errors import OptimizeError
from sqlglot.optimizer.qualify import qualify

from globe.eval import run_eval
from readout import config, qa, sqlguard

# The tables and columns the gold SQL may use (the contract in globe/API.md plus the readout tables), with types.
CONTRACT = {
    "dim_trial": {
        "nct_id": "varchar", "brief_title": "varchar", "official_title": "varchar", "acronym": "varchar",
        "phase": "varchar", "phase_label": "varchar", "phase_number": "decimal(2,1)", "overall_status": "varchar",
        "is_active": "boolean", "start_date": "date", "primary_completion_date": "date", "completion_date": "date",
        "enrollment_count": "bigint", "lead_sponsor_name": "varchar", "lead_org_id": "integer",
        "lead_org_name": "varchar", "conditions": "varchar", "interventions": "varchar",
        "has_drug_or_biologic": "boolean", "brief_summary": "varchar"},
    "dim_organization": {
        "org_id": "integer", "org_name": "varchar", "n_trials": "bigint", "n_trials_as_lead": "bigint",
        "cik": "bigint", "ticker": "varchar"},
    "mart_upcoming_readouts": {
        "org_id": "integer", "org_name": "varchar", "cik": "bigint", "ticker": "varchar", "sponsor_role": "varchar",
        "nct_id": "varchar", "brief_title": "varchar", "phase": "varchar", "phase_label": "varchar",
        "is_phase3": "boolean", "overall_status": "varchar", "primary_completion_date": "date",
        "enrollment_count": "bigint", "conditions": "varchar", "interventions": "varchar",
        "days_until_primary_completion": "bigint"},
    "fct_trial_sites": {
        "nct_id": "varchar", "site_order": "integer", "facility": "varchar", "city": "varchar", "state": "varchar",
        "zip": "varchar", "country": "varchar", "site_status": "varchar", "lat": "double", "lon": "double",
        "geo_key": "varchar"},
    "dim_site_location": {
        "geo_key": "varchar", "lat": "double", "lon": "double", "city": "varchar", "state": "varchar",
        "country": "varchar", "n_sites": "bigint", "n_trials": "bigint"},
}
SCHEMA = {"marts": CONTRACT}
FIELDS = {"id", "question", "kind", "gold_sql", "note"}

QUESTIONS = json.loads(run_eval.QUESTIONS_FILE.read_text())
IDS = [q["id"] for q in QUESTIONS]


def _stand_in(tables=tuple(CONTRACT)):
    """An in-memory DuckDB with empty copies of the contract tables."""
    con = duckdb.connect()
    con.execute("create schema marts")
    for table in tables:
        cols = ", ".join(f"{c} {t}" for c, t in CONTRACT[table].items())
        con.execute(f"create table marts.{table} ({cols})")
    return con


# ---------- questions.json ----------

def test_twenty_questions_with_unique_ids():
    assert len(QUESTIONS) == 20
    assert len(set(IDS)) == len(IDS)
    assert all(re.fullmatch(r"g\d\d", i) for i in IDS)


def test_every_item_has_exactly_the_expected_fields():
    for q in QUESTIONS:
        assert set(q) == FIELDS, q["id"]
        assert all(isinstance(q[f], str) and q[f].strip() for f in FIELDS), q["id"]


def test_kinds_are_the_answer_kinds_readout_qa_uses():
    qa_kinds = {kind for _, kind, _, _ in qa.QUESTIONS}
    assert run_eval.KINDS == qa_kinds
    assert {q["kind"] for q in QUESTIONS} == qa_kinds  # every kind is exercised


@pytest.mark.parametrize("q", QUESTIONS, ids=IDS)
def test_gold_sql_passes_the_sql_guard(q):
    assert sqlguard.check(q["gold_sql"])


@pytest.mark.parametrize("q", QUESTIONS, ids=IDS)
def test_question_fits_the_ask_endpoint_contract(q):
    text = q["question"]  # POST /api/ask takes 1 to 300 characters of one line of plain text (globe/API.md)
    assert 1 <= len(text) <= 300 and text == text.strip() and text.isprintable()


def test_house_style_has_no_dashes_or_arrows():
    banned = {chr(0x2013), chr(0x2014), chr(0x2192), "--", "/*"}  # en dash, em dash, arrow, SQL comment openers
    for q in QUESTIONS:
        for field in FIELDS:
            assert not any(mark in q[field] for mark in banned), (q["id"], field)


@pytest.mark.parametrize("q", QUESTIONS, ids=IDS)
def test_gold_sql_uses_only_contract_tables_and_columns(q):
    tree = sqlglot.parse_one(q["gold_sql"], read="duckdb")
    tables = {f"{t.db}.{t.name}" for t in tree.find_all(exp.Table)}
    assert tables <= {f"marts.{t}" for t in CONTRACT}, tables
    try:  # strict: an unknown column raises
        qualify(tree, schema=SCHEMA, dialect="duckdb", validate_qualify_columns=True)
    except OptimizeError as exc:
        pytest.fail(f"{q['id']}: {exc}")


def test_relative_windows_use_the_as_of_date():
    end = dt.date.fromisoformat(qa.AS_OF).replace(year=int(qa.AS_OF[:4]) + 1).isoformat()  # 12 months after
    for q in QUESTIONS:
        assert set(re.findall(r"date '(\d{4}-\d{2}-\d{2})'", q["gold_sql"])) <= {qa.AS_OF, end}, q["id"]
        assert set(re.findall(r"\d{4}-\d{2}-\d{2}", q["question"])) <= {qa.AS_OF, end}, q["id"]


@pytest.fixture(scope="module")
def stand_in():
    con = _stand_in()
    yield con
    con.close()


@pytest.mark.parametrize("q", QUESTIONS, ids=IDS)
def test_gold_sql_runs_and_has_the_shape_its_kind_needs(stand_in, q):
    cols, rows = sqlguard.run(stand_in, q["gold_sql"])
    if q["kind"] == "list":
        assert len(cols) == 1  # the list is the first column
    elif q["kind"] == "text":
        # the winner and its count, and one runner-up row, so run_eval can see a tie for first place
        assert len(cols) == 2 and re.search(r"limit 2\s*$", q["gold_sql"]), q["id"]
    else:
        assert len(cols) == 1 and len(rows) == 1  # a scalar: count, number or date


# ---------- run_eval ----------

def test_results_go_to_their_own_folder():
    assert run_eval.OUT == config.RESULTS / "globe_qa"
    assert run_eval.OUT != qa.OUT and run_eval.OUT.name == "globe_qa"


def test_load_questions_rejects_duplicate_ids_and_unknown_kinds(tmp_path):
    base = {"question": "q", "gold_sql": "select 1", "note": "n"}
    dup = tmp_path / "dup.json"
    dup.write_text(json.dumps([{"id": "a", "kind": "count", **base}, {"id": "a", "kind": "text", **base}]))
    with pytest.raises(SystemExit, match="not unique"):
        run_eval.load_questions(dup)
    odd = tmp_path / "odd.json"
    odd.write_text(json.dumps([{"id": "a", "kind": "boolean", **base}]))
    with pytest.raises(SystemExit, match="unknown answer kinds"):
        run_eval.load_questions(odd)
    assert [q["id"] for q in run_eval.load_questions()] == IDS


def test_site_tables_are_checked_for_presence_and_rows():
    partial = _stand_in(tables=("fct_trial_sites",))
    assert run_eval.missing_site_tables(partial) == ["marts.dim_site_location"]
    partial.close()
    full = _stand_in()
    assert run_eval.missing_site_tables(full) == []
    assert run_eval.empty_site_tables(full) == list(run_eval.SITE_TABLES)
    full.execute("insert into marts.fct_trial_sites (nct_id, city) values ('NCT00000001', 'Boston')")
    assert run_eval.empty_site_tables(full) == ["marts.dim_site_location"]
    full.close()


def test_run_refuses_without_the_site_tables_or_rows_or_the_right_snapshot(monkeypatch):
    """Each call exits before any question is asked, so nothing is written and no model is called."""
    def refuse(con, text):
        monkeypatch.setattr(run_eval.sqlguard, "sandbox_connection", lambda *a, **k: con)
        with pytest.raises(SystemExit, match=text):
            run_eval.run()

    refuse(_stand_in(tables=("dim_trial",)), "marts.fct_trial_sites and marts.dim_site_location not in")
    refuse(_stand_in(), "has no rows")
    populated = _stand_in()
    populated.execute("insert into marts.fct_trial_sites (nct_id, city) values ('NCT00000001', 'Boston')")
    populated.execute("insert into marts.dim_site_location (geo_key, city) values ('1.0000,2.0000', 'Boston')")
    monkeypatch.setattr(run_eval.config, "as_of_date", lambda: "2030-01-01")
    refuse(populated, "snapshot")


def test_gold_problem_flags_answers_that_cannot_be_graded():
    problem = run_eval.gold_problem
    assert problem("count", []) == "gold SQL returned no rows"
    assert "zero" in problem("count", [(0,)])
    assert problem("count", [(298,)]) is None
    assert problem("number", [(None,)]) == "gold answer is NULL"
    assert problem("number", [(0.0,)]) is None  # a zero percentage can be a real answer
    assert "tie" in problem("text", [("A", 5), ("B", 5)])
    assert problem("text", [("A", 6), ("B", 5)]) is None
    assert problem("text", [("A",)]) is None
    assert "more than" in problem("list", [("x",)] * (run_eval.MAX_LIST + 1))
    assert "NULL" in problem("list", [("x",), (None,)])
    assert problem("list", [("x",)] * run_eval.MAX_LIST) is None


def test_compute_gold_reports_failing_sql_instead_of_stopping():
    con = _stand_in()
    questions = [{"id": "ok", "kind": "count", "gold_sql": "select 3"},
                 {"id": "binder", "kind": "count", "gold_sql": "select nope from marts.dim_trial"},
                 {"id": "guard", "kind": "count", "gold_sql": "drop table marts.dim_trial"},
                 {"id": "empty", "kind": "text", "gold_sql": "select city, 1 from marts.fct_trial_sites"}]
    gold = run_eval.compute_gold(con, questions)
    con.close()
    assert gold["ok"] == {"rows": [(3,)], "problem": None}
    assert gold["binder"]["problem"].startswith("gold SQL failed")
    assert gold["guard"]["problem"].startswith("gold SQL failed")
    assert gold["empty"]["problem"] == "gold SQL returned no rows"


def test_grade_uses_readout_qa_matching_and_checks_the_full_list_length():
    grade = run_eval.grade
    assert grade("count", [(298,)], {"rows": [["Boston", 298.0]], "row_count": 1})
    assert not grade("count", [(298,)], {"rows": [[297]], "row_count": 1})
    assert grade("text", [("California", 3690), ("Texas", 3464)], {"rows": [["california", 3690]], "row_count": 1})
    assert grade("date", [(dt.date(2026, 11, 13),)], {"rows": [["2026-11-13"]], "row_count": 1})
    assert grade("number", [(21.8,)], {"rows": [[21.8000001]], "row_count": 1})
    assert not grade("number", [(450.5,)], {"rows": [[450]], "row_count": 1})
    gold = [("China",), ("Spain",), ("Australia",)]
    assert grade("list", gold, {"rows": [["Spain", 9], ["China", 8], ["Australia", 7]], "row_count": 3})
    assert not grade("list", gold, {"rows": [["Spain"], ["China"]], "row_count": 2})
    assert not grade("list", gold, {"rows": [["Spain"], ["China"], ["Australia"]], "row_count": 300})  # display cap
    assert not grade("count", [(298,)], {"rows": [], "row_count": 0})


class _Refused(Exception):
    kind, message, sql = "unsafe_sql", "refused by the SQL guard", "select 1; select 2"


def _answer(rows, seconds, cached=False):
    return {"sql": "select 1", "columns": ["x"], "rows": rows, "row_count": len(rows), "model": "m1",
            "seconds": seconds, "cached": cached}


def test_evaluate_and_summarize_count_what_the_report_needs():
    questions = [{"id": i, "kind": k, "question": f"q{i}", "note": "n"} for i, k in
                 (("a", "count"), ("b", "text"), ("c", "list"), ("d", "count"), ("e", "count"))]
    gold = {"a": {"rows": [(10,)], "problem": None},
            "b": {"rows": [("X", 9), ("Y", 3)], "problem": None},
            "c": {"rows": [("p",), ("q",)], "problem": None},
            "d": {"rows": [(1,)], "problem": None},
            "e": {"rows": [(0,)], "problem": "gold count is zero, so any empty answer would pass"}}
    answers = {"qa": _answer([[10]], 1.0), "qb": _answer([["Y", 3]], 2.0, cached=True),
               "qc": _answer([["p"], ["q"]], 7.0)}

    def ask_one(text):
        if text == "qd":
            raise _Refused()
        return answers[text]

    records = run_eval.evaluate(questions, gold, ask_one, log=lambda *_: None)
    assert [r["status"] for r in records] == ["ok", "ok", "ok", "unsafe_sql", "invalid_gold"]
    assert [r["correct"] for r in records] == [True, False, True, False, None]
    assert records[3]["error"] == "refused by the SQL guard" and records[3]["sql"] == "select 1; select 2"
    assert records[0]["gold"] == [[10]] and records[1]["gold"] == [["X", 9], ["Y", 3]]
    json.dumps(records)  # records.json must serialize

    metrics = run_eval.summarize(records, "2026-11-03")
    assert (metrics["questions"], metrics["scored"], metrics["correct"], metrics["accuracy"]) == (5, 4, 2, 0.5)
    assert metrics["by_kind"] == {"count": {"questions": 2, "correct": 1}, "list": {"questions": 1, "correct": 1},
                                  "text": {"questions": 1, "correct": 0}}
    assert metrics["outcomes"] == {"correct": 2, "invalid_gold": 1, "unsafe_sql": 1, "wrong_result": 1}
    assert metrics["answered"] == 3 and metrics["cached_answers"] == 1 and metrics["models"] == ["m1"]
    assert metrics["median_seconds"] == 2.0 and metrics["median_seconds_uncached"] == 4.0
    assert metrics["invalid_gold"] == [{"id": "e", "reason": gold["e"]["problem"]}]
    json.dumps(metrics)


def test_summarize_handles_a_run_with_no_answers():
    metrics = run_eval.summarize([], "2026-11-03")
    assert metrics["accuracy"] == 0.0 and metrics["median_seconds"] is None and metrics["models"] == []
