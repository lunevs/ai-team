import io
import json
import uuid
import zipfile
from pathlib import Path
import pytest
from pydantic import ValidationError
from app.product_contract import ProductSolution,build_package,inspect_package,safe_path,compose


def solution():
    root=Path('app/product_templates')
    return ProductSolution(title='Синтетическая проверка поставки',kind='service',output_type='scalar',needs_database=False,
        architecture='Независимый Python сервис с отдельными файлами и проверяемым результатом.',
        operating_notes='Миграции и внешние данные отсутствуют. Результат: value=42.',
        files={'app.py':(root/'smoke_app.py').read_text(),'tests/test_product.py':(root/'smoke_test.py').read_text()})


def test_product_bundle_is_reproducible_and_portable():
    s=solution();id=uuid.uuid4()
    first,meta=build_package(s,'p-012345678abc','1.0.1',id,1)
    assert first==build_package(s,'p-012345678abc','1.0.1',id,1)[0]
    checked,files=inspect_package(first)
    assert meta==checked
    assert {'app.py','install.md','Dockerfile','docker-compose.yml','product.json'}<=set(files)
    assert b'OpenProject' in files['install.md']
    policy=json.loads(files['docker-compose.yml'])['services']['app']
    assert policy['cap_drop']==['ALL'] and policy['read_only']
    assert all('docker.sock' not in x for x in policy['volumes'])


@pytest.mark.parametrize('path',['../x.py','/x.py','a/../x.py','a//x.py','.env','a/.env','a\\x.py','a.sh','a/./x.py'])
def test_product_paths_reject_traversal_and_secrets(path):
    with pytest.raises(ValueError):safe_path(path)


def test_sources_cannot_define_build_commands():
    s=solution().model_dump();s['files']['Dockerfile']='FROM evil'
    with pytest.raises(ValidationError):ProductSolution.model_validate(s)
    s=solution().model_dump();s['files']['app.py']='import ('
    with pytest.raises(SyntaxError):ProductSolution.model_validate(s)


def test_package_rejects_tampering_even_with_changed_manifest():
    content,_=build_package(solution(),'p-012345678abc','1.0.1',uuid.uuid4(),1)
    archive=zipfile.ZipFile(io.BytesIO(content));files={i.filename:archive.read(i) for i in archive.infolist()}
    root='products/p-012345678abc/'
    files[root+'Dockerfile']=b'FROM evil\n'
    import hashlib
    manifest=json.loads(files[root+'product.json'])
    manifest['files']['Dockerfile']={'sha256':hashlib.sha256(files[root+'Dockerfile']).hexdigest(),'size':len(files[root+'Dockerfile'])}
    files[root+'product.json']=json.dumps(manifest).encode()
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w') as result:
        for name,data in files.items():result.writestr(name,data)
    with pytest.raises(ValueError,match='Build policy'):inspect_package(output.getvalue())


def test_job_has_no_published_port_or_restart():
    service=compose('p-012345678abc','1.0.1','job')['services']['app']
    assert 'ports' not in service and service['restart']=='no'
