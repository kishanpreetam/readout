"""Download interventional, industry-sponsored studies from the ClinicalTrials.gov API v2."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from . import config
from .http import Fetcher

API = "https://clinicaltrials.gov/api/v2/studies"
OUT = config.RAW / "ctgov"

# Industry lead sponsor, interventional, primary completion (actual or anticipated) on or after 2020-01-01.
QUERY = (
    "AREA[LeadSponsorClass]INDUSTRY AND AREA[StudyType]INTERVENTIONAL "
    "AND AREA[PrimaryCompletionDate]RANGE[2020-01-01,MAX]"
)
FIELDS = [
    "NCTId", "BriefTitle", "OfficialTitle", "Acronym", "OverallStatus", "WhyStopped",
    "StartDate", "StartDateType", "PrimaryCompletionDate", "PrimaryCompletionDateType", "CompletionDate",
    "CompletionDateType", "StudyFirstPostDate", "LastUpdatePostDate", "HasResults",
    "LeadSponsor", "Collaborator", "StudyType", "Phase", "EnrollmentInfo", "DesignAllocation",
    "DesignPrimaryPurpose", "Condition", "Keyword", "Intervention", "PrimaryOutcome", "BriefSummary",
]
PAGE_SIZE = 1000


def fetch(force: bool = False) -> dict:
    done = OUT / "_complete.json"
    if done.exists() and not force:
        info = json.loads(done.read_text())
        print(f"ctgov: cached, {info['studies']} studies in {info['pages']} pages ({info['fetched_at']})")
        return info
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("studies_*.json.gz"):
        old.unlink()
    fetcher = Fetcher(config.USER_AGENT, config.CTGOV_MIN_INTERVAL_S)
    params = {
        "filter.advanced": QUERY,
        "fields": ",".join(FIELDS),
        "pageSize": PAGE_SIZE,
        "countTotal": "true",
        "format": "json",
    }
    page, studies, total, token = 0, 0, None, None
    while True:
        page += 1
        if token:
            params["pageToken"] = token
            params.pop("countTotal", None)
        data = fetcher.get_json(API, params=params, dest=OUT / f"studies_{page:04d}.json.gz")
        if total is None:
            total = data.get("totalCount")
        studies += len(data.get("studies", []))
        token = data.get("nextPageToken")
        print(f"ctgov: page {page}, {studies}/{total} studies")
        if not token:
            break
    info = {
        "query": QUERY,
        "fields": FIELDS,
        "studies": studies,
        "total_count_reported": total,
        "pages": page,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    done.write_text(json.dumps(info, indent=2))
    snapshot = {"as_of_date": info["fetched_at"][:10], "source": "ClinicalTrials.gov download date"}
    config.SNAPSHOT.write_text(json.dumps(snapshot, indent=2))
    return info
