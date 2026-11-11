from readout.linking import Linker

COMPANIES = [
    {"cik": 310158, "name": "MERCK & CO., INC.", "former_names": ["MERCK & CO INC"], "aliases": ["Merck Sharp & Dohme LLC"]},
    {"cik": 78003, "name": "PFIZER INC", "former_names": [], "aliases": []},
    {"cik": 875320, "name": "VERTEX PHARMACEUTICALS INC / MA", "former_names": [], "aliases": []},
    {"cik": 1682852, "name": "Moderna, Inc.", "former_names": [], "aliases": []},
]


def test_tiers():
    linker = Linker(COMPANIES)
    assert linker.link("Vertex Pharmaceuticals Incorporated").cik == 875320
    assert linker.link("Pfizer Japan Inc.").method == "affiliate"
    assert linker.link("ModernaTX, Inc.").cik == 1682852
    assert linker.link("Merck Sharp & Dohme LLC").alias_source == "curated_alias"


def test_lookalikes_stay_unlinked():
    linker = Linker(COMPANIES)
    assert linker.link("Merck KGaA, Darmstadt, Germany").cik is None
    assert linker.link("Vertx Pharmaceuticals").cik is None  # below the fuzzy threshold
