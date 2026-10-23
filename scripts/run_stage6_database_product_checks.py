"""Hermetic DB-product smoke; no customer schema/warehouse/data source is designed."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import uuid
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.product_policy import build_package
from scripts.product_operator import check_package


def main():
    root=Path(__file__).resolve().parents[1]
    source=(root/'app/product_templates/smoke_app.py').read_text()
    source=source.replace("if __name__=='__main__':\n", "if __name__=='__main__':\n    import os,psycopg\n    with psycopg.connect(os.environ['DATABASE_URL'],connect_timeout=5) as conn:\n        assert conn.execute('SELECT 42').fetchone()[0]==42\n")
    solution=SimpleNamespace(title='Empty test PostgreSQL protocol',kind='service',output_type='scalar',needs_database=True,
        architecture='Technical fixture connects to an empty isolated test PostgreSQL. No warehouse tables or source loading.',
        operating_notes='Only SELECT 42 on a temporary database. No customer data/schema.',
        files={'app.py':source,'tests/test_product.py':(root/'app/product_templates/smoke_test.py').read_text()})
    package,_=build_package(solution,'p-6e0000000004','1.0.1',uuid.uuid4(),1)
    result=check_package(package,{'operation_id':str(uuid.uuid4())})
    assert result['passed'] and result['result']=={'value':42,'unit':'synthetic-check'},result.get('error_type')
    report={'status':'passed','checks':['offline unittest','isolated empty PostgreSQL for needs_database smoke','actual DB connection and SELECT 42','HTTP health and result','temporary database/network cleanup']}
    (root/'docs/stage6-database-product-check.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))

if __name__=='__main__':main()
