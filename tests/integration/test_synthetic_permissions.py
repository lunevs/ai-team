import pytest
import psycopg
from app import db
from app.synthetic import dataset,checksum,metadata


def test_seed_metadata_and_database_constraints():
    with db.connection() as conn:
        info=metadata(conn)
        assert info['dataset']['checksum']==checksum(dataset())
        assert info['dataset']['row_counts']['dim_calendar']==181
        row=conn.execute("SELECT sum(outstanding_balance) AS balance FROM synthetic.fact_loan_daily WHERE date='2026-01-31' AND loan_id<=4").fetchone()
        assert row['balance']==10000


def test_runtime_cannot_modify_sources():
    for sql in ['DELETE FROM synthetic.fact_loan_daily',
                'UPDATE synthetic.dim_customer SET region=region',
                'CREATE TABLE synthetic.forbidden(id int)']:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with db.connection() as conn:conn.execute(sql)
