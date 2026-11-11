from readout.resolve import pair_rule, profile, resolve


def rule(a, b):
    return pair_rule(profile(a), profile(b))


def test_pair_rules():
    assert rule("Prothena Biosciences Ltd.", "Prothena Biosciences Limited") == "exact"
    assert rule("Arcus Biosciences, Inc.", "Arcus Bioscience") == "core"
    assert rule("Pfizer", "Pfizer Japan Inc.") == "affiliate"
    assert rule("Janssen Pharmaceuticals", "Janssen R&D Ireland") == "affiliate"


def test_pair_rules_keep_lookalikes_apart():
    assert rule("Merck KGaA, Darmstadt, Germany", "Merck & Co., Inc.") is None
    assert rule("Prime Medicine, Inc.", "Prime Pharmaceuticals LLC") is None  # industry words disagree
    assert rule("Boston Pharmaceuticals", "Boston Scientific Corporation") is None


def test_resolve_uses_parent_clauses_and_names_group_after_parent():
    names = ["Pfizer", "Pfizer Inc.", "Seagen, a wholly owned subsidiary of Pfizer", "Seagen Inc.",
             "Kite, A Gilead Company", "Gilead Sciences", "Boston Scientific Corporation", "Boston Pharmaceuticals"]
    counts = {"Pfizer": 800, "Gilead Sciences": 190, "Seagen Inc.": 30}
    res = resolve(names, counts)
    group_of = {names[i]: k for k, members in enumerate(res["orgs"]) for i in members}
    assert group_of["Seagen Inc."] == group_of["Pfizer"] == group_of["Pfizer Inc."]
    assert group_of["Kite, A Gilead Company"] == group_of["Gilead Sciences"]
    assert group_of["Boston Pharmaceuticals"] != group_of["Boston Scientific Corporation"]
    assert res["org_names"][group_of["Pfizer"]] == "Pfizer"
