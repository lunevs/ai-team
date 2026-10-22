import pytest
from app.sql_guard import validate_select,UnsafeSQL

@pytest.mark.parametrize('query',[
 'DELETE FROM synthetic.fact_loan_daily',
 'SELECT * INTO public.stolen FROM synthetic.fact_loan',
 'WITH x AS (DELETE FROM synthetic.fact_loan RETURNING *) SELECT * FROM x',
 'SELECT pg_read_file(\'/etc/passwd\')',
 'SELECT pg_sleep(10)', 'SELECT * FROM public.stories',
 'SELECT * FROM pg_catalog.pg_authid', 'SELECT 1; SELECT 2',
 'SELECT 1::regclass', 'SELECT * FROM synthetic.fact_loan FOR UPDATE',
 'WITH RECURSIVE t AS (SELECT 1) SELECT * FROM t',
 'COPY (SELECT 1) TO PROGRAM \'id\'',
])
def test_forbidden_sql(query):
 with pytest.raises(UnsafeSQL):validate_select(query)

def test_allowed_aggregate():
 assert validate_select('WITH x AS (SELECT sum(outstanding_balance) AS balance FROM synthetic.fact_loan_daily) SELECT round(balance,2) FROM x')
