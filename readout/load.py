"""Flatten the raw JSON downloads into typed tables in the DuckDB `raw` schema.

Python does the extract and load; dbt does every transformation after this point.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import config
from .db import connect
from .http import read_gz_json

CT_TABLES = {
    "ct_studies": {
        "nct_id": "VARCHAR", "brief_title": "VARCHAR", "official_title": "VARCHAR", "acronym": "VARCHAR",
        "overall_status": "VARCHAR", "why_stopped": "VARCHAR",
        "start_date": "VARCHAR", "start_date_type": "VARCHAR",
        "primary_completion_date": "VARCHAR", "primary_completion_date_type": "VARCHAR",
        "completion_date": "VARCHAR", "completion_date_type": "VARCHAR",
        "first_post_date": "VARCHAR", "last_update_date": "VARCHAR",
        "lead_sponsor_name": "VARCHAR", "lead_sponsor_class": "VARCHAR",
        "study_type": "VARCHAR", "phases": "VARCHAR[]", "enrollment_count": "BIGINT", "enrollment_type": "VARCHAR",
        "allocation": "VARCHAR", "primary_purpose": "VARCHAR", "keywords": "VARCHAR[]", "brief_summary": "VARCHAR",
        "has_results": "BOOLEAN",
    },
    "ct_collaborators": {"nct_id": "VARCHAR", "ord": "INTEGER", "name": "VARCHAR", "class": "VARCHAR"},
    "ct_conditions": {"nct_id": "VARCHAR", "ord": "INTEGER", "condition": "VARCHAR"},
    "ct_interventions": {
        "nct_id": "VARCHAR", "ord": "INTEGER", "type": "VARCHAR", "name": "VARCHAR", "other_names": "VARCHAR[]",
    },
    "ct_primary_outcomes": {"nct_id": "VARCHAR", "ord": "INTEGER", "measure": "VARCHAR", "time_frame": "VARCHAR"},
}


def _date(struct: dict | None) -> tuple[str | None, str | None]:
    if not struct:
        return None, None
    return struct.get("date"), struct.get("type")


def flatten_study(study: dict) -> dict[str, list[dict]]:
    """One API study record becomes one row in ct_studies plus child rows. Pure function, easy to test."""
    p = study.get("protocolSection", {})
    ident = p.get("identificationModule", {})
    status = p.get("statusModule", {})
    spons = p.get("sponsorCollaboratorsModule", {})
    design = p.get("designModule", {})
    nct = ident.get("nctId")
    start, start_type = _date(status.get("startDateStruct"))
    pcd, pcd_type = _date(status.get("primaryCompletionDateStruct"))
    comp, comp_type = _date(status.get("completionDateStruct"))
    enroll = design.get("enrollmentInfo") or {}
    info = design.get("designInfo") or {}
    lead = spons.get("leadSponsor") or {}
    out = {
        "ct_studies": [{
            "nct_id": nct,
            "brief_title": ident.get("briefTitle"),
            "official_title": ident.get("officialTitle"),
            "acronym": ident.get("acronym"),
            "overall_status": status.get("overallStatus"),
            "why_stopped": status.get("whyStopped"),
            "start_date": start, "start_date_type": start_type,
            "primary_completion_date": pcd, "primary_completion_date_type": pcd_type,
            "completion_date": comp, "completion_date_type": comp_type,
            "first_post_date": (status.get("studyFirstPostDateStruct") or {}).get("date"),
            "last_update_date": (status.get("lastUpdatePostDateStruct") or {}).get("date"),
            "lead_sponsor_name": lead.get("name"),
            "lead_sponsor_class": lead.get("class"),
            "study_type": design.get("studyType"),
            "phases": design.get("phases") or [],
            "enrollment_count": enroll.get("count"),
            "enrollment_type": enroll.get("type"),
            "allocation": info.get("allocation"),
            "primary_purpose": info.get("primaryPurpose"),
            "keywords": (p.get("conditionsModule") or {}).get("keywords") or [],
            "brief_summary": (p.get("descriptionModule") or {}).get("briefSummary"),
            "has_results": study.get("hasResults"),
        }],
        "ct_collaborators": [
            {"nct_id": nct, "ord": i, "name": c.get("name"), "class": c.get("class")}
            for i, c in enumerate(spons.get("collaborators") or [])
        ],
        "ct_conditions": [
            {"nct_id": nct, "ord": i, "condition": c}
            for i, c in enumerate((p.get("conditionsModule") or {}).get("conditions") or [])
        ],
        "ct_interventions": [
            {"nct_id": nct, "ord": i, "type": iv.get("type"), "name": iv.get("name"),
             "other_names": iv.get("otherNames") or []}
            for i, iv in enumerate((p.get("armsInterventionsModule") or {}).get("interventions") or [])
        ],
        "ct_primary_outcomes": [
            {"nct_id": nct, "ord": i, "measure": o.get("measure"), "time_frame": o.get("timeFrame")}
            for i, o in enumerate((p.get("outcomesModule") or {}).get("primaryOutcomes") or [])
        ],
    }
    return out


def _load_ndjson(con, table: str, columns: dict[str, str], path: Path) -> int:
    cols = ", ".join(f"'{k}': '{v}'" for k, v in columns.items())
    con.execute(f"DROP TABLE IF EXISTS raw.{table}")
    con.execute(
        f"CREATE TABLE raw.{table} AS SELECT * FROM read_json(?, format='newline_delimited', columns={{{cols}}})",
        [str(path)],
    )
    return con.execute(f"SELECT count(*) FROM raw.{table}").fetchone()[0]


def load_ctgov() -> dict[str, int]:
    pages = sorted((config.RAW / "ctgov").glob("studies_*.json.gz"))
    if not pages:
        raise SystemExit("No ClinicalTrials.gov pages in data/raw/ctgov. Run `make fetch` first.")
    config.CACHE.mkdir(parents=True, exist_ok=True)
    paths = {t: config.CACHE / f"{t}.ndjson" for t in CT_TABLES}
    handles = {t: paths[t].open("w") for t in CT_TABLES}
    seen: set[str] = set()
    try:
        for page in pages:
            for study in read_gz_json(page).get("studies", []):
                flat = flatten_study(study)
                nct = flat["ct_studies"][0]["nct_id"]
                if not nct or nct in seen:
                    continue
                seen.add(nct)
                for table, recs in flat.items():
                    for rec in recs:
                        handles[table].write(json.dumps(rec) + "\n")
    finally:
        for h in handles.values():
            h.close()
    con = connect()
    con.execute("CREATE SCHEMA IF NOT EXISTS raw")
    counts = {t: _load_ndjson(con, t, CT_TABLES[t], paths[t]) for t in CT_TABLES}
    con.close()
    for p in paths.values():
        p.unlink()
    return counts


SEC_TABLES = {
    "sec_companies": {
        "cik": "BIGINT", "name": "VARCHAR", "tickers": "VARCHAR[]", "exchange": "VARCHAR", "sic": "INTEGER",
        "sic_description": "VARCHAR", "former_names": "VARCHAR[]", "fiscal_year_end": "VARCHAR",
        "state_of_incorporation": "VARCHAR", "universe_stratum": "VARCHAR", "rd_expense_cy2024": "DOUBLE",
    },
    "sec_facts": {
        "cik": "BIGINT", "taxonomy": "VARCHAR", "concept": "VARCHAR", "unit": "VARCHAR", "value": "DOUBLE",
        "period_start": "VARCHAR", "period_end": "VARCHAR", "fiscal_year": "INTEGER", "fiscal_period": "VARCHAR",
        "form": "VARCHAR", "filed": "VARCHAR", "accession": "VARCHAR", "frame": "VARCHAR",
    },
}
# The us-gaap concepts stg_sec__facts maps to metrics; everything else in companyfacts is skipped.
SEC_CONCEPTS = {
    "CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    "CashAndDueFromBanks", "ShortTermInvestments", "MarketableSecuritiesCurrent",
    "AvailableForSaleSecuritiesDebtSecuritiesCurrent", "ResearchAndDevelopmentExpense",
    "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost", "NetIncomeLoss", "ProfitLoss",
    "NetCashProvidedByUsedInOperatingActivities", "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    "Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
}
SEC_FORMS = {"10-K", "10-Q", "10-K/A", "10-Q/A", "10-KT", "10-QT"}


def company_rows(universe: list[dict], submissions: dict[int, dict]) -> list[dict]:
    rows = []
    for c in universe:
        sub = submissions.get(int(c["cik"])) or {}
        rows.append({
            "cik": int(c["cik"]),
            "name": sub.get("name") or c["name"],
            "tickers": sub.get("tickers") or c.get("tickers") or [],
            "exchange": c.get("exchange"),
            "sic": int(sub["sic"]) if str(sub.get("sic") or "").isdigit() else c.get("sic"),
            "sic_description": sub.get("sicDescription"),
            "former_names": [f.get("name") for f in sub.get("formerNames") or [] if f.get("name")],
            "fiscal_year_end": sub.get("fiscalYearEnd"),
            "state_of_incorporation": sub.get("stateOfIncorporation"),
            "universe_stratum": c.get("stratum"),
            "rd_expense_cy2024": c.get("rd_expense_cy"),
        })
    return rows


def fact_rows(cik: int, facts: dict) -> list[dict]:
    """Flatten the needed us-gaap USD facts of one companyfacts document. Pure function, easy to test."""
    out = []
    for concept, body in (facts.get("facts", {}).get("us-gaap") or {}).items():
        if concept not in SEC_CONCEPTS:
            continue
        for unit, items in (body.get("units") or {}).items():
            if unit != "USD":
                continue
            for f in items:
                if f.get("form") not in SEC_FORMS or f.get("val") is None:
                    continue
                out.append({
                    "cik": cik, "taxonomy": "us-gaap", "concept": concept, "unit": unit, "value": float(f["val"]),
                    "period_start": f.get("start"), "period_end": f.get("end"), "fiscal_year": f.get("fy"),
                    "fiscal_period": f.get("fp"), "form": f.get("form"), "filed": f.get("filed"),
                    "accession": f.get("accn"), "frame": f.get("frame"),
                })
    return out


def load_sec() -> dict[str, int]:
    """Load the SEC universe and facts. Creates empty tables when SEC data is missing, so dbt still builds."""
    sec_dir = config.RAW / "sec"
    universe_file = sec_dir / "universe.json"
    config.CACHE.mkdir(parents=True, exist_ok=True)
    paths = {t: config.CACHE / f"{t}.ndjson" for t in SEC_TABLES}
    for p in paths.values():
        p.write_text("")
    if universe_file.exists():
        universe = json.loads(universe_file.read_text())["companies"]
        subs = {}
        for c in universe:
            f = sec_dir / "submissions" / f"CIK{int(c['cik']):010d}.json.gz"
            if f.exists():
                subs[int(c["cik"])] = read_gz_json(f)
        with paths["sec_companies"].open("w") as fh:
            for row in company_rows(universe, subs):
                fh.write(json.dumps(row) + "\n")
        with paths["sec_facts"].open("w") as fh:
            for c in universe:
                f = sec_dir / "companyfacts" / f"CIK{int(c['cik']):010d}.json.gz"
                if f.exists():
                    for row in fact_rows(int(c["cik"]), read_gz_json(f)):
                        fh.write(json.dumps(row) + "\n")
    con = connect()
    con.execute("CREATE SCHEMA IF NOT EXISTS raw")
    counts = {}
    for table, cols in SEC_TABLES.items():
        if paths[table].stat().st_size == 0:
            ddl = ", ".join(f"{k} {v}" for k, v in cols.items())
            con.execute(f"DROP TABLE IF EXISTS raw.{table}")
            con.execute(f"CREATE TABLE raw.{table} ({ddl})")
            counts[table] = 0
        else:
            counts[table] = _load_ndjson(con, table, cols, paths[table])
    con.close()
    for p in paths.values():
        p.unlink()
    return counts
