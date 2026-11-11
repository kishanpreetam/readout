import gzip
import json

import duckdb

from readout import config, sites
from readout.sites import coordinate, page_rows, site_rows

BOSTON = {"facility": "Dana-Farber Cancer Institute", "status": "RECRUITING", "city": "Boston",
          "state": "Massachusetts", "zip": "02215", "country": "United States",
          "geoPoint": {"lat": 42.35843, "lon": -71.05977}}
# Many Japanese sites name a ward and come without coordinates
CHUO = {"facility": "Research Site", "city": "Chuo-ku", "country": "Japan"}


def _study(nct, *locations):
    study = {"protocolSection": {"identificationModule": {"nctId": nct}}}
    if locations:
        study["protocolSection"]["contactsLocationsModule"] = {"locations": list(locations)}
    return study


def test_site_rows_keep_registry_order_and_fields():
    rows = site_rows(_study("NCT00000001", BOSTON, CHUO))
    assert rows[0] == {"nct_id": "NCT00000001", "site_order": 1, "facility": "Dana-Farber Cancer Institute",
                       "city": "Boston", "state": "Massachusetts", "zip": "02215", "country": "United States",
                       "site_status": "RECRUITING", "lat": 42.35843, "lon": -71.05977}
    assert rows[1]["site_order"] == 2 and rows[1]["city"] == "Chuo-ku"
    assert rows[1]["state"] is None and rows[1]["site_status"] is None
    assert rows[1]["lat"] is None and rows[1]["lon"] is None


def test_studies_without_locations_have_no_rows():
    assert site_rows(_study("NCT00000002")) == []
    assert site_rows({"protocolSection": {"identificationModule": {"nctId": "NCT00000003"},
                                          "contactsLocationsModule": {"locations": []}}}) == []
    assert site_rows({}) == []


def test_coordinates_parse_to_floats_or_none():
    assert coordinate(42.35843) == 42.35843 and coordinate(-71) == -71.0 and coordinate("12.5") == 12.5
    for bad in (None, "", "north", True, float("nan"), float("inf"), {"lat": 1}):
        assert coordinate(bad) is None
    assert coordinate(95.0) == 95.0  # range checks happen in dbt, so the raw table keeps what was sent


def test_page_rows_keep_each_study_once_across_pages():
    page1 = {"studies": [_study("NCT00000001", BOSTON), _study("NCT00000002", CHUO, BOSTON)]}
    page2 = {"studies": [_study("NCT00000002", CHUO), _study(None, BOSTON), _study("NCT00000004", CHUO)]}
    seen = set()
    rows = list(page_rows([page1, page2], seen))
    assert [(r["nct_id"], r["site_order"]) for r in rows] == [
        ("NCT00000001", 1), ("NCT00000002", 1), ("NCT00000002", 2), ("NCT00000004", 1)]
    assert seen == {"NCT00000001", "NCT00000002", "NCT00000004"}


def test_load_writes_a_typed_raw_table(tmp_path, monkeypatch):
    raw = tmp_path / "ctgov_sites"
    raw.mkdir()
    with gzip.open(raw / "sites_0001.json.gz", "wt") as fh:
        json.dump({"studies": [_study("NCT00000001", BOSTON, CHUO), _study("NCT00000002")]}, fh)
    (raw / "_complete.json").write_text("{}")
    monkeypatch.setattr(sites, "OUT", raw)
    monkeypatch.setattr(config, "CACHE", tmp_path / "cache")
    monkeypatch.setattr(config, "WAREHOUSE_DIR", tmp_path)
    monkeypatch.setattr(config, "WAREHOUSE_DB", tmp_path / "fixture.duckdb")
    assert sites.load() == {"studies": 2, "ct_sites": 2}
    assert not (tmp_path / "cache" / "ct_sites.ndjson").exists()
    with duckdb.connect(str(tmp_path / "fixture.duckdb"), read_only=True) as con:
        types = dict(con.execute("select column_name, data_type from information_schema.columns "
                                 "where table_schema = 'raw' and table_name = 'ct_sites'").fetchall())
        rows = con.execute("select nct_id, site_order, city, lat, lon from raw.ct_sites order by site_order").fetchall()
    assert types == sites.COLUMNS
    assert rows == [("NCT00000001", 1, "Boston", 42.35843, -71.05977), ("NCT00000001", 2, "Chuo-ku", None, None)]
