"""Text-to-SQL benchmark over the warehouse.

48 questions (36 single-step and 12 multi-step), each with a gold SQL query written in advance. Gold answers are computed by running that SQL on
the warehouse, so they always match the data. A model writes SQL for each question under two prompt
variants, the SQL runs in the sandbox (readout.sqlguard), and the result is compared with the gold answer.
  schema_only    table and column names with types
  schema_docs    the same plus column meanings, code values, the snapshot date and three worked examples
                 (none taken from the benchmark); on an SQL error the model gets one repair attempt
"""

from __future__ import annotations

import datetime as dt
import decimal
import json
import re
from concurrent.futures import ThreadPoolExecutor

from . import config, llm, sqlguard

OUT = config.RESULTS / "qa"
WORKERS = 3
AS_OF = "2026-11-03"

# id, kind of answer, question, gold SQL. Kinds: count, number, text, date, list
QUESTIONS: list[tuple[str, str, str, str]] = [
    ("q01", "count", "How many studies does Pfizer lead as the lead sponsor?",
     "select n_trials_as_lead from marts.dim_organization where org_name = 'Pfizer'"),
    ("q02", "count", "How many Phase 3 studies (phase exactly Phase 3, not Phase 2/3) does AstraZeneca lead?",
     "select count(*) from marts.bridge_trial_organization b join marts.dim_trial t using (nct_id) "
     "join marts.dim_organization o using (org_id) where o.org_name = 'AstraZeneca' and b.sponsor_role = 'lead' "
     "and t.phase = 'PHASE3'"),
    ("q03", "count", "How many studies led by Eli Lilly have a primary completion date in calendar year 2027?",
     "select count(*) from marts.bridge_trial_organization b join marts.dim_trial t using (nct_id) "
     "join marts.dim_organization o using (org_id) where o.org_name = 'Eli Lilly and Company' "
     "and b.sponsor_role = 'lead' and year(t.primary_completion_date) = 2027"),
    ("q04", "count", "How many studies led by the organization 'Novartis Pharmaceuticals' have the status RECRUITING?",
     "select count(*) from marts.bridge_trial_organization b join marts.dim_trial t using (nct_id) "
     "join marts.dim_organization o using (org_id) where o.org_name = 'Novartis Pharmaceuticals' "
     "and b.sponsor_role = 'lead' and t.overall_status = 'RECRUITING'"),
    ("q05", "count", "How many studies led by Bristol-Myers Squibb were terminated?",
     "select count(*) from marts.bridge_trial_organization b join marts.dim_trial t using (nct_id) "
     "join marts.dim_organization o using (org_id) where o.org_name = 'Bristol-Myers Squibb' "
     "and b.sponsor_role = 'lead' and t.overall_status = 'TERMINATED'"),
    ("q06", "count", "On how many studies is Regeneron a collaborator rather than the lead sponsor?",
     "select n_trials_as_collaborator from marts.dim_organization where org_name = 'Regeneron Pharmaceuticals'"),
    ("q07", "text", "Which organization leads the most studies?",
     "select org_name from marts.dim_organization order by n_trials_as_lead desc limit 1"),
    ("q08", "list", "Which 5 organizations have the most active Phase 3 or Phase 2/3 studies, counting both lead "
     "sponsor and collaborator roles? List their names.",
     "select org_name from marts.mart_org_pipeline order by n_active_phase3 desc limit 5"),
    ("q09", "count", "How many distinct organizations lead at least one study?",
     "select count(distinct org_id) from marts.bridge_trial_organization where sponsor_role = 'lead'"),
    ("q10", "number", "What is the median enrollment count of completed Phase 3 studies (phase exactly Phase 3)?",
     "select median(enrollment_count) from marts.dim_trial where phase = 'PHASE3' and overall_status = 'COMPLETED' "
     "and enrollment_count is not null"),
    ("q11", "count", "How many studies started in calendar year 2024?",
     "select count(*) from marts.dim_trial where year(start_date) = 2024"),
    ("q12", "number", "What percentage of Phase 2 studies (phase exactly Phase 2) have the status TERMINATED? "
     "Round to one decimal place.",
     "select round(100.0 * count(*) filter (where overall_status = 'TERMINATED') / count(*), 1) "
     "from marts.dim_trial where phase = 'PHASE2'"),
    ("q13", "count", "How many distinct active Phase 3 or Phase 2/3 studies have a primary completion date in the "
     "12 months after the 2026-11-03 snapshot (on or after 2026-11-03 and before 2027-11-03)?",
     "select count(distinct nct_id) from marts.mart_upcoming_readouts where is_phase3"),
    ("q14", "count", "How many organizations have at least one active Phase 3 or Phase 2/3 study with a primary "
     "completion date in the 12 months after the 2026-11-03 snapshot, in any role?",
     "select count(distinct org_id) from marts.mart_upcoming_readouts where is_phase3"),
    ("q15", "text", "Which organization has the most upcoming readouts (active Phase 2, 2/3 or 3 studies with "
     "primary completion in the 12 months after 2026-11-03, any role)?",
     "select org_name from marts.mart_org_pipeline order by n_readouts_12m desc limit 1"),
    ("q16", "date", "What is the date of Merck & Co.'s next primary completion of an active Phase 3 or Phase 2/3 "
     "study on or after 2026-11-03, in any role?",
     "select next_phase3_primary_completion from marts.mart_org_pipeline where org_name = 'Merck & Co., Inc.'"),
    ("q17", "list", "List the NCT IDs of Amgen's active Phase 3 or Phase 2/3 studies (any role) with a primary "
     "completion date in the 12 months after 2026-11-03.",
     "select nct_id from marts.mart_upcoming_readouts where org_name = 'Amgen' and is_phase3"),
    ("q18", "count", "In which month of 2027 do the most studies have an estimated (anticipated) primary "
     "completion date? Answer with the month number.",
     "select month(event_date) from marts.fct_trial_events where event_type = 'primary_completion' "
     "and date_type = 'ESTIMATED' and year(event_date) = 2027 group by 1 order by count(*) desc limit 1"),
    ("q19", "count", "How many primary completion dates in calendar year 2025 are actual rather than estimated?",
     "select count(*) from marts.fct_trial_events where event_type = 'primary_completion' "
     "and date_type = 'ACTUAL' and year(event_date) = 2025"),
    ("q20", "count", "How many studies have a primary completion date earlier than their start date?",
     "select count(*) from marts.dim_trial where primary_completion_date < start_date"),
    ("q21", "count", "How many studies list a condition containing the word 'obesity' (case-insensitive)?",
     "select count(*) from marts.dim_trial where conditions ilike '%obesity%'"),
    ("q22", "text", "Which organization leads the most studies whose conditions mention Alzheimer (case-insensitive)?",
     "select o.org_name from marts.bridge_trial_organization b join marts.dim_trial t using (nct_id) "
     "join marts.dim_organization o using (org_id) where b.sponsor_role = 'lead' and t.conditions ilike '%alzheimer%' "
     "group by o.org_name order by count(*) desc limit 1"),
    ("q23", "count", "How many different sponsor name spellings were merged into the organization Sanofi?",
     "select n_name_variants from marts.dim_organization where org_name = 'Sanofi'"),
    ("q24", "text", "Which organization has the most sponsor name variants merged into it?",
     "select org_name from marts.dim_organization order by n_name_variants desc limit 1"),
    ("q25", "text", "Among organizations that lead at least 100 studies, which has the highest share of its led "
     "studies terminated?",
     "select o.org_name from marts.bridge_trial_organization b join marts.dim_trial t using (nct_id) "
     "join marts.dim_organization o using (org_id) where b.sponsor_role = 'lead' group by o.org_name "
     "having count(*) >= 100 order by avg(case when t.overall_status = 'TERMINATED' then 1.0 else 0 end) desc limit 1"),
    ("q26", "count", "How many studies led by Gilead have posted results?",
     "select count(*) from marts.bridge_trial_organization b join marts.dim_trial t using (nct_id) "
     "join marts.dim_organization o using (org_id) where o.org_name = 'Gilead Sciences' and b.sponsor_role = 'lead' "
     "and t.has_results"),
    ("q27", "count", "What is the total enrollment across Takeda's active Phase 3 studies (phase exactly Phase 3, "
     "Takeda as lead sponsor)?",
     "select sum(t.enrollment_count) from marts.bridge_trial_organization b join marts.dim_trial t using (nct_id) "
     "join marts.dim_organization o using (org_id) where o.org_name = 'Takeda' and b.sponsor_role = 'lead' "
     "and t.phase = 'PHASE3' and t.is_active"),
    ("q28", "count", "How many Phase 1 studies (phase exactly Phase 1) that started in 2025 were led by "
     "organizations whose name contains 'Jiangsu'?",
     "select count(*) from marts.bridge_trial_organization b join marts.dim_trial t using (nct_id) "
     "join marts.dim_organization o using (org_id) where b.sponsor_role = 'lead' and o.org_name ilike '%jiangsu%' "
     "and t.phase = 'PHASE1' and year(t.start_date) = 2025"),
    ("q29", "count", "How many Phase 2 studies (phase exactly Phase 2) does the organization 'Janssen Research & Development, LLC' lead?",
     "select count(*) from marts.bridge_trial_organization b join marts.dim_trial t using (nct_id) "
     "join marts.dim_organization o using (org_id) where o.org_name = 'Janssen Research & Development, LLC' "
     "and b.sponsor_role = 'lead' and t.phase = 'PHASE2'"),
    ("q30", "text", "Which organization leads the most studies with the status WITHDRAWN?",
     "select o.org_name from marts.bridge_trial_organization b join marts.dim_trial t using (nct_id) "
     "join marts.dim_organization o using (org_id) where b.sponsor_role = 'lead' and t.overall_status = 'WITHDRAWN' "
     "group by o.org_name order by count(*) desc limit 1"),
    ("q31", "count", "How many studies led by the organization 'Hoffmann-La Roche' have an estimated (anticipated) primary "
     "completion date that is already before the 2026-11-03 snapshot?",
     "select count(*) from marts.bridge_trial_organization b join marts.dim_trial t using (nct_id) "
     "join marts.dim_organization o using (org_id) where o.org_name = 'Hoffmann-La Roche' and b.sponsor_role = 'lead' "
     "and t.primary_completion_date_type = 'ESTIMATED' and t.primary_completion_date < date '2026-11-03'"),
    ("q32", "count", "How many studies started in the first quarter (January through March) of 2025?",
     "select count(*) from marts.dim_trial where start_date >= date '2025-01-01' and start_date < date '2025-04-01'"),
    ("q33", "count", "How many upcoming readouts (active Phase 2, 2/3 or 3 studies with primary completion in the "
     "12 months after 2026-11-03) does AbbVie have as the lead sponsor?",
     "select count(*) from marts.mart_upcoming_readouts where org_name = 'AbbVie' and sponsor_role = 'lead'"),
    ("q34", "list", "Which organizations have more than 20 upcoming active Phase 3 or Phase 2/3 readouts in the "
     "12 months after 2026-11-03 (any role)? List their names.",
     "select org_name from marts.mart_org_pipeline where n_phase3_readouts_12m > 20"),
    ("q35", "count", "How many Phase 4 studies does Novo Nordisk lead?",
     "select count(*) from marts.bridge_trial_organization b join marts.dim_trial t using (nct_id) "
     "join marts.dim_organization o using (org_id) where o.org_name = 'Novo Nordisk A/S' and b.sponsor_role = 'lead' "
     "and t.phase = 'PHASE4'"),
    ("q36", "text", "Which phase code has the most active studies led by Pfizer?",
     "select t.phase from marts.bridge_trial_organization b join marts.dim_trial t using (nct_id) "
     "join marts.dim_organization o using (org_id) where o.org_name = 'Pfizer' and b.sponsor_role = 'lead' "
     "and t.is_active group by t.phase order by count(*) desc limit 1"),
    # Harder, multi-step questions (joins with roles, date arithmetic, ratios, ranking with offsets).
    ('q37', 'text', 'Which organization has the most active Phase 3 or Phase 2/3 studies where it is the lead sponsor (collaborations not counted)?',
     "select o.org_name from marts.bridge_trial_organization b join marts.dim_trial t using (nct_id) join marts.dim_organization o using (org_id) where b.sponsor_role = 'lead' and t.is_active and t.phase in ('PHASE3', 'PHASE2_PHASE3') group by o.org_name order by count(*) desc limit 1"),
    ('q38', 'number', 'For completed studies led by Pfizer, what is the median number of days between the start date and the primary completion date?',
     "select median(datediff('day', t.start_date, t.primary_completion_date)) from marts.bridge_trial_organization b join marts.dim_trial t using (nct_id) join marts.dim_organization o using (org_id) where o.org_name = 'Pfizer' and b.sponsor_role = 'lead' and t.overall_status = 'COMPLETED'"),
    ('q39', 'count', 'Among organizations that lead at least 10 active studies, how many lead more active Phase 3 or Phase 2/3 studies than active Phase 2 studies (phase exactly Phase 2)?',
     "select count(*) from (select b.org_id, count(*) filter (where t.phase in ('PHASE3', 'PHASE2_PHASE3')) as p3, count(*) filter (where t.phase = 'PHASE2') as p2, count(*) as n from marts.bridge_trial_organization b join marts.dim_trial t using (nct_id) where b.sponsor_role = 'lead' and t.is_active group by b.org_id) where n >= 10 and p3 > p2"),
    ('q40', 'count', 'How many studies led by AstraZeneca also have at least one collaborator organization?',
     "select count(distinct b.nct_id) from marts.bridge_trial_organization b join marts.dim_organization o using (org_id) where o.org_name = 'AstraZeneca' and b.sponsor_role = 'lead' and exists (select 1 from marts.bridge_trial_organization c where c.nct_id = b.nct_id and c.sponsor_role = 'collaborator')"),
    ('q41', 'number', 'By what percent did the number of studies started in 2024 change compared with 2023? Round to one decimal place.',
     'select round(100.0 * (count(*) filter (where year(start_date) = 2024) - count(*) filter (where year(start_date) = 2023)) / count(*) filter (where year(start_date) = 2023), 1) from marts.dim_trial'),
    ('q42', 'count', 'How many distinct organizations appear as collaborators on studies led by Merck & Co.?',
     "select count(distinct c.org_id) from marts.bridge_trial_organization l join marts.dim_organization lo on lo.org_id = l.org_id join marts.bridge_trial_organization c on c.nct_id = l.nct_id and c.sponsor_role = 'collaborator' where lo.org_name = 'Merck & Co., Inc.' and l.sponsor_role = 'lead'"),
    ('q43', 'number', 'What percentage of the estimated (anticipated) primary completion dates in 2027 belong to Phase 3 or Phase 2/3 studies? Round to one decimal place.',
     "select round(100.0 * count(*) filter (where t.phase in ('PHASE3', 'PHASE2_PHASE3')) / count(*), 1) from marts.fct_trial_events e join marts.dim_trial t using (nct_id) where e.event_type = 'primary_completion' and e.date_type = 'ESTIMATED' and year(e.event_date) = 2027"),
    ('q44', 'count', 'How many studies list conditions that mention both diabetes and obesity (case-insensitive)?',
     "select count(*) from marts.dim_trial where conditions ilike '%diabetes%' and conditions ilike '%obesity%'"),
    ('q45', 'text', 'Which quarter of 2026 had the most study starts? Answer in the form 2026-Q1.',
     'select d.year_quarter from marts.dim_trial t join marts.dim_date d on d.date_day = t.start_date where d.year = 2026 group by d.year_quarter order by count(*) desc limit 1'),
    ('q46', 'text', 'Among the active studies Eli Lilly leads, which phase code has the second highest number of studies?',
     "select t.phase from marts.bridge_trial_organization b join marts.dim_trial t using (nct_id) join marts.dim_organization o using (org_id) where o.org_name = 'Eli Lilly and Company' and b.sponsor_role = 'lead' and t.is_active group by t.phase order by count(*) desc limit 1 offset 1"),
    ('q47', 'count', 'How many organizations lead more than 50 studies but have only one sponsor name variant?',
     'select count(*) from marts.dim_organization where n_trials_as_lead > 50 and n_name_variants = 1'),
    ('q48', 'count', 'How many studies among the upcoming readouts (12 months after 2026-11-03) are attached to more than one organization?',
     'select count(*) from (select nct_id from marts.mart_upcoming_readouts group by nct_id having count(distinct org_id) > 1)'),
]

TABLES = {
    "marts.dim_organization": ["org_id", "org_name", "n_name_variants", "n_names_stating_a_parent", "n_trials",
                               "n_trials_as_lead", "n_trials_as_collaborator"],
    "marts.dim_trial": ["nct_id", "brief_title", "official_title", "acronym", "phase", "phase_label", "phase_number",
                        "overall_status", "is_active", "why_stopped", "enrollment_count", "enrollment_type",
                        "allocation", "primary_purpose", "start_date", "start_date_type", "primary_completion_date",
                        "primary_completion_date_type", "completion_date", "completion_date_type", "first_posted_date",
                        "last_update_date", "has_results", "lead_sponsor_name", "lead_org_id", "lead_org_name",
                        "conditions", "n_conditions", "interventions", "n_interventions"],
    "marts.bridge_trial_organization": ["nct_id", "org_id", "sponsor_role", "sponsor_name"],
    "marts.fct_trial_events": ["event_id", "nct_id", "lead_org_id", "event_type", "event_date", "date_key",
                               "date_type", "date_precision"],
    "marts.dim_date": ["date_key", "date_day", "year", "quarter", "month", "month_name", "year_quarter", "is_weekend"],
    "marts.mart_upcoming_readouts": ["org_id", "org_name", "sponsor_role", "nct_id", "brief_title", "phase",
                                     "phase_label", "is_phase3", "overall_status", "primary_completion_date",
                                     "primary_completion_date_type", "enrollment_count", "conditions",
                                     "days_until_primary_completion"],
    "marts.mart_org_pipeline": ["org_id", "org_name", "n_name_variants", "n_trials", "n_trials_as_lead",
                                "n_active_trials", "n_active_phase1", "n_active_phase2", "n_active_phase3",
                                "n_terminated", "n_completed", "n_readouts_12m", "n_phase3_readouts_12m",
                                "next_phase3_primary_completion"],
}

DOCS = f"""Conventions:
- The data is a snapshot of ClinicalTrials.gov taken on {AS_OF}: interventional studies with an industry lead
  sponsor and a primary completion date on or after 2020-01-01. "Today" means {AS_OF}.
- An organization is a resolved company group: spellings and stated subsidiaries of sponsor names rolled into one
  row of marts.dim_organization. org_name is its display name and may differ from the everyday name
  (for example 'Eli Lilly and Company', 'Hoffmann-La Roche' for Roche, 'Merck & Co., Inc.'). Match names with
  ILIKE but beware look-alikes such as 'Merck KGaA, Darmstadt, Germany'.
- marts.bridge_trial_organization links studies to organizations. sponsor_role is 'lead' or 'collaborator'.
  "X leads a study" means sponsor_role = 'lead'. "Any role" means lead or collaborator.
- dim_trial.phase codes: EARLY_PHASE1, PHASE1, PHASE1_PHASE2, PHASE2, PHASE2_PHASE3, PHASE3, PHASE4,
  NA (no phase), UNKNOWN. "Phase 3 or Phase 2/3" means phase in ('PHASE3', 'PHASE2_PHASE3').
- dim_trial.overall_status values: RECRUITING, NOT_YET_RECRUITING, ACTIVE_NOT_RECRUITING,
  ENROLLING_BY_INVITATION (these four make is_active true), COMPLETED, TERMINATED, WITHDRAWN, SUSPENDED, UNKNOWN.
- Date types (start_date_type, primary_completion_date_type, fct_trial_events.date_type) are ACTUAL or
  ESTIMATED (anticipated). Month-only registry dates are stored as the last day of that month.
- dim_trial.conditions and dim_trial.interventions are '; '-separated text lists.
- fct_trial_events has one row per study milestone: event_type in ('start', 'primary_completion', 'completion').
- marts.mart_upcoming_readouts already holds only active PHASE2, PHASE2_PHASE3 and PHASE3 studies with a primary
  completion date on or after {AS_OF} and before 2027-11-03, one row per study and organization (any role);
  is_phase3 is true for PHASE3 and PHASE2_PHASE3.
- marts.mart_org_pipeline has one row per organization: n_active_phase3 counts active PHASE3 and PHASE2_PHASE3
  studies in any role; n_readouts_12m and n_phase3_readouts_12m count rows of mart_upcoming_readouts;
  next_phase3_primary_completion is the earliest upcoming PHASE3 or PHASE2_PHASE3 primary completion.

Worked examples (not part of the test):
Q: How many studies does Bayer lead in total?
SQL: select n_trials_as_lead from marts.dim_organization where org_name ilike 'bayer'
Q: How many active Phase 2 studies (phase exactly Phase 2) does Amgen lead?
SQL: select count(*) from marts.bridge_trial_organization b join marts.dim_trial t using (nct_id)
     join marts.dim_organization o using (org_id)
     where o.org_name ilike 'amgen' and b.sponsor_role = 'lead' and t.phase = 'PHASE2' and t.is_active
Q: How many estimated primary completions fall in 2028?
SQL: select count(*) from marts.fct_trial_events
     where event_type = 'primary_completion' and date_type = 'ESTIMATED' and year(event_date) = 2028
"""

SYSTEM = ("You translate questions about a clinical-trial warehouse into one DuckDB SQL query. Use only the tables "
          "and columns given. Return a single read-only SELECT statement and nothing else, as JSON.")
SCHEMA = {
    "type": "object",
    "properties": {"sql": {"type": "string"}},
    "required": ["sql"],
    "additionalProperties": False,
}
VARIANTS = ("schema_only", "schema_docs")


def schema_text(con) -> str:
    lines = []
    for table, cols in TABLES.items():
        schema, name = table.split(".")
        types = dict(con.execute(
            "select column_name, data_type from information_schema.columns where table_schema = ? and table_name = ?",
            [schema, name]).fetchall())
        lines.append(f"{table}(" + ", ".join(f"{c} {types.get(c, '?')}" for c in cols) + ")")
    return "\n".join(lines)


def prompt(variant: str, schema: str, question: str) -> str:
    parts = ["Tables:\n" + schema]
    if variant == "schema_docs":
        parts.append(DOCS)
    parts.append(f"Question: {question}\nReturn JSON with one key, sql.")
    return "\n\n".join(parts)


# ---------- comparing answers ----------

def _norm(v):
    if v is None:
        return None
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()[:10]
    if isinstance(v, decimal.Decimal):
        v = float(v)
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return float(v)
    return str(v).strip().lower()


def _equal(a, b, kind: str = "text") -> bool:
    """Counts must match exactly; other numbers may differ by rounding (0.05 absolute or 0.1% relative)."""
    a, b = _norm(a), _norm(b)
    if isinstance(a, str) and isinstance(b, float) or isinstance(a, float) and isinstance(b, str):
        try:
            a, b = float(a), float(b)
        except ValueError:
            return False
    if isinstance(a, float) and isinstance(b, float):
        if kind == "count":
            return a == b
        return abs(a - b) <= max(0.05, 0.001 * abs(b))
    return a == b


def matches(kind: str, gold_rows: list[tuple], pred_rows: list[tuple]) -> bool:
    """Scalar answers: the first predicted row must contain the gold value. Lists: some predicted column must hold
    exactly the gold values, in any order."""
    if kind == "list":
        gold = sorted(str(_norm(r[0])) for r in gold_rows)
        if len(pred_rows) != len(gold):
            return False
        for col in range(len(pred_rows[0]) if pred_rows else 0):
            if sorted(str(_norm(r[col])) for r in pred_rows) == gold:
                return True
        return False
    if not gold_rows or not pred_rows:
        return False
    target = gold_rows[0][0]
    return any(_equal(cell, target, kind) for cell in pred_rows[0])


def gold_answers(con) -> dict[str, list[tuple]]:
    return {qid: sqlguard.run(con, sql)[1] for qid, _, _, sql in QUESTIONS}


def _ask(variant: str, schema: str, qid: str, question: str, kind: str, gold_rows: list[tuple], con_factory) -> dict:
    rec = {"id": qid, "variant": variant, "question": question, "calls": 0}
    msg = prompt(variant, schema, question)
    for attempt in range(2 if variant == "schema_docs" else 1):
        try:
            ans = llm.complete_json(SYSTEM, msg, SCHEMA, task=f"qa_{variant}")
        except llm.LLMUnavailable as exc:
            rec.update(status="llm_unavailable", error=str(exc))
            return rec
        except llm.LLMError as exc:
            rec.update(status="llm_error", error=str(exc)[:300])
            return rec
        rec["calls"] += 1
        rec["model"] = ans["_meta"].get("model")
        sql = (ans.get("sql") or "").strip()
        rec["sql"] = sql
        con = con_factory()
        try:
            cols, rows = sqlguard.run(con, sql)
        except sqlguard.UnsafeSQL as exc:
            rec.update(status="guard_rejected", error=str(exc))
        except Exception as exc:  # DuckDB parser, binder or runtime error
            rec.update(status="sql_error", error=str(exc).splitlines()[0][:300])
        else:
            rec.update(status="ok", result=[[_norm(v) for v in r] for r in rows[:20]], n_rows=len(rows),
                       correct=matches(kind, gold_rows, rows))
            rec["repaired"] = attempt == 1
            return rec
        finally:
            con.close()
        if variant == "schema_docs" and attempt == 0:  # one repair attempt with the error message
            msg = msg + f"\n\nYour previous SQL was:\n{sql}\nIt failed with: {rec['error']}\nFix it."
    rec["correct"] = False
    return rec


def run(variants: tuple[str, ...] = VARIANTS) -> dict:
    con = sqlguard.sandbox_connection()
    schema = schema_text(con)
    gold = gold_answers(con)
    con.close()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "questions.json").write_text(json.dumps(
        [{"id": q, "kind": k, "question": text, "gold_sql": sql,
          "gold_answer": [[_norm(v) for v in r] for r in gold[q]]} for q, k, text, sql in QUESTIONS], indent=1) + "\n")
    jobs = [(v, q, text, kind) for v in variants for q, kind, text, _ in QUESTIONS]
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        records = list(pool.map(
            lambda j: _ask(j[0], schema, j[1], j[2], j[3], gold[j[1]], sqlguard.sandbox_connection), jobs))
    with (OUT / "predictions.jsonl").open("w") as fh:
        for r in records:
            fh.write(json.dumps(r, default=str) + "\n")
    return score(records)


def score(records: list[dict] | None = None) -> dict:
    """Score saved predictions (re-runnable without any model calls)."""
    if records is None:
        records = [json.loads(l) for l in (OUT / "predictions.jsonl").read_text().splitlines() if l.strip()]
    # recompute correctness from the saved result rows, so a change to the comparison rules applies everywhere
    saved = {q["id"]: q for q in json.loads((OUT / "questions.json").read_text())}
    for r in records:
        if r.get("status") == "ok" and r["id"] in saved:
            q = saved[r["id"]]
            rows = [tuple(x) for x in r.get("result") or []]
            if q["kind"] == "list" and r.get("n_rows", len(rows)) != len(q["gold_answer"]):
                r["correct"] = False
            else:
                r["correct"] = matches(q["kind"], [tuple(x) for x in q["gold_answer"]], rows)
    review = {}
    review_file = config.RESULTS / "qa" / "failure_review.csv"
    if review_file.exists():
        import csv
        with review_file.open() as fh:
            review = {(r["id"], r["variant"]): r["category"] for r in csv.DictReader(fh)}
    out = {"questions": len(QUESTIONS), "variants": {}}
    for v in sorted({r["variant"] for r in records}):
        rs = [r for r in records if r["variant"] == v]
        answered = [r for r in rs if r.get("status") not in ("llm_unavailable", "llm_error")]
        correct = sum(1 for r in rs if r.get("correct"))
        statuses = {}
        for r in rs:
            key = "correct" if r.get("correct") else ("wrong_result" if r.get("status") == "ok" else r.get("status"))
            statuses[key] = statuses.get(key, 0) + 1
        categories = {}
        for r in rs:
            if not r.get("correct") and (r["id"], v) in review:
                c = review[(r["id"], v)]
                categories[c] = categories.get(c, 0) + 1
        out["variants"][v] = {
            "answered": len(answered),
            "correct": correct,
            "execution_accuracy": round(correct / len(rs), 3) if rs else 0.0,
            "outcomes": statuses,
            "repaired_after_error": sum(1 for r in rs if r.get("repaired")),
            "answers_from_model": sum(r.get("calls", 0) for r in rs),
            "failure_categories_hand_reviewed": categories,
        }
    models = sorted({r.get("model") for r in records if r.get("model")})
    out["models"] = models
    (OUT / "metrics.json").write_text(json.dumps(out, indent=2) + "\n")
    return out
