import pytest

from alchemist.sqlguard import SQLRejected, check_sql

ALLOWED = {"quality_lots", "historian_line2"}


@pytest.mark.parametrize("sql,used", [
    ("select * from ds.quality_lots", ["quality_lots"]),
    ("select a from ds.quality_lots join ds.historian_line2 using (line)", ["historian_line2", "quality_lots"]),
    ("with x as (select * from ds.quality_lots) select count(*) from x", ["quality_lots"]),
    ("select * from ds.quality_lots where lot_id in (select lot_id from ds.quality_lots)", ["quality_lots"]),
])
def test_allowed(sql, used):
    assert check_sql(sql, ALLOWED) == used


@pytest.mark.parametrize("sql", [
    "select * from read_parquet('s3://plant-data/x.parquet')",
    "select * from 's3://plant-data/x.parquet'",
    "select * from ds.historian_line5",
    "select read_text('/etc/passwd')",
    "select * from main.secrets",
    "copy (select 1) to 'x.csv'",
    "create table t as select 1",
    "select 1; select 2",
    "select getenv('AWS_SECRET_ACCESS_KEY')",
])
def test_rejected(sql):
    with pytest.raises(SQLRejected):
        check_sql(sql, ALLOWED)
