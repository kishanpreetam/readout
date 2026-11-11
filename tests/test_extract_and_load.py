from readout.extract import _match, baseline_extract, score
from readout.load import fact_rows, flatten_study


def test_flatten_study():
    study = {"protocolSection": {
        "identificationModule": {"nctId": "NCT1", "briefTitle": "T"},
        "statusModule": {"overallStatus": "RECRUITING", "primaryCompletionDateStruct": {"date": "2027-03", "type": "ESTIMATED"}},
        "sponsorCollaboratorsModule": {"leadSponsor": {"name": "Acme", "class": "INDUSTRY"},
                                       "collaborators": [{"name": "Beta", "class": "INDUSTRY"}]},
        "designModule": {"phases": ["PHASE2", "PHASE3"]},
        "conditionsModule": {"conditions": ["Asthma"]},
        "armsInterventionsModule": {"interventions": [{"type": "DRUG", "name": "X-1", "otherNames": ["xumab"]}]},
    }}
    flat = flatten_study(study)
    row = flat["ct_studies"][0]
    assert row["nct_id"] == "NCT1" and row["phases"] == ["PHASE2", "PHASE3"]
    assert row["primary_completion_date"] == "2027-03" and row["primary_completion_date_type"] == "ESTIMATED"
    assert flat["ct_collaborators"][0]["name"] == "Beta"
    assert flat["ct_interventions"][0]["other_names"] == ["xumab"]


def test_fact_rows_keep_only_needed_usd_facts_from_10k_and_10q():
    facts = {"facts": {"us-gaap": {
        "CashAndCashEquivalentsAtCarryingValue": {"units": {"USD": [
            {"end": "2025-12-31", "val": 10, "form": "10-K", "fy": 2025, "fp": "FY", "filed": "2026-02-01", "accn": "a"},
            {"end": "2025-12-31", "val": 11, "form": "8-K", "filed": "2026-01-15", "accn": "b"}]}},
        "Goodwill": {"units": {"USD": [{"end": "2025-12-31", "val": 5, "form": "10-K"}]}},
    }}}
    rows = fact_rows(1, facts)
    assert len(rows) == 1 and rows[0]["value"] == 10.0 and rows[0]["concept"] == "CashAndCashEquivalentsAtCarryingValue"


def test_extraction_matching_and_scoring():
    assert _match("non-small cell lung cancer", ["Non-Small Cell Lung Cancer"])
    assert not _match("asthma", ["Psoriasis"])
    trials = [{"id": "N1", "title": "Drug ABC-123 in asthma", "summary": "Adults with asthma receive ABC-123."}]
    gold = {"N1": {"conditions": ["Asthma"], "interventions": [["ABC-123"]], "endpoints": ["FEV1"]}}
    pred = {"N1": baseline_extract(trials[0], {"asthma"}, set())}
    s = score(trials, pred, gold)
    assert s["conditions"]["recall"] == 1.0 and s["interventions"]["recall"] == 1.0
