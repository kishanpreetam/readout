"""Paths and settings shared by every pipeline step."""

from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RAW = DATA / "raw"
CACHE = DATA / "cache"
WAREHOUSE_DIR = DATA / "warehouse"
WAREHOUSE_DB = WAREHOUSE_DIR / "readout.duckdb"
RESULTS = ROOT / "results"
LABELS = ROOT / "labels"
DBT_DIR = ROOT / "warehouse"
MANIFEST = RAW / "manifest.jsonl"
SNAPSHOT = RAW / "snapshot.json"

# Every request names the project and its public profile. No email address is sent.
USER_AGENT = os.environ.get(
    "READOUT_USER_AGENT", "Readout biotech research project (github.com/kishanpreetam)"
)
# SEC asks automated clients to declare who they are. Set READOUT_SEC_USER_AGENT to your own
# declared string if SEC refuses the default one.
SEC_USER_AGENT = os.environ.get("READOUT_SEC_USER_AGENT", USER_AGENT)

SEC_MIN_INTERVAL_S = 0.2  # at most 5 requests per second, half of the SEC fair access limit
CTGOV_MIN_INTERVAL_S = 0.5
MAX_RESPONSE_BYTES = 200 * 1024 * 1024  # refuse anything larger: downloads are untrusted input

DUCKDB_MEMORY_LIMIT = "1GB"
DUCKDB_THREADS = 2


def as_of_date() -> str:
    """The snapshot date of the trial download (YYYY-MM-DD). Every relative window uses it."""
    if os.environ.get("READOUT_AS_OF"):
        return os.environ["READOUT_AS_OF"]
    if SNAPSHOT.exists():
        return json.loads(SNAPSHOT.read_text())["as_of_date"]
    raise SystemExit("No snapshot yet: run `make fetch` first.")
