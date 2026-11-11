from readout.clauses import parse


def test_subsidiary_chain_keeps_every_parent():
    p = parse("ArQule, Inc., a subsidiary of Merck Sharp & Dohme LLC, a subsidiary of Merck & Co., Inc. (Rahway, NJ USA)")
    assert p.base == "ArQule, Inc."
    assert p.parents == ["Merck Sharp & Dohme LLC", "Merck & Co., Inc."]


def test_company_of_patterns():
    assert parse("Kite, A Gilead Company").parents == ["Gilead"]
    assert parse("Bellus Health Inc. - a GSK company").parents == ["GSK"]
    assert parse("RAPT Therapeutics, Inc., a member of the GSK plc group of companies").parents == ["GSK plc"]
    p = parse("Morphic Therapeutic, Inc. (A Wholly Owned Subsidiary of Eli Lilly and Company)")
    assert p.base == "Morphic Therapeutic, Inc." and p.parents == ["Eli Lilly and Company"]


def test_aliases_and_mergers():
    assert parse("Advanced Cooling Therapy, Inc., d/b/a Attune Medical").aliases == ["Attune Medical"]
    assert parse("Chinook Therapeutics, Inc. (formerly Aduro)").aliases == ["Aduro"]
    assert parse("Pfizer's Upjohn has merged with Mylan to form Viatris Inc.").base == "Viatris Inc."


def test_plain_company_names_are_left_alone():
    for name in ["La Jolla Pharmaceutical Company", "Sun Pharma Advanced Research Company Limited", "Pfizer"]:
        p = parse(name)
        assert p.parents == [] and p.aliases == [] and p.base == name
