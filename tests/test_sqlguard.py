import duckdb
import pytest

from readout import sqlguard


@pytest.fixture()
def con(tmp_path):
    path = tmp_path / "w.duckdb"
    with duckdb.connect(str(path)) as c:
        c.execute("create schema marts; create table marts.t as select 1 as x union all select 2")
    c = sqlguard.sandbox_connection(path)
    yield c
    c.close()


def test_select_runs(con):
    cols, rows = sqlguard.run(con, "with a as (select x from marts.t) select sum(x) as s from a")
    assert cols == ["s"] and rows == [(3,)]


@pytest.mark.parametrize("sql", [
    "select * from read_csv('/etc/passwd')",
    "select 1; drop table marts.t",
    "copy (select 1) to 'x.csv'",
    "attach 'x.db' as x",
    "pragma version",
    "set enable_external_access = true",
    "select getenv('HOME')",
    "insert into marts.t values (3)",
])
def test_unsafe_sql_is_rejected(con, sql):
    with pytest.raises(sqlguard.UnsafeSQL):
        sqlguard.run(con, sql)


def test_database_layer_blocks_file_access_even_without_the_guard(con):
    with pytest.raises(duckdb.Error):
        con.execute("select * from '/etc/hosts'")


def test_words_inside_strings_are_allowed(con):
    assert sqlguard.run(con, "select 'drop table' as s")[1] == [("drop table",)]
