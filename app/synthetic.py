"""Versioned synthetic-only fixture. No runtime SQL or random provider input."""
import calendar
import hashlib
import json
import random
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

SEED = 4182
VERSION = 'credit-demo-v1'
MONEY = Decimal('0.01')
METRICS = {
    'currency': 'RUB', 'money_scale': 2, 'dpd30_inclusive': 30,
    'dpd90_inclusive': 90, 'npl_definition': 'dpd >= 90',
    'snapshot': 'last_calendar_day', 'missing_snapshot': 'NULL; no carry forward',
    'empty_slice': 'NULL', 'zero_denominator': 'NULL',
    'interest_income': 'sum of daily income within calendar month',
    'ratio_scale': 6, 'rounding': 'ROUND_HALF_UP',
    'demo_definitions_confirmed_on': '2026-10-22',
}


def dataset():
    rng=random.Random(SEED)
    customers=[{'customer_id':i,'region':region,'segment':'synthetic'}
               for i,region in [(1,'North'),(2,'North'),(3,'North'),(4,'North'),(5,'South'),(6,'South'),(7,'North')]]
    products=[{'product_id':1,'name':'Consumer'},{'product_id':2,'name':'Mortgage'}]
    loans=[{'loan_id':i,'customer_id':i,'product_id':2 if i==6 else 1,
            'origination_date':'2026-01-01','principal':str(1000*i),
            'annual_rate':'0.120000'} for i in range(1,8)]
    days=[]; facts=[]
    d=date(2026,1,1)
    while d<=date(2026,6,30):
        days.append({'date':d.isoformat(),'month':d.strftime('%Y-%m'),
                     'is_month_end':d.day==calendar.monthrange(d.year,d.month)[1]})
        for i in range(1,8):
            # Explicit gap: no June observations for South/Consumer.
            if i==5 and d.month==6:continue
            # A loan with a missing final snapshot; no previous-day fallback.
            if i==7 and days[-1]['is_month_end']:continue
            balance=Decimal(1000*i)
            if i==6 or (i==5 and d.month>=4):balance=Decimal(0)
            dpd={1:29,2:30,3:89,4:90,5:0,6:0,7:30}[i]
            if i==1 and d.month>1:dpd=[29,30,89,90,0,0][d.month-1]
            income=Decimal(rng.randrange(1,10))/100 if balance else Decimal(0)
            facts.append({'loan_id':i,'date':d.isoformat(),'outstanding_balance':str(balance.quantize(MONEY, rounding=ROUND_HALF_UP)),
                          'dpd':dpd,'accrued_interest':str(income),'interest_income':str(income)})
        d+=timedelta(days=1)
    return {'dim_customer':customers,'dim_product':products,'fact_loan':loans,
            'dim_calendar':days,'fact_loan_daily':facts}


def checksum(data):
    return hashlib.sha256(json.dumps(data,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def reference(data=None):
    """Independent Python reference, not the generated mart implementation."""
    data=data or dataset()
    customers={x['customer_id']:x for x in data['dim_customer']}
    loans={x['loan_id']:x for x in data['fact_loan']}
    output=[]
    for month in range(1,7):
        last=date(2026,month,calendar.monthrange(2026,month)[1]).isoformat()
        for product in data['dim_product']:
            for region in ['North','South']:
                rows=[r for r in data['fact_loan_daily'] if int(r['date'][5:7])==month
                      and loans[r['loan_id']]['product_id']==product['product_id']
                      and customers[loans[r['loan_id']]['customer_id']]['region']==region]
                snapshots=[r for r in rows if r['date']==last]
                balance=sum((Decimal(r['outstanding_balance']) for r in snapshots),Decimal(0)) if snapshots else None
                late=lambda threshold:sum((Decimal(r['outstanding_balance']) for r in snapshots if r['dpd']>=threshold),Decimal(0)) if snapshots else None
                npl=late(90)
                output.append({'month':f'2026-{month:02d}','product_id':product['product_id'],'region':region,
                    'balance':None if balance is None else str(balance.quantize(MONEY, rounding=ROUND_HALF_UP)),
                    'dpd30_balance':None if balance is None else str(late(30).quantize(MONEY, rounding=ROUND_HALF_UP)),
                    'dpd90_balance':None if npl is None else str(npl.quantize(MONEY, rounding=ROUND_HALF_UP)),
                    'npl90_ratio':str((npl/balance).quantize(Decimal('0.000001'), rounding=ROUND_HALF_UP)) if balance else None,
                    'interest_income':str(sum((Decimal(r['interest_income']) for r in rows),Decimal(0)).quantize(MONEY, rounding=ROUND_HALF_UP)) if rows else None,
                    'source_row_count':len(rows),'snapshot_loan_count':len(snapshots),
                    'missing_snapshot_loans':len({r['loan_id'] for r in rows}-{r['loan_id'] for r in snapshots})})
    return output


def metadata(conn):
    # Fixed schema/table allowlist. No caller-controlled SQL or arbitrary catalog access.
    rows=conn.execute("SELECT table_name,column_name,data_type,is_nullable,numeric_precision,numeric_scale FROM information_schema.columns WHERE table_schema='synthetic' ORDER BY table_name,ordinal_position").fetchall()
    info=conn.execute('SELECT version,seed,checksum,row_counts FROM synthetic.dataset_info WHERE singleton=true').fetchone()
    return {'dataset':info,'columns':rows,'metric_defaults':METRICS,
            'target':'dm_credit_portfolio_monthly', 'synthetic_only':True,'model_contract':MODEL_CONTRACT}

# Business semantics of this versioned fixture, shared with all planning agents.
MODEL_CONTRACT = {
    'dimensions': 'Static dim_product.name and dim_customer.region; no SCD. Full grid: 6 months × 2 products × 2 regions, including unobserved combinations.',
    'missing_snapshot_loans': 'Count DISTINCT loan_id with at least one fact row in the month but no last-calendar-day row in that slice. Exclude loans with no rows at all in that month. Empty set => 0, never NULL. A quality flag, not a monetary measure.',
    'income_source': 'fact_loan_daily.interest_income, NOT accrued_interest; sum available rows independently of last-day coverage. No rows => NULL, do not scale. Synthetic cashflow is not derived from annual_rate.',
    'money': 'ROUND_HALF_UP to 2 decimals for all four money metrics after aggregation. Ratio ROUND_HALF_UP to 6 decimals; NULL if numerator missing, denominator missing or zero.',
    'absent_slice': 'Money and ratio NULL; missing_snapshot_loans=0. This applies also to product/region combinations without loans.',
    'target_contract': 'dm_credit_portfolio_monthly has month YYYY-MM, product_id, region and five nullable metrics balance, dpd30_balance, dpd90_balance, npl90_ratio, interest_income. missing_snapshot_loans is a separate reference/DQ indicator, not a required mart column. Dataset version/seed/checksum are specification metadata, not mandatory mart columns. fact_loan_daily.outstanding_balance is NOT NULL; a NULL daily balance cannot occur in this fixture.',
    'reference_format': 'JSON table in specification and UI. CSV/Excel exports and SQL execution belong to later stages, not blockers for this specification.',
}
