import pytest
from app.portability import database_parameters
from app.providers import provider_for,PrivateLLMStub,CapabilityError

def test_external_postgresql_requires_hostname_verification():
    assert database_parameters('postgresql://runtime:secret@postgres/ai_team')['host']=='postgres'
    for suffix in ['','?sslmode=require','?sslmode=verify-full','?hostaddr=127.0.0.1&sslmode=verify-full&sslrootcert=ca.crt']:
        with pytest.raises(ValueError):database_parameters('postgresql://runtime:secret@db.example/ai_team'+suffix)
    assert database_parameters('postgresql://runtime:secret@db.example/ai_team?sslmode=verify-full&sslrootcert=ca.crt')['sslmode']=='verify-full'
    with pytest.raises(ValueError) as error:database_parameters('not-a-dsn SECRET_CANARY')
    assert 'SECRET_CANARY' not in str(error.value)

def test_private_provider_does_not_fallback_or_access_network(monkeypatch):
    p=provider_for('private');assert isinstance(p,PrivateLLMStub)
    for method,args in [(p.generate_structured,('pm','prompt',object)),(p.generate_text,('prompt',)),(p.tool_call,())]:
        with pytest.raises(CapabilityError,match='not implemented'):method(*args)
    with pytest.raises(CapabilityError):provider_for('unknown')

def test_private_stub_rejects_before_db_cache_or_budget(monkeypatch):
    from types import SimpleNamespace
    from app import providers
    monkeypatch.setattr(providers,'settings',SimpleNamespace(backend='private'))
    monkeypatch.setattr(providers.db,'active',lambda *a:pytest.fail('Private stub touched DB/cache'))
    with pytest.raises(CapabilityError,match='not implemented'):providers._generate('unused','pm',object,{})
