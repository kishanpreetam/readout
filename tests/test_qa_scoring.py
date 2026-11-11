import datetime as dt

from readout.qa import QUESTIONS, matches


def test_scalar_matching_tolerates_extra_columns_and_types():
    assert matches("count", [(787,)], [("Pfizer", 787)])
    assert matches("number", [(12.6,)], [(12.6000001,)])
    assert not matches("count", [(787,)], [(788,)])
    assert matches("date", [(dt.date(2026, 11, 20),)], [("2026-11-20",)])
    assert matches("text", [("AstraZeneca",)], [("astrazeneca", 815)])


def test_list_matching_ignores_order_but_not_membership():
    gold = [("A",), ("B",), ("C",)]
    assert matches("list", gold, [("C", 1), ("A", 2), ("B", 3)])
    assert not matches("list", gold, [("A",), ("B",)])
    assert not matches("list", gold, [("A",), ("B",), ("D",)])


def test_benchmark_size_and_ids():
    ids = [q[0] for q in QUESTIONS]
    assert 30 <= len(ids) <= 60 and len(ids) == len(set(ids))
