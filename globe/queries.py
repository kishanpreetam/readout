"""Filter validation and parameterized SQL for the Readout Globe API.

Every request value is checked here against a known value set, a pattern or a range, then bound as a
DuckDB parameter. No request value is ever formatted into SQL text. The functions that run SQL take a
cursor, so each request uses its own cursor on the server's single read-only connection.
"""

from __future__ import annotations

import calendar
import datetime as dt
import re
import threading
import unicodedata
from dataclasses import dataclass

import duckdb

# dim_trial.phase values, in the order the page lists them, with dim_trial.phase_label.
PHASES = {
    "EARLY_PHASE1": "Early Phase 1",
    "PHASE1": "Phase 1",
    "PHASE1_PHASE2": "Phase 1/2",
    "PHASE2": "Phase 2",
    "PHASE2_PHASE3": "Phase 2/3",
    "PHASE3": "Phase 3",
    "PHASE4": "Phase 4",
    "NA": "Not applicable",
    "UNKNOWN": "Unknown",
}
# dim_trial.overall_status values: (label, is_active). The four active ones make dim_trial.is_active true.
STATUSES = {
    "RECRUITING": ("Recruiting", True),
    "NOT_YET_RECRUITING": ("Not yet recruiting", True),
    "ENROLLING_BY_INVITATION": ("Enrolling by invitation", True),
    "ACTIVE_NOT_RECRUITING": ("Active, not recruiting", True),
    "COMPLETED": ("Completed", False),
    "TERMINATED": ("Terminated", False),
    "SUSPENDED": ("Suspended", False),
    "WITHDRAWN": ("Withdrawn", False),
    "UNKNOWN": ("Unknown status", False),
}
MONTHS = {"min": "2018-01", "max": "2030-12"}  # the time slider's range

DUE_MONTHS = 12  # "due": active, primary completion on or after as_of and before as_of plus 12 months
MAX_POINTS = 20_000
MAX_POINT_TRIALS = 100
MAX_NCT = 500
MAX_LIST_ITEMS = 20  # phase and status lists; there are only nine values of each
MAX_ORGS = 10
Q_LEN = (2, 80)
ORG_Q_LEN = (2, 60)
DUE_WITHIN = (1, 36)
ORG_ID_MAX = 2**31 - 1  # dim_trial.lead_org_id is an INTEGER
YEARS = (1900, 2100)
QUERY_TIMEOUT_S = 20  # the same limit sqlguard puts on model-written SQL

FILTER_PARAMS = ("phase", "status", "org_id", "q", "active_on", "due_within", "nct")
# re.ASCII: \d means 0-9 only, not every Unicode digit
NCT_RE = re.compile(r"NCT\d{8}", re.ASCII)
GEO_KEY_RE = re.compile(r"-?\d{1,3}\.\d{4},-?\d{1,3}\.\d{4}", re.ASCII)  # printf('%.4f,%.4f', lat, lon)
MONTH_RE = re.compile(r"(\d{4})-(\d{2})", re.ASCII)
INT_RE = re.compile(r"\d{1,10}", re.ASCII)
NAME_RE = re.compile(r"[a-z_]{1,40}", re.ASCII)
# Control, format, surrogate, private use and line or paragraph separator characters are refused everywhere.
BAD_CHAR_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Zl", "Zp"})

CTGOV_STUDY_URL = "https://clinicaltrials.gov/study/"


class BadFilter(ValueError):
    """A request value failed validation; the API answers 400 with kind bad_filter."""


@dataclass(frozen=True)
class Filters:
    phases: tuple[str, ...] = ()
    statuses: tuple[str, ...] = ()
    org_id: int | None = None
    q: str | None = None
    active_on: str | None = None  # YYYY-MM
    due_within: int | None = None
    ncts: tuple[str, ...] = ()


# ---------- validation ----------

def has_bad_chars(text: str) -> bool:
    return any(unicodedata.category(ch) in BAD_CHAR_CATEGORIES for ch in text)


def _comma_list(name: str, value: str, allowed, limit: int) -> tuple[str, ...]:
    items = [item.strip() for item in value.split(",")]
    if len(items) > limit:
        raise BadFilter(f"{name}: at most {limit} values")
    out: list[str] = []
    for item in items:
        if item not in allowed:
            raise BadFilter(f"{name}: unknown value" if item else f"{name}: empty value in list")
        if item not in out:
            out.append(item)
    return tuple(out)


def _int_in(name: str, value: str, low: int, high: int) -> int:
    if not INT_RE.fullmatch(value) or not low <= int(value) <= high:
        raise BadFilter(f"{name}: must be a whole number from {low} to {high}")
    return int(value)


def _text(name: str, value: str, low: int, high: int) -> str:
    if has_bad_chars(value):
        raise BadFilter(f"{name}: control characters are not allowed")
    if not low <= len(value) <= high:
        raise BadFilter(f"{name}: must be {low} to {high} characters")
    return value


def check_month(name: str, value: str) -> str:
    m = MONTH_RE.fullmatch(value)
    if not m or not 1 <= int(m.group(2)) <= 12 or not YEARS[0] <= int(m.group(1)) <= YEARS[1]:
        raise BadFilter(f"{name}: must be a month written YYYY-MM")
    return value


def check_nct(value: str) -> str:
    if not isinstance(value, str) or not NCT_RE.fullmatch(value):
        raise BadFilter("nct_id: must look like NCT01234567")
    return value


def check_geo_key(value: str) -> str:
    if not isinstance(value, str) or not GEO_KEY_RE.fullmatch(value):
        raise BadFilter("geo_key: must look like 42.3601,-71.0589")
    return value


def _single_values(items: list[tuple[str, str]], allowed: tuple[str, ...]) -> dict[str, str]:
    """Query parameters as a dict. Unknown or repeated names are refused; empty values count as not given."""
    values: dict[str, str] = {}
    for name, value in items:
        if name not in allowed:
            shown = f" {name!r}" if NAME_RE.fullmatch(name) else ""
            raise BadFilter(f"unknown parameter{shown}; allowed: {', '.join(allowed)}")
        if name in values:
            raise BadFilter(f"{name}: given more than once")
        values[name] = value
    return {k: v.strip() for k, v in values.items() if v.strip()}


def parse_filters(items: list[tuple[str, str]]) -> Filters:
    """Validate the common filters (a list of query-string pairs) into a Filters value."""
    v = _single_values(items, FILTER_PARAMS)
    phases = _comma_list("phase", v["phase"], PHASES, MAX_LIST_ITEMS) if "phase" in v else ()
    statuses = _comma_list("status", v["status"], STATUSES, MAX_LIST_ITEMS) if "status" in v else ()
    org_id = _int_in("org_id", v["org_id"], 0, ORG_ID_MAX) if "org_id" in v else None
    q = _text("q", v["q"], *Q_LEN) if "q" in v else None
    active_on = check_month("active_on", v["active_on"]) if "active_on" in v else None
    due_within = _int_in("due_within", v["due_within"], *DUE_WITHIN) if "due_within" in v else None
    ncts: tuple[str, ...] = ()
    if "nct" in v:
        items_ = [item.strip() for item in v["nct"].split(",")]
        if len(items_) > MAX_NCT:
            raise BadFilter(f"nct: at most {MAX_NCT} NCT IDs")
        for item in items_:
            if not NCT_RE.fullmatch(item):
                raise BadFilter("nct: every item must look like NCT01234567")
        ncts = tuple(dict.fromkeys(items_))
    return Filters(phases, statuses, org_id, q, active_on, due_within, ncts)


def parse_org_query(items: list[tuple[str, str]]) -> str:
    v = _single_values(items, ("q",))
    if "q" not in v:
        raise BadFilter(f"q: required, {ORG_Q_LEN[0]} to {ORG_Q_LEN[1]} characters")
    return _text("q", v["q"], *ORG_Q_LEN)


# ---------- dates ----------

def add_months(day: dt.date, months: int) -> dt.date:
    """The same day `months` later, clamped to the month's last day (as DuckDB's date + interval does)."""
    index = day.month - 1 + months
    year, month = day.year + index // 12, index % 12 + 1
    return dt.date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def month_bounds(month: str) -> tuple[dt.date, dt.date]:
    year, mon = int(month[:4]), int(month[5:7])
    return dt.date(year, mon, 1), dt.date(year, mon, calendar.monthrange(year, mon)[1])


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


# ---------- SQL ----------

def where_clause(f: Filters, as_of: dt.date) -> tuple[str, dict]:
    """The WHERE text over marts.dim_trial (alias t) and its parameters. Values are only ever parameters."""
    parts: list[str] = []
    params: dict = {}
    if f.phases:
        parts.append("list_contains($phases, t.phase)")
        params["phases"] = list(f.phases)
    if f.statuses:
        parts.append("list_contains($statuses, t.overall_status)")
        params["statuses"] = list(f.statuses)
    if f.org_id is not None:
        parts.append("t.lead_org_id = $org_id")
        params["org_id"] = f.org_id
    if f.q is not None:
        parts.append("(contains(lower(t.brief_title), lower($q)) or contains(lower(t.conditions), lower($q))"
                     " or contains(lower(t.interventions), lower($q)))")
        params["q"] = f.q
    if f.active_on is not None:
        first, last = month_bounds(f.active_on)
        parts.append("t.start_date <= $active_last and t.primary_completion_date >= $active_first")
        params.update(active_first=first, active_last=last)
    if f.due_within is not None:
        parts.append("t.is_active and t.primary_completion_date >= $within_start"
                     " and t.primary_completion_date < $within_end")
        params.update(within_start=as_of, within_end=add_months(as_of, f.due_within))
    if f.ncts:
        parts.append("t.nct_id in (select unnest($ncts::varchar[]))")
        params["ncts"] = list(f.ncts)
    return (" and ".join(parts) or "true"), params


DUE_SQL = ("coalesce(t.is_active and t.primary_completion_date >= $as_of"
           " and t.primary_completion_date < $due_end, false)")


def _due_params(as_of: dt.date) -> dict:
    return {"as_of": as_of, "due_end": add_months(as_of, DUE_MONTHS)}


def _selected(f: Filters, as_of: dt.date) -> tuple[str, dict]:
    """A CTE named sel: the matching trials with their lead sponsor and due flag."""
    where, params = where_clause(f, as_of)
    sql = f"""sel as (
    select t.nct_id, coalesce(t.lead_org_name, t.lead_sponsor_name) as org, {DUE_SQL} as due
    from marts.dim_trial as t
    where {where}
)"""
    return sql, {**params, **_due_params(as_of)}


def run(cur: duckdb.DuckDBPyConnection, sql: str, params: dict | None = None,
        timeout: float = QUERY_TIMEOUT_S) -> tuple[list[str], list[tuple]]:
    """Execute one parameterized statement with a timeout; returns column names and all rows."""
    timer = threading.Timer(timeout, cur.interrupt)
    timer.start()
    try:
        result = cur.execute(sql, params or {})
        cols = [d[0] for d in result.description]
        return cols, result.fetchall()
    finally:
        timer.cancel()


def _dicts(cols: list[str], rows: list[tuple]) -> list[dict]:
    return [dict(zip(cols, r)) for r in rows]


REQUIRED_TABLES = ("dim_trial", "fct_trial_sites", "dim_site_location")


def missing_tables(cur) -> list[str]:
    _, rows = run(cur, "select table_name from information_schema.tables where table_schema = 'marts'")
    present = {r[0] for r in rows}
    return [f"marts.{t}" for t in REQUIRED_TABLES if t not in present]


def meta(cur, as_of: dt.date) -> dict:
    cols, rows = run(cur, """
        select
            (select count(*) from marts.dim_trial) as trials,
            (select count(distinct nct_id) from marts.fct_trial_sites where geo_key is not null) as trials_with_geo,
            (select count(*) from marts.fct_trial_sites) as sites,
            (select count(*) from marts.dim_site_location) as points
    """)
    counts = dict(zip(cols, rows[0]))
    phase_counts = dict(run(cur, "select phase, count(*) from marts.dim_trial group by phase")[1])
    status_counts = dict(run(cur, "select overall_status, count(*) from marts.dim_trial group by overall_status")[1])
    return {
        "as_of": as_of.isoformat(),
        **counts,
        "phases": [{"value": v, "label": label, "count": phase_counts.get(v, 0)} for v, label in PHASES.items()],
        "statuses": [{"value": v, "label": label, "active": active, "count": status_counts.get(v, 0)}
                     for v, (label, active) in STATUSES.items()],
        "months": dict(MONTHS),
    }


def points(cur, f: Filters, as_of: dt.date, limit: int | None = None) -> dict:
    """GeoJSON: one feature per point with at least one matching trial, most trials first, at most MAX_POINTS."""
    limit = MAX_POINTS if limit is None else limit
    sel, params = _selected(f, as_of)
    # pairs: one row per point and matching trial, with that trial's site rows there. kept: the points shown.
    # top_org: the most frequent lead sponsor among a point's matching trials; ties go to the first name.
    # all_trials: distinct trials across the kept points, for the summary.
    sql = f"""
with {sel},
pairs as materialized (
    select s.geo_key, s.nct_id, any_value(sel.org) as org, any_value(sel.due) as due, count(*) as n_sites
    from marts.fct_trial_sites as s
    join sel on sel.nct_id = s.nct_id
    where s.geo_key is not null
    group by s.geo_key, s.nct_id
),
kept as materialized (
    select geo_key, count(*) as trials, count(*) filter (where due) as due, sum(n_sites) as sites
    from pairs
    group by geo_key
    order by trials desc, geo_key
    limit $limit
),
org_counts as (
    select geo_key, org, count(*) as n
    from pairs
    where org is not null and geo_key in (select geo_key from kept)
    group by geo_key, org
),
top_org as (
    select geo_key, first(org order by n desc, org) as top_org
    from org_counts
    group by geo_key
)
select
    k.geo_key, round(l.lon, 4) as lon, round(l.lat, 4) as lat, l.city, l.state, l.country,
    k.trials, k.due, k.sites, o.top_org,
    (select count(distinct nct_id) from pairs where geo_key in (select geo_key from kept)) as all_trials
from kept as k
join marts.dim_site_location as l on l.geo_key = k.geo_key
left join top_org as o on o.geo_key = k.geo_key
order by k.trials desc, k.geo_key
"""
    _, rows = run(cur, sql, {**params, "limit": limit})
    features = []
    sites = 0
    for geo_key, lon, lat, city, state, country, trials, due, n_sites, top_org, _all in rows:
        sites += n_sites
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {"geo_key": geo_key, "city": city, "state": state, "country": country,
                           "trials": trials, "due": due, "top_org": top_org},
        })
    summary = {"trials": rows[0][-1] if rows else 0, "sites": int(sites), "points": len(features)}
    return {"type": "FeatureCollection", "summary": summary, "features": features}


def location(cur, geo_key: str) -> dict | None:
    cols, rows = run(cur, "select geo_key, city, state, country from marts.dim_site_location where geo_key = $geo_key",
                     {"geo_key": geo_key})
    return dict(zip(cols, rows[0])) if rows else None


def point_trials(cur, geo_key: str, f: Filters, as_of: dt.date, limit: int | None = None) -> dict | None:
    """The matching trials with a site at one point, soonest primary completion first. None if the point is unknown."""
    limit = MAX_POINT_TRIALS if limit is None else limit
    place = location(cur, geo_key)
    if place is None:
        return None
    sel, params = _selected(f, as_of)
    sql = f"""
with {sel},
here as (
    select s.nct_id, list_sort(list_distinct(list(s.facility))) as facilities
    from marts.fct_trial_sites as s
    where s.geo_key = $geo_key
    group by s.nct_id
)
select
    t.nct_id, t.brief_title as title, t.phase, t.phase_label, t.overall_status as status,
    sel.org as lead_org_name, t.conditions, t.start_date, t.primary_completion_date, sel.due,
    h.facilities, count(*) over () as total
from here as h
join sel on sel.nct_id = h.nct_id
join marts.dim_trial as t on t.nct_id = h.nct_id
order by t.primary_completion_date asc nulls last, t.nct_id
limit $limit
"""
    cols, rows = run(cur, sql, {**params, "geo_key": geo_key, "limit": limit})
    trials = []
    for r in _dicts(cols, rows):
        r.pop("total")
        r["start_date"] = _iso(r["start_date"])
        r["primary_completion_date"] = _iso(r["primary_completion_date"])
        r["facilities"] = list(r["facilities"] or [])
        r["url"] = CTGOV_STUDY_URL + r["nct_id"]
        trials.append(r)
    total = rows[0][cols.index("total")] if rows else 0
    return {**place, "total": total, "trials": trials}


def trial(cur, nct_id: str, as_of: dt.date) -> dict | None:
    cols, rows = run(cur, f"""
        select
            t.nct_id, t.brief_title as title, t.official_title, t.phase, t.phase_label, t.overall_status as status,
            t.is_active, t.lead_org_id, coalesce(t.lead_org_name, t.lead_sponsor_name) as lead_org_name,
            t.conditions, t.interventions, t.enrollment_count, t.start_date, t.primary_completion_date,
            {DUE_SQL} as due, t.brief_summary
        from marts.dim_trial as t
        where t.nct_id = $nct_id
    """, {"nct_id": nct_id, **_due_params(as_of)})
    if not rows:
        return None
    out = dict(zip(cols, rows[0]))
    _, site_rows = run(cur, """
        with by_country as (
            select country, count(*) as n from marts.fct_trial_sites where nct_id = $nct_id group by country
        )
        select
            coalesce(sum(n), 0) as n_sites,
            count(country) as n_countries,
            coalesce(list(country order by n desc, country) filter (where country is not null), []) as countries
        from by_country
    """, {"nct_id": nct_id})
    n_sites, n_countries, countries = site_rows[0]
    summary = out.pop("brief_summary")
    out["start_date"] = _iso(out["start_date"])
    out["primary_completion_date"] = _iso(out["primary_completion_date"])
    out.update(n_sites=int(n_sites), n_countries=n_countries, countries=list(countries),
               brief_summary=summary, url=CTGOV_STUDY_URL + nct_id)
    return out


def orgs(cur, q: str, limit: int | None = None) -> dict:
    """Lead sponsors whose organization name, or a sponsor name rolled into it, contains q; most trials first."""
    limit = MAX_ORGS if limit is None else limit
    cols, rows = run(cur, """
        select t.lead_org_id as org_id, min(t.lead_org_name) as org_name, count(*) as n_trials
        from marts.dim_trial as t
        where t.lead_org_id is not null
        group by t.lead_org_id
        having bool_or(coalesce(contains(lower(t.lead_org_name), lower($q)), false)
                       or coalesce(contains(lower(t.lead_sponsor_name), lower($q)), false))
        order by n_trials desc, org_name, org_id
        limit $limit
    """, {"q": q, "limit": limit})
    return {"orgs": _dicts(cols, rows)}
