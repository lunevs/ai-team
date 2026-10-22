"""QA compares executed PostgreSQL output with independent Decimal source reference."""
from decimal import Decimal,InvalidOperation
from app.synthetic import reference

KEYS=('month','product_id','region')
METRICS=('balance','dpd30_balance','dpd90_balance','npl90_ratio','interest_income')


def check_rows(rows):
    failures=[]
    expected={tuple(str(r[k]) for k in KEYS):r for r in reference()}
    actual={}
    for row in rows:
        if any(k not in row for k in KEYS+METRICS):
            failures.append({'check':'columns','detail':'Missing required key/metric'});continue
        key=tuple(str(row[k]) for k in KEYS)
        if key in actual:failures.append({'check':'unique_grain','key':key})
        actual[key]=row
    if set(actual)!=set(expected):failures.append({'check':'complete_grid','missing':sorted(set(expected)-set(actual)),'extra':sorted(set(actual)-set(expected))})
    for key in set(actual)&set(expected):
        for name in METRICS:
            a,e=actual[key].get(name),expected[key][name]
            if a is None or e is None:
                if a is not None or e is not None:failures.append({'check':name,'key':key,'expected':e,'actual':a})
                continue
            try:
                value=Decimal(str(a));target=Decimal(e)
                if not value.is_finite() or abs(value-target)>Decimal('0.0000005' if name=='npl90_ratio' else '0.0000001'):
                    failures.append({'check':name,'key':key,'expected':e,'actual':a})
            except (InvalidOperation,ValueError):failures.append({'check':name,'key':key,'detail':'Non-numeric result'})
        try:
            b,d30,d90=(Decimal(str(actual[key][n])) if actual[key][n] is not None else None for n in METRICS[:3])
            if b is not None and (d30 is None or d90 is None or not 0<=d90<=d30<=b):failures.append({'check':'economic_hierarchy','key':key})
        except (InvalidOperation,TypeError):pass
    return {'passed':not failures,'expected_rows':24,'actual_rows':len(rows),'failures':failures[:50],
            'checks':['independent_source_reference','unique_month_product_region','24_slice_grid',
                      'month_end_no_carry','DPD29_30_89_90','monthly_income','zero_denominator_NULL',
                      'empty_slice_NULL','economic_hierarchy','NPL_ratio_precision'],
            'reference_backend':'Python Decimal; independent of generated SQL'}
