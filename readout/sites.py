"""Trial site locations for Readout Globe, from the ClinicalTrials.gov API v2.

The same studies as ctgov.py (the same QUERY), downloaded again with only the location fields:

- fetch() saves the pages under data/raw/ctgov_sites,
- load() flattens them into raw.ct_sites, one row per site,
- build() runs dbt on the site models and their tests only, then writes results/sites.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import time
from collections.abc import Iterable, Iterator
from datetime import datetime, timezone

from . import config
from .ctgov import API, QUERY
from .db import connect
from .http import Fetcher, read_gz_json
from .load import _load_ndjson

OUT = config.RAW / "ctgov_sites"
RESULTS = config.RESULTS / "sites"
FIELDS = [
    "NCTId", "LocationFacility", "LocationCity", "LocationState", "LocationZip", "LocationCountry",
    "LocationStatus", "LocationGeoPoint",
]
# With only these fields a page of 1000 studies is a few MB, far below the Fetcher's size cap.
PAGE_SIZE = 1000

COLUMNS = {
    "nct_id": "VARCHAR", "site_order": "INTEGER", "facility": "VARCHAR", "city": "VARCHAR", "state": "VARCHAR",
    "zip": "VARCHAR", "country": "VARCHAR", "site_status": "VARCHAR", "lat": "DOUBLE", "lon": "DOUBLE",
}
# The site models and their tests; dim_trial and everything else in the warehouse are left as they are.
DBT_SELECT = "stg_ctgov__sites+"


def fetch(force: bool = False) -> dict:
    done = OUT / "_complete.json"
    if done.exists() and not force:
        info = json.loads(done.read_text())
        print(f"sites: cached, {info['studies']} studies in {info['pages']} pages ({info['fetched_at']})")
        return info
    OUT.mkdir(parents=True, exist_ok=True)
    done.unlink(missing_ok=True)  # a download that stops halfway must not look complete
    for old in [*OUT.glob("sites_*.json.gz"), OUT / "manifest.jsonl"]:
        old.unlink(missing_ok=True)
    # Each saved page is logged next to the pages, so data/raw/manifest.jsonl keeps describing the trial snapshot.
    fetcher = Fetcher(config.USER_AGENT, config.CTGOV_MIN_INTERVAL_S, manifest=OUT / "manifest.jsonl")
    params = {
        "filter.advanced": QUERY,
        "fields": ",".join(FIELDS),
        "pageSize": PAGE_SIZE,
        "countTotal": "true",
        "format": "json",
    }
    started = time.monotonic()
    page, studies, total, token = 0, 0, None, None
    while True:
        page += 1
        if token:
            params["pageToken"] = token
            params.pop("countTotal", None)
        data = fetcher.get_json(API, params=params, dest=OUT / f"sites_{page:04d}.json.gz")
        if data is None:
            raise SystemExit(f"sites: ClinicalTrials.gov returned 404 for page {page}")
        if total is None:
            total = data.get("totalCount")
        studies += len(data.get("studies", []))
        token = data.get("nextPageToken")
        print(f"sites: page {page}, {studies}/{total} studies")
        if not token:
            break
    info = {
        "query": QUERY,
        "fields": FIELDS,
        "page_size": PAGE_SIZE,
        "studies": studies,
        "total_count_reported": total,
        "pages": page,
        "requests": fetcher.requests_made,
        "seconds": round(time.monotonic() - started, 1),
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    done.write_text(json.dumps(info, indent=2))
    return info


def coordinate(value) -> float | None:
    """A latitude or longitude as a float; None when it is missing or not a finite number.

    Range checks happen in dbt (stg_ctgov__sites), so raw.ct_sites keeps what the registry sent.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def site_rows(study: dict) -> list[dict]:
    """The sites listed on one API study record, in registry order. Pure function, easy to test."""
    p = study.get("protocolSection") or {}
    nct = (p.get("identificationModule") or {}).get("nctId")
    rows = []
    for order, loc in enumerate((p.get("contactsLocationsModule") or {}).get("locations") or [], start=1):
        if not isinstance(loc, dict):
            continue
        geo = loc.get("geoPoint") or {}
        rows.append({
            "nct_id": nct,
            "site_order": order,
            "facility": loc.get("facility"),
            "city": loc.get("city"),
            "state": loc.get("state"),
            "zip": loc.get("zip"),
            "country": loc.get("country"),
            "site_status": loc.get("status"),
            "lat": coordinate(geo.get("lat")),
            "lon": coordinate(geo.get("lon")),
        })
    return rows


def page_rows(pages: Iterable[dict], seen: set[str] | None = None) -> Iterator[dict]:
    """Site rows from API pages. A study that shows up on two pages is kept once, from its first page."""
    seen = set() if seen is None else seen
    for page in pages:
        for study in page.get("studies") or []:
            nct = ((study.get("protocolSection") or {}).get("identificationModule") or {}).get("nctId")
            if not nct or nct in seen:
                continue
            seen.add(nct)
            yield from site_rows(study)


def load() -> dict[str, int]:
    pages = sorted(OUT.glob("sites_*.json.gz"))
    if not pages or not (OUT / "_complete.json").exists():
        raise SystemExit("No complete site download in data/raw/ctgov_sites. "
                         "Run `python -m readout sites-fetch` first.")
    config.CACHE.mkdir(parents=True, exist_ok=True)
    path = config.CACHE / "ct_sites.ndjson"
    seen: set[str] = set()
    try:
        with path.open("w") as fh:
            for row in page_rows((read_gz_json(p) for p in pages), seen):
                fh.write(json.dumps(row) + "\n")
        con = connect()
        try:
            con.execute("CREATE SCHEMA IF NOT EXISTS raw")
            if path.stat().st_size:
                n = _load_ndjson(con, "ct_sites", COLUMNS, path)
            else:  # no study lists a site: keep an empty table so dbt still builds
                con.execute("DROP TABLE IF EXISTS raw.ct_sites")
                con.execute("CREATE TABLE raw.ct_sites (" + ", ".join(f"{k} {v}" for k, v in COLUMNS.items()) + ")")
                n = 0
        finally:
            con.close()
    finally:
        path.unlink(missing_ok=True)
    return {"studies": len(seen), "ct_sites": n}


def build() -> dict:
    """dbt build of the site models and their tests, with the same profile and vars as `make warehouse`."""
    env = dict(os.environ, READOUT_DUCKDB=str(config.WAREHOUSE_DB), DBT_SEND_ANONYMOUS_USAGE_STATS="false")
    dbt = str(config.ROOT / ".venv" / "bin" / "dbt")
    # A target path of its own keeps the full build's artifacts and docs in warehouse/target as they are.
    target = config.DBT_DIR / "target" / "sites"
    args = ["--select", DBT_SELECT, "--project-dir", str(config.DBT_DIR), "--profiles-dir", str(config.DBT_DIR),
            "--target-path", str(target), "--vars", json.dumps({"as_of_date": config.as_of_date()})]
    run = subprocess.run([dbt, "build", *args], env=env, cwd=config.DBT_DIR, capture_output=True, text=True)
    print("dbt build " + DBT_SELECT + ":\n" + "\n".join(run.stdout.strip().splitlines()[-2:]))
    if run.returncode != 0:
        print(run.stdout[-3000:])
        raise SystemExit("dbt build of the site models failed")
    results = json.loads((target / "run_results.json").read_text())
    by_kind: dict[str, dict[str, int]] = {}
    for r in results["results"]:
        kind = r["unique_id"].split(".")[0]
        by_kind.setdefault(kind, {})
        by_kind[kind][r["status"]] = by_kind[kind].get(r["status"], 0) + 1
    warnings = [r["unique_id"].split(".")[2] for r in results["results"] if r["status"] == "warn"]
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "dbt_build.json").write_text(json.dumps(
        {"select": DBT_SELECT, "by_kind": by_kind, "warnings": warnings}, indent=2) + "\n")
    return summarize()


def summarize() -> dict:
    """Write results/sites/summary.json. Every count is read from the warehouse."""
    con = connect(read_only=True)
    try:
        counts = dict(zip(
            ["trials_total", "trials_with_sites", "trials_with_geo", "site_rows", "site_rows_with_geo", "points",
             "countries"],
            con.execute("""
                select
                    (select count(*) from marts.dim_trial),
                    (select count(distinct nct_id) from marts.fct_trial_sites),
                    (select count(distinct nct_id) from marts.fct_trial_sites where geo_key is not null),
                    (select count(*) from marts.fct_trial_sites),
                    (select count(*) from marts.fct_trial_sites where geo_key is not null),
                    (select count(*) from marts.dim_site_location),
                    (select count(distinct country) from marts.fct_trial_sites)
            """).fetchone(),
        ))
        top = con.execute("""
            select country, count(distinct nct_id) as trials, count(*) as sites
            from marts.fct_trial_sites
            where country is not null
            group by country
            order by trials desc, country
            limit 10
        """).fetchall()
    finally:
        con.close()
    summary = {
        "fetched_at": json.loads((OUT / "_complete.json").read_text())["fetched_at"],
        "as_of": config.as_of_date(),
        **counts,
        "top_countries": [{"country": c, "trials": t, "sites": s} for c, t, s in top],
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary
