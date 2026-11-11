"""Readout Globe API (globe/app.py) on a small fixture warehouse. No model is called: complete_json is stubbed."""

import datetime as dt
import json
import threading

import duckdb
import pytest
from fastapi.testclient import TestClient

import globe.app as globe_app
from globe import ask as asker
from globe import queries
from globe.app import ALLOWED_HOSTS, SECURITY_HEADERS, create_app
from readout import config, llm, qa, sqlguard

AS_OF = "2026-11-03"
BOSTON, LONDON, TOKYO, SYDNEY = "42.3601,-71.0589", "51.5072,-0.1276", "35.6762,139.6503", "-33.8688,151.2093"
PLACES = {
    BOSTON: ("Boston", "Massachusetts", "United States"),
    LONDON: ("London", None, "United Kingdom"),
    TOKYO: ("Tokyo", None, "Japan"),
    SYDNEY: ("Sydney", "New South Wales", "Australia"),
}
# nct, title, phase, status, is_active, org_id, org_name, sponsor, conditions, interventions, start, primary completion
TRIALS = [
    ("NCT00000001", "Alpha study in breast cancer", "PHASE3", "RECRUITING", True, 1, "Acme Pharma", "Acme Pharma Inc",
     "Breast Cancer", "Drug A", "2024-01-15", "2027-03-01"),
    ("NCT00000002", "Beta trial of insulin", "PHASE2", "COMPLETED", False, 1, "Acme Pharma", "Acme Labs",
     "Diabetes", "Insulin X", "2020-05-01", "2022-06-30"),
    ("NCT00000003", "Gamma obesity study", "PHASE3", "ACTIVE_NOT_RECRUITING", True, 2, "Bolt Bio", "Bolt Biotherapeutics",
     "Obesity; Diabetes", "Drug G", "2025-02-01", "2028-01-31"),
    ("NCT00000004", "Delta lymphoma trial", "PHASE1", "NOT_YET_RECRUITING", True, 2, "Bolt Bio", "Genentech, Inc.",
     "Lymphoma", "Cell therapy D", "2026-12-01", "2027-11-02"),
    ("NCT00000005", "Epsilon heart study", "PHASE2_PHASE3", "TERMINATED", False, 3, "Cardio Corp", "Cardio Corp",
     "Heart Failure", "Device E", "2019-03-01", "2021-01-31"),
    ("NCT00000006", "Zeta study without sites", "PHASE4", "RECRUITING", True, 3, "Cardio Corp", "Cardio Corp",
     "Hypertension", "Drug Z", "2026-01-01", "2027-11-03"),
    ("NCT00000007", "Eta study with unknown dates", "NA", "UNKNOWN", False, None, None, "Small Startup LLC",
     "Asthma", "Inhaler H", None, None),
    ("NCT00000008", "Theta study due today", "PHASE3", "RECRUITING", True, 1, "Acme Pharma", "Acme Pharma Inc",
     "Breast Cancer", "Drug A2", "2023-06-01", "2026-11-03"),
]
# nct, site_order, facility, geo_key (None: no coordinates), country when there are no coordinates
SITES = [
    ("NCT00000001", 1, "Mass General", BOSTON, None), ("NCT00000001", 2, "Dana-Farber", BOSTON, None),
    ("NCT00000001", 3, "Guy's Hospital", LONDON, None), ("NCT00000001", 4, "Clinic Without Coordinates", None, "Canada"),
    ("NCT00000002", 1, "Mass General", BOSTON, None), ("NCT00000002", 2, "Tokyo Hospital", TOKYO, None),
    ("NCT00000003", 1, "Brigham", BOSTON, None), ("NCT00000003", 2, "UCLH", LONDON, None),
    ("NCT00000004", 1, "Dana-Farber", BOSTON, None),
    ("NCT00000005", 1, "Guy's Hospital", LONDON, None), ("NCT00000005", 2, "Tokyo Hospital", TOKYO, None),
    ("NCT00000007", 1, "Sydney Clinic", SYDNEY, None), ("NCT00000007", 2, None, SYDNEY, None),
    ("NCT00000008", 1, "Mass General", BOSTON, None), ("NCT00000008", 2, None, None, None),
]
DIM_TRIAL = """create table marts.dim_trial (
    nct_id varchar, brief_title varchar, official_title varchar, acronym varchar, phase varchar, phase_label varchar,
    phase_number decimal(2,1), overall_status varchar, is_active boolean, why_stopped varchar, study_type varchar,
    enrollment_count bigint, enrollment_type varchar, allocation varchar, primary_purpose varchar, start_date date,
    start_date_type varchar, primary_completion_date date, primary_completion_date_type varchar,
    primary_completion_date_precision varchar, completion_date date, completion_date_type varchar,
    first_posted_date date, last_update_date date, has_results boolean, lead_sponsor_name varchar,
    lead_sponsor_class varchar, lead_org_id integer, lead_org_name varchar, lead_cik bigint, conditions varchar,
    n_conditions bigint, interventions varchar, n_interventions bigint, has_drug_or_biologic boolean,
    brief_summary varchar)"""
SITE_TABLE = """create table marts.fct_trial_sites (nct_id varchar, site_order integer, facility varchar, city varchar,
    state varchar, zip varchar, country varchar, site_status varchar, lat double, lon double, geo_key varchar)"""


def build_warehouse(path, with_sites: bool = True) -> None:
    with duckdb.connect(str(path)) as con:
        con.execute("create schema marts")
        con.execute(DIM_TRIAL)
        con.executemany(
            "insert into marts.dim_trial (nct_id, brief_title, official_title, phase, phase_label, overall_status, "
            "is_active, lead_org_id, lead_org_name, lead_sponsor_name, conditions, interventions, enrollment_count, "
            "start_date, primary_completion_date, brief_summary) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(n, t, "Official " + t, ph, queries.PHASES[ph], st, act, oid, oname, sp, cond, interv, 100, s, p,
              "Summary of " + t)
             for n, t, ph, st, act, oid, oname, sp, cond, interv, s, p in TRIALS])
        con.execute("""create table marts.dim_organization (org_id integer, org_name varchar, n_name_variants bigint,
            n_names_stating_a_parent bigint, n_trials bigint, n_trials_as_lead bigint, n_trials_as_collaborator bigint,
            cik bigint, ticker varchar, sec_company_name varchar)""")
        con.execute("insert into marts.dim_organization (org_id, org_name, n_trials_as_lead) values "
                    "(1, 'Acme Pharma', 3), (2, 'Bolt Bio', 2), (3, 'Cardio Corp', 2)")
        if not with_sites:
            return
        con.execute(SITE_TABLE)
        rows = []
        for nct, order, facility, key, country in SITES:
            if key is None:
                rows.append((nct, order, facility, "Nowhere", None, None, country, None, None, None, None))
            else:
                lat, lon = (float(x) for x in key.split(","))
                city, state, country = PLACES[key]
                rows.append((nct, order, facility, city, state, None, country, "RECRUITING", lat, lon,
                             f"{lat:.4f},{lon:.4f}"))
        con.executemany("insert into marts.fct_trial_sites values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
        con.execute("""create table marts.dim_site_location as
            select geo_key, cast(split_part(geo_key, ',', 1) as double) as lat,
                   cast(split_part(geo_key, ',', 2) as double) as lon,
                   any_value(city) as city, any_value(state) as state, any_value(country) as country,
                   count(*) as n_sites, count(distinct nct_id) as n_trials
            from marts.fct_trial_sites where geo_key is not null group by geo_key""")


@pytest.fixture()
def protect_llm(monkeypatch, tmp_path):
    """create_app points readout.llm at globe files; restore the module afterwards and keep tests off real files."""
    for name in ("USAGE_FILE", "CACHE_DIR", "MAX_CALLS"):
        monkeypatch.setattr(llm, name, getattr(llm, name))
    monkeypatch.setenv("READOUT_AS_OF", AS_OF)
    monkeypatch.delenv(asker.BUDGET_ENV, raising=False)

    def use_tmp_files():
        monkeypatch.setattr(llm, "USAGE_FILE", tmp_path / "llm_usage.json")
        monkeypatch.setattr(llm, "CACHE_DIR", tmp_path / "llm_cache")
    return use_tmp_files


@pytest.fixture()
def warehouse(tmp_path):
    path = tmp_path / "w.duckdb"
    build_warehouse(path)
    return path


@pytest.fixture()
def static_dir(tmp_path):
    d = tmp_path / "static"
    d.mkdir()
    (d / "index.html").write_text("<!doctype html><title>Globe</title><h1>Readout Globe</h1>")
    (d / "app.js").write_text("console.log('globe');")
    return d


@pytest.fixture()
def app(warehouse, static_dir, protect_llm, monkeypatch, tmp_path):
    marker = tmp_path / "_complete.json"
    marker.write_text(json.dumps({"fetched_at": "2026-11-11T20:17:09+00:00"}))
    monkeypatch.setattr(globe_app, "SITES_COMPLETE", marker)
    application = create_app(db_path=warehouse, static_dir=static_dir, allowed_hosts=["testserver"])
    protect_llm()
    return application


@pytest.fixture()
def client(app):
    with TestClient(app) as c:
        yield c


def stub_model(monkeypatch, *answers, seen=None):
    """Make llm.complete_json return these answers in turn (an exception instance is raised instead)."""
    queue = list(answers)

    def fake(system, message, schema, task):
        if seen is not None:
            seen.append({"system": system, "message": message, "schema": schema, "task": task})
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        return {"sql": item, "_meta": {"model": "test-model"}}

    monkeypatch.setattr(llm, "complete_json", fake)


def assert_security_headers(response):
    for name, value in SECURITY_HEADERS.items():
        assert response.headers.get(name) == value, name


def keys(body):
    return [f["properties"]["geo_key"] for f in body["features"]]


# ---------- /api/meta ----------

def test_meta(client):
    r = client.get("/api/meta")
    assert r.status_code == 200
    body = r.json()
    assert body["as_of"] == AS_OF and body["sites_fetched_at"] == "2026-11-11T20:17:09Z"
    assert (body["trials"], body["trials_with_geo"], body["sites"], body["points"]) == (8, 7, 15, 4)
    assert body["months"] == {"min": "2018-01", "max": "2030-12"}
    phases = {p["value"]: p for p in body["phases"]}
    assert list(phases) == list(queries.PHASES)
    assert phases["PHASE3"] == {"value": "PHASE3", "label": "Phase 3", "count": 3}
    assert phases["NA"]["label"] == "Not applicable" and phases["EARLY_PHASE1"]["count"] == 0
    statuses = {s["value"]: s for s in body["statuses"]}
    assert set(statuses) == set(queries.STATUSES)
    assert statuses["RECRUITING"] == {"value": "RECRUITING", "label": "Recruiting", "active": True, "count": 3}
    assert statuses["ACTIVE_NOT_RECRUITING"]["label"] == "Active, not recruiting"
    assert statuses["SUSPENDED"]["active"] is False and statuses["SUSPENDED"]["count"] == 0
    assert [s["value"] for s in body["statuses"] if s["active"]] == [
        "RECRUITING", "NOT_YET_RECRUITING", "ENROLLING_BY_INVITATION", "ACTIVE_NOT_RECRUITING"]


def test_meta_without_site_download_marker(warehouse, static_dir, protect_llm, monkeypatch, tmp_path):
    monkeypatch.setattr(globe_app, "SITES_COMPLETE", tmp_path / "missing.json")
    with TestClient(create_app(warehouse, static_dir, ["testserver"])) as c:
        assert c.get("/api/meta").json()["sites_fetched_at"] is None


# ---------- /api/points ----------

def test_points_geojson_shape_and_counts(client):
    r = client.get("/api/points")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/json")
    body = r.json()
    assert body["type"] == "FeatureCollection"
    assert body["summary"] == {"trials": 7, "sites": 13, "points": 4}
    assert keys(body) == [BOSTON, LONDON, TOKYO, SYDNEY]  # most trials first
    for f in body["features"]:
        assert set(f) == {"type", "geometry", "properties"} and f["type"] == "Feature"
        assert f["geometry"]["type"] == "Point"
        lon, lat = f["geometry"]["coordinates"]
        assert f["properties"]["geo_key"] == f"{lat:.4f},{lon:.4f}"
        assert set(f["properties"]) == {"geo_key", "city", "state", "country", "trials", "due", "top_org"}
    boston = body["features"][0]["properties"]
    assert boston == {"geo_key": BOSTON, "city": "Boston", "state": "Massachusetts", "country": "United States",
                      "trials": 5, "due": 3, "top_org": "Acme Pharma"}
    props = {f["properties"]["geo_key"]: f["properties"] for f in body["features"]}
    assert (props[LONDON]["trials"], props[LONDON]["due"]) == (3, 1)
    assert props[LONDON]["top_org"] == "Acme Pharma"  # a three-way tie goes to the first name
    assert props[SYDNEY]["top_org"] == "Small Startup LLC"  # no resolved organization: the sponsor name


@pytest.mark.parametrize("query, expected, trials", [
    ("phase=PHASE3", {BOSTON: 3, LONDON: 2}, 3),
    ("phase=PHASE3,PHASE2", {BOSTON: 4, LONDON: 2, TOKYO: 1}, 4),
    ("phase=PHASE3, PHASE3", {BOSTON: 3, LONDON: 2}, 3),
    ("status=RECRUITING", {BOSTON: 2, LONDON: 1}, 2),
    ("status=COMPLETED,TERMINATED", {BOSTON: 1, LONDON: 1, TOKYO: 2}, 2),
    ("org_id=2", {BOSTON: 2, LONDON: 1}, 2),
    ("org_id=99", {}, 0),
    ("q=insulin", {BOSTON: 1, TOKYO: 1}, 1),
    ("q=DIABETES", {BOSTON: 2, LONDON: 1, TOKYO: 1}, 2),
    ("q=heart failure", {LONDON: 1, TOKYO: 1}, 1),
    ("q=100%25", {}, 0),  # % is a literal character, not a wildcard
    ("active_on=2027-02", {BOSTON: 3, LONDON: 2}, 3),
    ("due_within=12", {BOSTON: 3, LONDON: 1}, 3),
    ("due_within=1", {BOSTON: 1}, 1),
    ("nct=NCT00000005,NCT00000007", {LONDON: 1, TOKYO: 1, SYDNEY: 1}, 2),
    ("nct=NCT00000006", {}, 0),
    ("phase=PHASE3&status=RECRUITING&q=breast", {BOSTON: 2, LONDON: 1}, 2),
    ("phase=&q=", {BOSTON: 5, LONDON: 3, TOKYO: 2, SYDNEY: 1}, 7),  # empty values mean "not set"
])
def test_points_filters(client, query, expected, trials):
    body = client.get("/api/points?" + query).json()
    assert {f["properties"]["geo_key"]: f["properties"]["trials"] for f in body["features"]} == expected
    assert body["summary"]["trials"] == trials and body["summary"]["points"] == len(expected)


def test_points_due_window_is_half_open(client):
    # NCT00000008 completes on the as-of date (due); NCT00000006 exactly 12 months later (not due, and no sites)
    body = client.get("/api/points?nct=NCT00000008,NCT00000004,NCT00000003").json()
    boston = body["features"][0]["properties"]
    assert (boston["trials"], boston["due"]) == (3, 2)


def test_points_ties_sort_by_geo_key(client):
    body = client.get("/api/points?nct=NCT00000005,NCT00000007").json()
    assert keys(body) == [SYDNEY, TOKYO, LONDON]


def test_points_cap_and_summary_cover_the_response(client, monkeypatch):
    monkeypatch.setattr(queries, "MAX_POINTS", 2)
    body = client.get("/api/points").json()
    assert keys(body) == [BOSTON, LONDON]
    assert body["summary"] == {"trials": 6, "sites": 9, "points": 2}


# ---------- /api/points/{geo_key}/trials ----------

def test_point_trials(client):
    r = client.get(f"/api/points/{BOSTON}/trials")
    assert r.status_code == 200
    body = r.json()
    assert (body["geo_key"], body["city"], body["state"], body["country"]) == (
        BOSTON, "Boston", "Massachusetts", "United States")
    assert body["total"] == 5
    assert [t["nct_id"] for t in body["trials"]] == [
        "NCT00000002", "NCT00000008", "NCT00000001", "NCT00000004", "NCT00000003"]  # soonest completion first
    first = next(t for t in body["trials"] if t["nct_id"] == "NCT00000001")
    assert first == {
        "nct_id": "NCT00000001", "title": "Alpha study in breast cancer", "phase": "PHASE3", "phase_label": "Phase 3",
        "status": "RECRUITING", "lead_org_name": "Acme Pharma", "conditions": "Breast Cancer",
        "start_date": "2024-01-15", "primary_completion_date": "2027-03-01", "due": True,
        "facilities": ["Dana-Farber", "Mass General"], "url": "https://clinicaltrials.gov/study/NCT00000001"}
    assert [t["due"] for t in body["trials"]] == [False, True, True, True, False]


def test_point_trials_nulls_last_and_filters(client, monkeypatch):
    sydney = client.get(f"/api/points/{SYDNEY}/trials").json()
    assert sydney["total"] == 1
    only = sydney["trials"][0]
    assert only["facilities"] == ["Sydney Clinic"] and only["primary_completion_date"] is None
    assert only["lead_org_name"] == "Small Startup LLC" and only["due"] is False
    filtered = client.get(f"/api/points/{BOSTON}/trials?phase=PHASE3&due_within=12").json()
    assert [t["nct_id"] for t in filtered["trials"]] == ["NCT00000008", "NCT00000001"]
    empty = client.get(f"/api/points/{LONDON}/trials?phase=PHASE1")
    assert empty.status_code == 200 and empty.json()["total"] == 0 and empty.json()["trials"] == []
    monkeypatch.setattr(queries, "MAX_POINT_TRIALS", 2)
    capped = client.get(f"/api/points/{BOSTON}/trials").json()
    assert capped["total"] == 5 and len(capped["trials"]) == 2


def test_point_trials_unknown_and_malformed_keys(client):
    r = client.get("/api/points/1.0000,2.0000/trials")
    assert r.status_code == 404 and r.json()["kind"] == "not_found"
    for bad in ("boston", "42.36,-71.05", "42.3601;-71.0589", "1e3,2"):
        r = client.get(f"/api/points/{bad}/trials")
        assert r.status_code == 400 and r.json()["kind"] == "bad_filter", bad
    r = client.get(f"/api/points/{BOSTON}/trials?phase=PHASE9")
    assert r.status_code == 400 and r.json()["kind"] == "bad_filter"


# ---------- /api/trial/{nct_id} ----------

def test_trial(client):
    r = client.get("/api/trial/NCT00000001")
    assert r.status_code == 200
    body = r.json()
    assert body == {
        "nct_id": "NCT00000001", "title": "Alpha study in breast cancer",
        "official_title": "Official Alpha study in breast cancer", "phase": "PHASE3", "phase_label": "Phase 3",
        "status": "RECRUITING", "is_active": True, "lead_org_id": 1, "lead_org_name": "Acme Pharma",
        "conditions": "Breast Cancer", "interventions": "Drug A", "enrollment_count": 100,
        "start_date": "2024-01-15", "primary_completion_date": "2027-03-01", "due": True, "n_sites": 4,
        "n_countries": 3, "countries": ["United States", "Canada", "United Kingdom"],
        "brief_summary": "Summary of Alpha study in breast cancer",
        "url": "https://clinicaltrials.gov/study/NCT00000001"}


def test_trial_without_sites_or_dates(client):
    zeta = client.get("/api/trial/NCT00000006").json()
    assert (zeta["n_sites"], zeta["n_countries"], zeta["countries"], zeta["due"]) == (0, 0, [], False)
    eta = client.get("/api/trial/NCT00000007").json()
    assert eta["lead_org_id"] is None and eta["lead_org_name"] == "Small Startup LLC"
    assert eta["start_date"] is None and eta["primary_completion_date"] is None and eta["due"] is False


def test_trial_bad_and_unknown_ids(client):
    assert client.get("/api/trial/NCT99999999").status_code == 404
    for bad in ("NCT123", "nct00000001", "NCT000000011", "NCT0000000a", "1"):
        r = client.get(f"/api/trial/{bad}")
        assert r.status_code == 400 and r.json()["kind"] == "bad_filter", bad


# ---------- /api/orgs ----------

def test_orgs(client):
    assert client.get("/api/orgs?q=acme").json() == {"orgs": [{"org_id": 1, "org_name": "Acme Pharma", "n_trials": 3}]}
    # a sponsor name rolled into an organization finds it too
    assert client.get("/api/orgs?q=GENENTECH").json()["orgs"] == [{"org_id": 2, "org_name": "Bolt Bio", "n_trials": 2}]
    assert [o["org_name"] for o in client.get("/api/orgs?q=ar").json()["orgs"]] == ["Acme Pharma", "Cardio Corp"]
    assert client.get("/api/orgs?q=zzz").json() == {"orgs": []}


def test_orgs_limit(client, monkeypatch):
    monkeypatch.setattr(queries, "MAX_ORGS", 1)
    assert len(client.get("/api/orgs?q=ar").json()["orgs"]) == 1


@pytest.mark.parametrize("query", ["", "q=", "q=a", "q=" + "x" * 61, "q=ab%00", "q=pfizer&q=acme", "q=acme&x=1"])
def test_orgs_bad_queries(client, query):
    r = client.get("/api/orgs?" + query)
    assert r.status_code == 400 and r.json()["kind"] == "bad_filter"


# ---------- validation ----------

@pytest.mark.parametrize("query", [
    "phase=PHASE5", "phase=phase3", "phase=PHASE3,,PHASE2", "phase=PHASE3&phase=PHASE2",
    "phase=" + ",".join(["PHASE3"] * 21),
    "status=OPEN", "status=RECRUITING,FOO", "status=Recruiting",
    "org_id=abc", "org_id=-1", "org_id=1.5", "org_id=99999999999", "org_id=2147483648", "org_id=%D9%A1%D9%A2",
    "q=a", "q=" + "x" * 81, "q=ab%00cd", "q=ab%E2%80%AEcd", "q=ab%0Acd",
    "active_on=2027-13", "active_on=2027-1", "active_on=27-01", "active_on=2027-01-01", "active_on=1800-01",
    "active_on=2027-00",
    "due_within=0", "due_within=37", "due_within=abc", "due_within=12.0", "due_within=-3",
    "nct=NCT123", "nct=nct00000001", "nct=NCT00000001;DROP", "nct=NCT00000001,,NCT00000002",
    "nct=" + ",".join(f"NCT{i:08d}" for i in range(501)),
    "foo=bar", "phases=PHASE3",
])
def test_bad_filters_are_400(client, query):
    for path in ("/api/points", f"/api/points/{BOSTON}/trials"):
        r = client.get(f"{path}?{query}")
        assert r.status_code == 400, (path, query)
        body = r.json()
        assert body["kind"] == "bad_filter" and set(body) == {"error", "kind"}


def test_nct_filter_accepts_500(client):
    ids = ["NCT00000001"] + [f"NCT{i:08d}" for i in range(10, 509)]
    r = client.get("/api/points?nct=" + ",".join(ids))
    assert r.status_code == 200 and r.json()["summary"]["trials"] == 1


def test_filters_are_parameters_not_sql_text():
    f = queries.parse_filters([("q", "x' or 1=1 --"), ("phase", "PHASE3"), ("nct", "NCT00000001")])
    where, params = queries.where_clause(f, dt.date(2026, 11, 3))
    assert "or 1=1" not in where and "PHASE3" not in where and "NCT00000001" not in where
    assert params["q"] == "x' or 1=1 --" and params["phases"] == ["PHASE3"] and params["ncts"] == ["NCT00000001"]


def test_add_months_clamps_to_month_end():
    assert queries.add_months(dt.date(2026, 1, 31), 1) == dt.date(2026, 2, 28)
    assert queries.add_months(dt.date(2026, 11, 3), 12) == dt.date(2027, 11, 3)
    assert queries.add_months(dt.date(2026, 11, 30), 3) == dt.date(2027, 2, 28)
    assert queries.month_bounds("2028-02") == (dt.date(2028, 2, 1), dt.date(2028, 2, 29))


# ---------- security ----------

@pytest.mark.parametrize("base_url", ["http://127.0.0.1:8091", "http://localhost:8091"])
def test_default_hosts_are_allowed(warehouse, static_dir, protect_llm, base_url):
    app = create_app(warehouse, static_dir)
    protect_llm()
    with TestClient(app, base_url=base_url) as c:
        r = c.get("/api/meta")
        assert r.status_code == 200
        assert_security_headers(r)


@pytest.mark.parametrize("host", ["evil.example:8091", "127.0.0.1:9999", "127.0.0.1", "localhost", "testserver",
                                  "127.0.0.1:8091.evil.example", "[::1]:8091", ""])
def test_other_host_headers_are_rejected(warehouse, static_dir, protect_llm, host):
    app = create_app(warehouse, static_dir)
    protect_llm()
    with TestClient(app, base_url="http://127.0.0.1:8091") as c:
        for path in ("/api/meta", "/", "/app.js"):
            r = c.get(path, headers={"host": host})
            assert r.status_code == 400, (host, path)
            assert r.json() == {"error": "Host header not allowed", "kind": "bad_host"}
            assert_security_headers(r)


def test_security_headers_on_every_kind_of_response(client, monkeypatch):
    responses = {
        "api": client.get("/api/meta"),
        "points": client.get("/api/points"),
        "index": client.get("/"),
        "static file": client.get("/app.js"),
        "static 404": client.get("/missing.css"),
        "api 404": client.get("/api/nope"),
        "bad filter": client.get("/api/points?phase=BAD"),
        "405": client.post("/api/meta"),
        "ask 400": client.post("/api/ask", json={"question": ""}),
    }
    assert responses["index"].status_code == 200 and "Readout Globe" in responses["index"].text
    assert responses["index"].headers["content-type"].startswith("text/html")
    assert responses["static file"].headers["content-type"].startswith("text/javascript")
    assert responses["static 404"].status_code == 404 and responses["static 404"].json()["kind"] == "not_found"
    assert responses["api 404"].status_code == 404 and responses["api 404"].json()["kind"] == "not_found"
    assert responses["405"].status_code == 405
    for name, r in responses.items():
        assert_security_headers(r)

    def broken(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(queries, "points", broken)
    r = client.get("/api/points")
    assert r.status_code == 500 and r.json() == {"error": "internal server error", "kind": "server_error"}
    assert_security_headers(r)


def test_websockets_are_refused(client):
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/meta"):
            pass


def test_no_docs_or_openapi(client):
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


def test_cross_origin_post_is_refused(client, monkeypatch):
    stub_model(monkeypatch, "select 1 as x")
    r = client.post("/api/ask", json={"question": "anything"}, headers={"origin": "http://evil.example"})
    assert r.status_code == 403 and r.json()["kind"] == "bad_origin"
    assert_security_headers(r)
    ok = client.post("/api/ask", json={"question": "anything"}, headers={"origin": "http://testserver"})
    assert ok.status_code == 200


def test_connection_is_read_only_and_sandboxed(app):
    with app.state.globe.cursor() as cur:
        with pytest.raises(duckdb.Error):
            cur.execute("create table marts.x (a integer)")
        with pytest.raises(duckdb.Error):
            cur.execute("select * from read_text('/etc/hosts')")


def test_missing_site_tables_fail_at_startup(tmp_path, protect_llm):
    path = tmp_path / "no_sites.duckdb"
    build_warehouse(path, with_sites=False)
    with pytest.raises(RuntimeError, match="marts.fct_trial_sites"):
        create_app(path, tmp_path)


def test_no_static_dir_means_json_404_at_root(warehouse, protect_llm, tmp_path):
    app = create_app(warehouse, tmp_path / "absent", ["testserver"])
    protect_llm()
    with TestClient(app) as c:
        r = c.get("/")
        assert r.status_code == 404 and r.json()["kind"] == "not_found"
        assert_security_headers(r)


def test_create_app_points_llm_at_globe_files(warehouse, static_dir, protect_llm, monkeypatch):
    create_app(warehouse, static_dir, ["testserver"])
    assert llm.USAGE_FILE == config.DATA / "globe" / "llm_usage.json" != config.RESULTS / "llm_usage.json"
    assert llm.CACHE_DIR == config.CACHE / "globe_llm"
    assert llm.MAX_CALLS == 200
    monkeypatch.setenv(asker.BUDGET_ENV, "7")
    asker.configure_llm()
    assert llm.MAX_CALLS == 7
    monkeypatch.setenv(asker.BUDGET_ENV, "lots")
    with pytest.raises(ValueError):
        asker.configure_llm()


def test_default_allowed_hosts():
    assert ALLOWED_HOSTS == ("127.0.0.1:8091", "localhost:8091")


# ---------- /api/ask ----------

def test_ask_good_query(client, monkeypatch):
    seen = []
    stub_model(monkeypatch, "select nct_id, brief_title from marts.dim_trial where phase = 'PHASE3' order by nct_id",
               seen=seen)
    r = client.post("/api/ask", json={"question": "  Which Phase 3 trials are there?  "})
    assert r.status_code == 200
    body = r.json()
    assert body["question"] == "Which Phase 3 trials are there?"
    assert body["sql"].startswith("select nct_id")
    assert body["columns"] == ["nct_id", "brief_title"]
    assert body["rows"][0] == ["NCT00000001", "Alpha study in breast cancer"]
    assert body["row_count"] == 3 and body["nct_ids"] == ["NCT00000001", "NCT00000003", "NCT00000008"]
    assert body["geo_keys"] == []  # always present
    assert body["model"] == "test-model" and isinstance(body["cached"], bool) and body["seconds"] >= 0
    assert len(seen) == 1
    call = seen[0]
    assert call["task"] == "globe_ask" and call["schema"] == qa.SCHEMA
    assert call["system"].startswith(qa.SYSTEM)
    message = call["message"]
    assert qa.DOCS in message and asker.SITE_NOTES in message
    for text in (call["system"], message):
        flat = " ".join(text.split())  # the notes wrap lines; compare the words
        for rule in ("Answer at exactly the level the question asks about", "one row per country",
                     "one row per state", "one row per place, grouped by geo_key", "with its city and country",
                     "Add a geo_key column only for city, hospital or site level answers",
                     "return one row per trial with a plain nct_id column",
                     "Never pack nct_id or geo_key into lists, arrays or strings"):
            assert rule in flat, rule
        assert "never add it to country, state or organization level answers" in flat.lower()
        assert "include a geo_key column" not in flat  # the earlier rule asked for it on every place answer
    assert "grouped by geo_key (from marts.fct_trial_sites or marts.dim_site_location)" in " ".join(message.split())
    assert "still returns that number" in message
    assert "marts.fct_trial_sites(nct_id VARCHAR, site_order INTEGER" in message
    assert "marts.dim_site_location(geo_key VARCHAR, lat DOUBLE" in message
    assert "marts.dim_trial(nct_id VARCHAR" in message
    assert message.endswith("Question: Which Phase 3 trials are there?\nReturn JSON with one key, sql.")


def test_ask_rows_are_capped_and_converted(client, monkeypatch):
    stub_model(monkeypatch, "select i as n from range(1000) as t(i)")
    body = client.post("/api/ask", json={"question": "numbers"}).json()
    assert body["row_count"] == sqlguard.MAX_ROWS and len(body["rows"]) == asker.DISPLAY_ROWS
    assert body["nct_ids"] == []
    stub_model(monkeypatch, "select date '2026-11-03' as d, 1.5::decimal(4,2) as x, 'nan'::double as v, [1, 2] as l, "
                            "'NCT00000002' as NCT_ID")
    body = client.post("/api/ask", json={"question": "types"}).json()
    assert body["rows"] == [["2026-11-03", 1.5, None, [1, 2], "NCT00000002"]]
    assert body["nct_ids"] == ["NCT00000002"]


def test_ask_geo_keys_from_scalar_cells(client, monkeypatch):
    stub_model(monkeypatch, "select geo_key, count(distinct nct_id) as n from marts.fct_trial_sites "
                            "where geo_key is not null group by geo_key order by n desc, geo_key")
    body = client.post("/api/ask", json={"question": "Which cities have the most trials?"}).json()
    assert body["geo_keys"] == [BOSTON, LONDON, TOKYO, SYDNEY]
    assert body["nct_ids"] == []


def test_ask_highlights_from_list_cells_and_spaced_column_names(client, monkeypatch):
    # the shape seen live: one row per place with the trials packed into a list column named "NCT ID"
    stub_model(monkeypatch, 'select country, list(distinct geo_key order by geo_key) as geo_key, '
                            'list(distinct nct_id order by nct_id) as "NCT ID" from marts.fct_trial_sites '
                            'where geo_key is not null group by country order by country')
    body = client.post("/api/ask", json={"question": "Which countries have trials?"}).json()
    assert body["columns"] == ["country", "geo_key", "NCT ID"]
    assert body["geo_keys"] == [SYDNEY, TOKYO, LONDON, BOSTON]
    assert body["nct_ids"] == ["NCT00000007", "NCT00000002", "NCT00000005", "NCT00000001", "NCT00000003",
                               "NCT00000004", "NCT00000008"]
    assert body["rows"][0] == ["Australia", [SYDNEY], ["NCT00000007"]]


def test_highlight_values_drop_malformed_values():
    cols = ["NCT ID", "geo key", "Geo_Key", "other"]
    rows = [
        ("NCT00000001", "42.3601,-71.0589", None, "NCT00000009"),  # "other" is never read
        (["NCT00000002", ["NCT00000003", "bad"]], ["51.5072,-0.1276", "42.36,-71.05"], "-33.8688,151.2093", None),
        ("nct00000004", "42.3601;-71.0589", 42, None),
        (" NCT00000005", None, "1.0000,2.0000,3.0000", None),
        ("NCT00000001, NCT00000006", "abc", "123.4567,-12.3456", None),  # a packed string is not split
        (12345, "", ("-0.0000,0.0000",), None),
        ("NCT00000001", "42.3601,-71.0589", None, None),  # duplicates are kept once
    ]
    assert asker.highlight_values(cols, rows, "nct_id", queries.NCT_RE) == [
        "NCT00000001", "NCT00000002", "NCT00000003"]
    assert asker.highlight_values(cols, rows, "geo_key", asker.GEO_KEY_VALUE_RE) == [
        "42.3601,-71.0589", "51.5072,-0.1276", "-33.8688,151.2093", "123.4567,-12.3456", "-0.0000,0.0000"]
    assert asker.highlight_values(["nct_ids", "trial"], [(["NCT00000001"], "NCT00000002")], "nct_id",
                                  queries.NCT_RE) == []


def test_highlights_stop_at_500(client, monkeypatch):
    stub_model(monkeypatch, "select list(printf('NCT%08d', i)) as nct_id, "
                            "list(printf('%d.0000,%d.0000', i, i)) as geo_key from range(600) as t(i)")
    body = client.post("/api/ask", json={"question": "Many trials and places"}).json()
    assert body["row_count"] == 1
    assert len(body["nct_ids"]) == 500 and body["nct_ids"][0] == "NCT00000000" and body["nct_ids"][-1] == "NCT00000499"
    assert len(body["geo_keys"]) == 500 and body["geo_keys"][-1] == "499.0000,499.0000"
    rows = [([f"NCT{i:08d}" for i in range(300)],), ([f"NCT{i:08d}" for i in range(250, 700)],)]
    assert asker.highlight_values(["nct_id"], rows, "nct_id", queries.NCT_RE) == [f"NCT{i:08d}" for i in range(500)]


@pytest.mark.parametrize("sql", ["drop table marts.dim_trial", "select * from read_csv('/etc/passwd')",
                                 "select 1; select 2", "", "copy (select 1) to 'x.csv'"])
def test_ask_unsafe_sql_is_422_without_a_retry(client, monkeypatch, sql):
    seen = []
    stub_model(monkeypatch, sql, seen=seen)
    r = client.post("/api/ask", json={"question": "do something bad"})
    assert r.status_code == 422
    body = r.json()
    assert body["kind"] == "unsafe_sql" and body["sql"] == sql and body["error"]
    assert len(seen) == 1


def test_ask_repairs_a_failing_query_once(client, monkeypatch):
    seen = []
    stub_model(monkeypatch, "select nope from marts.dim_trial", "select count(*) as n from marts.dim_trial", seen=seen)
    r = client.post("/api/ask", json={"question": "how many trials"})
    assert r.status_code == 200 and r.json()["rows"] == [[8]]
    assert len(seen) == 2
    assert "Your previous SQL was:\nselect nope from marts.dim_trial\nIt failed with: " in seen[1]["message"]


def test_ask_query_failed_after_the_repair(client, monkeypatch):
    stub_model(monkeypatch, "select nope from marts.dim_trial", "select still_nope from marts.dim_trial")
    r = client.post("/api/ask", json={"question": "how many trials"})
    assert r.status_code == 500
    body = r.json()
    assert body["kind"] == "query_failed" and body["sql"] == "select still_nope from marts.dim_trial"


def test_ask_timeout(client, monkeypatch):
    monkeypatch.setattr(sqlguard, "TIMEOUT_S", 0.05)
    stub_model(monkeypatch, "select sum(hash(i)) from range(5000000000) as t(i)")
    r = client.post("/api/ask", json={"question": "something slow"})
    assert r.status_code == 504 and r.json()["kind"] == "timeout"


def test_ask_llm_unavailable(client, monkeypatch):
    stub_model(monkeypatch, llm.LLMUnavailable("LLM call cap of 200 reached (READOUT_LLM_MAX_CALLS)."))
    r = client.post("/api/ask", json={"question": "anything"})
    assert r.status_code == 503
    assert r.json() == {"error": "LLM call cap of 200 reached (READOUT_LLM_MAX_CALLS).", "kind": "llm_unavailable"}
    stub_model(monkeypatch, llm.LLMError("claude CLI error: " + "x" * 500))
    r = client.post("/api/ask", json={"question": "anything"})
    assert r.status_code == 503 and r.json()["kind"] == "llm_unavailable"
    assert len(r.json()["error"]) < 100


def test_ask_busy(client, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def slow(system, message, schema, task):
        entered.set()
        assert release.wait(10)
        return {"sql": "select 1 as x", "_meta": {"model": "test-model"}}

    monkeypatch.setattr(llm, "complete_json", slow)
    first = {}
    worker = threading.Thread(target=lambda: first.update(r=client.post("/api/ask", json={"question": "first"})))
    worker.start()
    try:
        assert entered.wait(10)
        busy = client.post("/api/ask", json={"question": "second"})
        assert busy.status_code == 429 and busy.json()["kind"] == "busy"
        assert_security_headers(busy)
    finally:
        release.set()
        worker.join(10)
    assert first["r"].status_code == 200
    assert client.post("/api/ask", json={"question": "third"}).status_code == 200  # the lock was released


@pytest.mark.parametrize("payload", [
    {"question": ""}, {"question": "   "}, {"question": "x" * 301}, {"question": "line one\nline two"},
    {"question": "tab\there"}, {"question": "bidi ‮ text"}, {"question": 5}, {"question": None},
    {"q": "hello"}, {"question": "hello", "extra": 1}, ["question"], "question",
])
def test_ask_bad_questions(client, monkeypatch, payload):
    stub_model(monkeypatch, "select 1")
    r = client.post("/api/ask", json=payload)
    assert r.status_code == 400 and r.json()["kind"] == "bad_question"


def test_ask_bad_bodies(client, monkeypatch):
    stub_model(monkeypatch, "select 1")
    cases = [
        client.post("/api/ask", content=b"not json", headers={"content-type": "application/json"}),
        client.post("/api/ask", content=b'{"question": "hi"}', headers={"content-type": "text/plain"}),
        client.post("/api/ask", content=b'{"question": "hi"}'),
        client.post("/api/ask", content=json.dumps({"question": "x" * 5000}).encode(),
                    headers={"content-type": "application/json"}),
    ]
    for r in cases:
        assert r.status_code == 400 and r.json()["kind"] == "bad_question"
    assert client.post("/api/ask", json={"question": "x" * 300}).status_code == 200


def test_ask_cached_flag_follows_real_model_calls(client, monkeypatch):
    calls = iter([5, 6])
    monkeypatch.setattr(llm, "calls_made", lambda: next(calls))
    stub_model(monkeypatch, "select 1 as x")
    assert client.post("/api/ask", json={"question": "fresh"}).json()["cached"] is False
    monkeypatch.setattr(llm, "calls_made", lambda: 6)
    assert client.post("/api/ask", json={"question": "again"}).json()["cached"] is True


def test_prompt_follows_a_new_snapshot_date():
    message = asker.build_prompt("t(a INTEGER)", "q?", "2027-01-15")
    assert "Today\" means 2027-01-15" in message and "before 2028-01-15" in message
    assert "2026-11-03" not in message and "2027-11-03" not in message
    assert asker.build_prompt("t(a INTEGER)", "q?", qa.AS_OF).count(qa.DOCS) == 1
