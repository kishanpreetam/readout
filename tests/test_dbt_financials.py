"""Run the real dbt project on a tiny synthetic fixture and check the financial and runway logic.

SEC data could not be downloaded for the shipped results, so this test is what exercises the path from
XBRL facts to quarterly burn, cash runway and the readout-plus-runway flag. The numbers are made up.
"""

import json
import os
import shutil
import subprocess

import duckdb
import pytest

from readout import config
from readout.linking import LINK_COLUMNS
from readout.load import CT_TABLES, SEC_TABLES

DBT = config.ROOT / ".venv" / "bin" / "dbt"
M = 1_000_000


def _create(con, table, columns, rows):
    con.execute(f"create table raw.{table} (" + ", ".join(f"{k} {v}" for k, v in columns.items()) + ")")
    for row in rows:
        con.execute(f"insert into raw.{table} values ({', '.join('?' * len(columns))})",
                    [row.get(k) for k in columns])


def _study(nct, phase, status, start, pcd, pcd_type):
    return {"nct_id": nct, "brief_title": f"Study {nct}", "overall_status": status, "start_date": start,
            "start_date_type": "ACTUAL", "primary_completion_date": pcd, "primary_completion_date_type": pcd_type,
            "completion_date": pcd, "completion_date_type": pcd_type, "lead_sponsor_name": "Acme Bio, Inc.",
            "lead_sponsor_class": "INDUSTRY", "study_type": "INTERVENTIONAL", "phases": [phase],
            "enrollment_count": 300, "keywords": [], "has_results": False}


def _duration(start, end, val, form, filed):
    return {"cik": 1, "taxonomy": "us-gaap", "concept": "NetCashProvidedByUsedInOperatingActivities", "unit": "USD",
            "value": val, "period_start": start, "period_end": end, "form": form, "filed": filed, "accession": filed}


def _cash(end, val, form, filed):
    return {"cik": 1, "taxonomy": "us-gaap", "concept": "CashAndCashEquivalentsAtCarryingValue", "unit": "USD",
            "value": val, "period_end": end, "form": form, "filed": filed, "accession": "c" + end}


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    if not DBT.exists():
        pytest.skip("dbt is not installed in .venv")
    tmp = tmp_path_factory.mktemp("dbt")
    db = tmp / "fixture.duckdb"
    con = duckdb.connect(str(db))
    con.execute("create schema raw")
    _create(con, "ct_studies", CT_TABLES["ct_studies"], [
        _study("NCT00000001", "PHASE3", "RECRUITING", "2024-05-01", "2027-03-31", "ESTIMATED"),
        _study("NCT00000002", "PHASE2", "COMPLETED", "2022-01-10", "2024-06-30", "ACTUAL"),
    ])
    for table in ("ct_collaborators", "ct_conditions", "ct_interventions", "ct_primary_outcomes"):
        _create(con, table, CT_TABLES[table], [])
    _create(con, "sec_companies", SEC_TABLES["sec_companies"], [{
        "cik": 1, "name": "ACME BIO INC", "tickers": ["ACME"], "exchange": "Nasdaq", "sic": 2834,
        "former_names": [], "universe_stratum": "all_eligible", "rd_expense_cy2024": 20 * M}])
    _create(con, "sec_facts", SEC_TABLES["sec_facts"], [
        # cash flow statements are year to date: 3, 6, 9 and 12 months from the same start
        _duration("2025-01-01", "2025-03-31", -5 * M, "10-Q", "2025-05-01"),
        _duration("2025-01-01", "2025-06-30", -11 * M, "10-Q", "2025-08-01"),
        _duration("2025-01-01", "2025-09-30", -17 * M, "10-Q", "2025-11-01"),
        _duration("2025-01-01", "2025-12-31", -24 * M, "10-K", "2026-02-15"),
        _duration("2026-01-01", "2026-03-31", -6 * M, "10-Q", "2026-05-01"),
        _duration("2026-01-01", "2026-06-30", -12 * M, "10-Q", "2026-08-01"),
        _cash("2025-09-30", 30 * M, "10-Q", "2025-11-01"),
        _cash("2025-12-31", 24 * M, "10-K", "2026-02-15"),
        _cash("2026-03-31", 18 * M, "10-Q", "2026-05-01"),
        _cash("2026-06-30", 12 * M, "10-Q", "2026-08-01"),
    ])
    _create(con, "sponsor_links", LINK_COLUMNS, [{"sponsor_name": "Acme Bio, Inc.", "cik": 1, "link_method": "exact",
                                                  "link_score": 1.0}])
    _create(con, "sponsor_organizations", {
        "sponsor_name": "VARCHAR", "org_id": "INTEGER", "org_name": "VARCHAR", "n_name_variants": "INTEGER",
        "base_name": "VARCHAR", "name_clause": "VARCHAR", "stated_parents": "VARCHAR"},
        [{"sponsor_name": "Acme Bio, Inc.", "org_id": 1, "org_name": "Acme Bio, Inc.", "n_name_variants": 1,
          "base_name": "Acme Bio, Inc.", "name_clause": "none"}])
    con.close()
    project = tmp / "warehouse"
    shutil.copytree(config.DBT_DIR, project, ignore=shutil.ignore_patterns("target", "logs", ".user.yml"))
    env = dict(os.environ, READOUT_DUCKDB=str(db), DBT_SEND_ANONYMOUS_USAGE_STATS="false")
    run = subprocess.run([str(DBT), "build", "--project-dir", str(project), "--profiles-dir", str(project),
                          "--vars", json.dumps({"as_of_date": "2026-11-03"})],
                         cwd=project, env=env, capture_output=True, text=True, timeout=300)
    assert run.returncode == 0, run.stdout[-3000:]
    con = duckdb.connect(str(db), read_only=True)
    yield con
    con.close()


def test_quarters_are_derived_from_year_to_date_values(built):
    burn = dict(built.execute("select period_end::varchar, cash_burn from marts.fct_financials "
                              "where cash_burn is not null order by 1").fetchall())
    assert burn == {"2025-03-31": 5 * M, "2025-06-30": 6 * M, "2025-09-30": 6 * M, "2025-12-31": 7 * M,
                    "2026-03-31": 6 * M, "2026-06-30": 6 * M}


def test_runway_uses_trailing_twelve_months_of_burn(built):
    row = built.execute("select latest_period_end::varchar, avg_quarterly_burn, burn_quarters, runway_months, "
                        "months_since_period_end, runway_months_remaining, runway_band from marts.mart_cash_runway").fetchone()
    # four quarters end in the 12 months to 2026-06-30: burn 6, 7, 6, 6, so 6.25M a quarter; 12M / 6.25M * 3 = 5.76
    assert row == ("2026-06-30", 6.25 * M, 4, 5.8, 5, 0.8, "under_12_months")


def test_phase3_readout_with_short_runway_is_flagged(built):
    rows = built.execute("select org_name, ticker, n_phase3_readouts_12m, funding_risk "
                         "from marts.mart_readout_runway_risk").fetchall()
    assert rows == [("Acme Bio, Inc.", "ACME", 1, True)]
