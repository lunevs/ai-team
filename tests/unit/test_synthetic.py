from decimal import Decimal
from app.synthetic import dataset,checksum,reference


def test_seed_is_reproducible_and_contains_boundaries():
    a=dataset();assert checksum(a)==checksum(dataset())
    assert {29,30,89,90}<=set(r['dpd'] for r in a['fact_loan_daily'])
    assert len(a['dim_calendar'])==181


def test_independent_hand_calculated_month_end():
    rows=reference()
    jan=next(r for r in rows if r['month']=='2026-01' and r['product_id']==1 and r['region']=='North')
    assert jan['balance']=='10000.00'
    assert jan['dpd30_balance']=='9000.00'
    assert jan['dpd90_balance']=='4000.00'
    assert jan['npl90_ratio']=='0.400000'
    assert jan['missing_snapshot_loans']==1
    june=next(r for r in rows if r['month']=='2026-06' and r['product_id']==1 and r['region']=='South')
    assert june['balance'] is None and june['interest_income'] is None
    zero=next(r for r in rows if r['month']=='2026-01' and r['product_id']==2 and r['region']=='South')
    assert zero['balance']=='0.00' and zero['npl90_ratio'] is None
    assert all(r['npl90_ratio'] is None or Decimal(0)<=Decimal(r['npl90_ratio'])<=1 for r in rows)
