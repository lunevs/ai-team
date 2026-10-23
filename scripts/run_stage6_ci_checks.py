"""Run exactly the product CI entry point against a disposable exported fixture."""
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import uuid
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.product_policy import build_package
from scripts import product_operator as host
from scripts.check_product_directories import main as validate_products


def main():
    root=Path(__file__).resolve().parents[1]
    source=root/'app/product_templates'
    solution=SimpleNamespace(title='CI protocol fixture',kind='service',output_type='scalar',needs_database=False,
        architecture='Independent synthetic fixture for the exact CI product validation entry point.',operating_notes='No external sources or database.',
        files={'app.py':(source/'smoke_app.py').read_text(),'tests/test_product.py':(source/'smoke_test.py').read_text()})
    content,_=build_package(solution,'p-6e0000000005','1.0.1',uuid.uuid4(),1)
    previous=Path.cwd()
    try:
        with tempfile.TemporaryDirectory(prefix='ai-product-ci-') as temporary:
            host.extract(content,Path(temporary));os.chdir(temporary);validate_products()
    finally:os.chdir(previous)
    report={'status':'passed','checks':['repository manifest validation','CI entry point builds trusted Dockerfile','offline unittest and HTTP smoke','no team service/secret dependency']}
    (root/'docs/stage6-ci-check.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))

if __name__=='__main__':main()
