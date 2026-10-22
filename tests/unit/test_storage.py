import hashlib,uuid
import pytest
from app.storage import LocalStore,S3Store,IntegrityError,StorageError,read_artifact

def test_local_store_is_immutable_and_checks_legacy_manifests(tmp_path,monkeypatch):
    id=uuid.uuid4();s=LocalStore(tmp_path);data=b'fixed report';manifest=s.put(id,'report.xlsx',data)
    assert s.put(id,'report.xlsx',data)==manifest and s.get(id,'report.xlsx',manifest)==data
    with pytest.raises(IntegrityError):s.put(id,'report.xlsx',b'changed report')
    monkeypatch.setenv('ARTIFACT_ROOT',str(tmp_path))
    legacy={k:v for k,v in manifest.items() if k!='backend'}
    assert read_artifact(id,'report.xlsx',legacy)==data
    s.path(id,'report.xlsx').write_bytes(b'corrupt')
    with pytest.raises(IntegrityError):read_artifact(id,'report.xlsx',manifest)
    with pytest.raises(ValueError):s.put(id,'../../.env',b'x')
    with pytest.raises(ValueError):s.put('not-uuid','qa.json',b'x')

def test_s3_requires_tls_and_explicit_local_exception(monkeypatch):
    monkeypatch.setenv('S3_ENDPOINT','http://external.example')
    monkeypatch.setenv('S3_ALLOW_LOCAL_HTTP','true')
    with pytest.raises(StorageError,match='HTTPS'):S3Store()
    for endpoint in ['https://user:secret@example.com','https://example.com/?secret=x']:
        monkeypatch.setenv('S3_ENDPOINT',endpoint)
        with pytest.raises(StorageError,match='endpoint'):S3Store()

def test_s3_digest_keys_and_retries_are_idempotent(monkeypatch):
    for k,v in {'S3_ENDPOINT':'https://storage.example','S3_BUCKET':'test-bucket','S3_REGION':'test-region','S3_ACCESS_KEY_ID':'test','S3_SECRET_ACCESS_KEY':'test'}.items():monkeypatch.setenv(k,v)
    s=S3Store();objects={};writes=[]
    def request(method,key,content=b''):
        if method=='PUT':objects[key]=content;writes.append(key);return b''
        return objects.get(key)
    monkeypatch.setattr(s,'request',request)
    id=uuid.uuid4();m=s.put(id,'query.sql',b'SELECT 1');assert s.put(id,'query.sql',b'SELECT 1')==m
    assert len(writes)==1 and s.get(id,'query.sql',m)==b'SELECT 1'
    objects[writes[0]]=b'changed'
    with pytest.raises(IntegrityError):s.get(id,'query.sql',m)
