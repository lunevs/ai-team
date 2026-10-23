from app.capabilities import registry
from app.product_policy import MAX_PACKAGE, compose


def test_registry_describes_real_profile_and_has_stable_digest():
    first=registry()
    assert first==registry()
    assert len(first['digest'])==64
    assert first['limits']['source_total_bytes']==MAX_PACKAGE
    app=compose('p-012345678abc','1.0.1','service')['services']['app']
    assert list(app['networks'])==['products']
    assert first['capabilities']['external_http']['supported'] is False
    assert first['capabilities']['airflow']['supported'] is False
    assert first['capabilities']['python_service']['supported'] is True
