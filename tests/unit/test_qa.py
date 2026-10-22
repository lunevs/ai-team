from copy import deepcopy
from app.synthetic import reference
from app.qa import check_rows

def test_independent_reference_and_defects():
 rows=reference();assert check_rows(rows)['passed']
 broken=deepcopy(rows);broken[0]['dpd30_balance']='8000'
 assert not check_rows(broken)['passed']
 duplicate=rows+[rows[0]];assert not check_rows(duplicate)['passed']
 missing=deepcopy(rows);missing[-1]['npl90_ratio']='0'
 assert not check_rows(missing)['passed']
