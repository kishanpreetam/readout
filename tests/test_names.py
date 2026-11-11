from readout.names import core, industry_words, normalize, tokens


def test_legal_suffixes_and_sec_state_suffix_are_removed():
    assert normalize("VERTEX PHARMACEUTICALS INC / MA") == "vertex pharmaceuticals"
    assert normalize("Vertex Pharmaceuticals Incorporated") == "vertex pharmaceuticals"
    assert normalize("Jiangsu HengRui Medicine Co., Ltd.") == "jiangsu hengrui medicine"
    assert normalize("Laboratorios Silanes S.A. de C.V.") == "laboratorios silanes"


def test_core_drops_industry_words():
    assert core("Gilead Sciences, Inc.") == "gilead"
    assert core("Arcus Biosciences, Inc.") == "arcus"
    assert core("Day One Biopharmaceuticals, Inc.") == "day one"


def test_kgaa_is_kept_so_merck_kgaa_differs_from_merck_and_co():
    assert core("Merck KGaA, Darmstadt, Germany") != core("MERCK & CO., INC.")
    assert core("MERCK & CO., INC.") == "merck"


def test_special_tokens():
    assert normalize("Novo Nordisk A/S") == "novo nordisk"
    assert "rd" in tokens("Janssen R&D Ireland")
    assert tokens("ModernaTX, Inc.")[:2] == ["moderna", "tx"]
    assert core("ModernaTX, Inc.") == "moderna"


def test_industry_word_families():
    assert industry_words("Prime Medicine, Inc.") == {"medicine"}
    assert industry_words("Arcus Bioscience") == industry_words("Arcus Biosciences")
