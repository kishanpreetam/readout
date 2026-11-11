"""SEC EDGAR downloads: listed tickers, an R&D frame, submissions (for SIC codes) and XBRL companyfacts.

Fair access: at most 5 requests per second (SEC allows 10) and a declared User-Agent. If SEC refuses a
request (HTTP 401 or 403), fetching stops at once, nothing is retried, and the refusal is written to
results/sec_access.json so the rest of the pipeline can run on what it has.
"""

from __future__ import annotations

import json
import random
from datetime import datetime, timezone

from . import config
from .http import AccessRefused, Fetcher, read_gz_json

TICKERS_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
FRAME_URL = "https://data.sec.gov/api/xbrl/frames/us-gaap/ResearchAndDevelopmentExpense/USD/CY{year}.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
OUT = config.RAW / "sec"
STATUS_FILE = config.RESULTS / "sec_access.json"

# Universe rules (documented in the README):
EXCHANGES = {"Nasdaq", "NYSE"}
SIC_CODES = {2834, 2836}  # pharmaceutical preparations; biological products
RD_FRAME_YEAR = 2024
UNIVERSE_MAX = 200  # take every eligible company when there are at most this many
TOP_BY_RD = 50  # otherwise: the 50 largest R&D spenders ...
RANDOM_REST = 100  # ... plus a seeded random sample of 100 of the rest
SEED = 20261103


def _status(payload: dict) -> None:
    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload["user_agent"] = config.SEC_USER_AGENT
    payload["checked_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    STATUS_FILE.write_text(json.dumps(payload, indent=2) + "\n")


def select_universe(eligible: list[dict]) -> list[dict]:
    """Deterministic universe: all eligible companies if few enough, else top R&D plus a seeded sample."""
    ranked = sorted(eligible, key=lambda c: (-c["rd_expense_cy"], c["cik"]))
    if len(ranked) <= UNIVERSE_MAX:
        return [dict(c, stratum="all_eligible") for c in ranked]
    top = [dict(c, stratum="top_rd") for c in ranked[:TOP_BY_RD]]
    rest = ranked[TOP_BY_RD:]
    sample = random.Random(SEED).sample(rest, RANDOM_REST)
    return top + [dict(c, stratum="random_sample") for c in sorted(sample, key=lambda c: c["cik"])]


def _listed(tickers: dict) -> dict[int, dict]:
    fields = tickers["fields"]
    out: dict[int, dict] = {}
    for row in tickers["data"]:
        rec = dict(zip(fields, row))
        if rec.get("exchange") not in EXCHANGES:
            continue
        cik = int(rec["cik"])
        entry = out.setdefault(cik, {"cik": cik, "name": rec["name"], "tickers": [], "exchange": rec["exchange"]})
        entry["tickers"].append(rec["ticker"])
    return out


def fetch(force: bool = False) -> dict:
    done = OUT / "_complete.json"
    if done.exists() and not force:
        info = json.loads(done.read_text())
        print(f"sec: cached, universe of {info['universe_size']} companies")
        return info
    OUT.mkdir(parents=True, exist_ok=True)
    fetcher = Fetcher(config.SEC_USER_AGENT, config.SEC_MIN_INTERVAL_S)
    stage = "tickers"
    try:
        tickers = fetcher.get_json(TICKERS_URL, dest=OUT / "company_tickers_exchange.json.gz")
        listed = _listed(tickers)
        stage = "rd_frame"
        frame = fetcher.get_json(FRAME_URL.format(year=RD_FRAME_YEAR), dest=OUT / f"frame_rd_cy{RD_FRAME_YEAR}.json.gz")
        rd = {}
        for f in frame["data"]:
            if f.get("val") and f["val"] > 0:
                rd[int(f["cik"])] = max(rd.get(int(f["cik"]), 0), float(f["val"]))
        candidates = sorted(c for c in listed if c in rd)
        print(f"sec: {len(listed)} listed companies, {len(candidates)} reported R&D for CY{RD_FRAME_YEAR}")
        stage = "submissions"
        eligible = []
        for i, cik in enumerate(candidates, 1):
            path = OUT / "submissions" / f"CIK{cik:010d}.json.gz"
            sub = read_gz_json(path) if path.exists() else fetcher.get_json(SUBMISSIONS_URL.format(cik=cik), dest=path)
            if sub and str(sub.get("sic") or "").isdigit() and int(sub["sic"]) in SIC_CODES:
                eligible.append({**listed[cik], "sic": int(sub["sic"]), "rd_expense_cy": rd[cik]})
            if i % 200 == 0:
                print(f"sec: submissions {i}/{len(candidates)}, {len(eligible)} eligible so far")
        universe = select_universe(eligible)
        (OUT / "universe.json").write_text(json.dumps(
            {"rules": {"exchanges": sorted(EXCHANGES), "sic_codes": sorted(SIC_CODES), "rd_frame": f"CY{RD_FRAME_YEAR}",
                       "universe_max": UNIVERSE_MAX, "top_by_rd": TOP_BY_RD, "random_rest": RANDOM_REST, "seed": SEED},
             "eligible": len(eligible), "companies": universe}, indent=1))
        print(f"sec: {len(eligible)} eligible, universe of {len(universe)}")
        stage = "companyfacts"
        for c in universe:
            path = OUT / "companyfacts" / f"CIK{c['cik']:010d}.json.gz"
            if not path.exists():
                fetcher.get_json(FACTS_URL.format(cik=c["cik"]), dest=path)
    except AccessRefused as exc:
        _status({"status": "refused", "stage": stage, "http_status": exc.status, "url": exc.url,
                 "requests_made": fetcher.requests_made,
                 "note": "SEC refused the request. SEC fetching stopped; the pipeline continues without SEC data."})
        print(f"sec: REFUSED with HTTP {exc.status} at stage '{stage}'. SEC fetching stopped.")
        return {"status": "refused", "universe_size": 0}
    info = {"status": "ok", "universe_size": len(universe), "eligible": len(eligible),
            "candidates": len(candidates), "requests_made": fetcher.requests_made,
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    done.write_text(json.dumps(info, indent=2))
    _status({"status": "ok", "requests_made": fetcher.requests_made})
    return info
