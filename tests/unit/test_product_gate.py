import uuid
from types import SimpleNamespace
from unittest.mock import Mock
from fastapi import HTTPException
import pytest
from app.git_broker import Publish,TaskBundle,publish_task
from app.product_api import operator
from app.products import publish


def test_operator_token_cannot_fall_back_to_app_token(monkeypatch):
    monkeypatch.setenv('APP_API_TOKEN','app-secret');monkeypatch.setenv('PRODUCT_OPERATOR_TOKEN','operator-secret')
    with pytest.raises(HTTPException) as e:operator('Bearer app-secret')
    assert e.value.status_code==401
    assert operator('Bearer operator-secret')=='product-operator'
    monkeypatch.delenv('PRODUCT_OPERATOR_TOKEN')
    with pytest.raises(HTTPException):operator('Bearer app-secret')


def test_broker_rejects_product_traversal_before_github(monkeypatch):
    from app import git_broker
    network=Mock(side_effect=AssertionError('Network must not be called'))
    monkeypatch.setattr(git_broker,'github',network)
    data=Publish(run_id=uuid.uuid4(),story_id=uuid.uuid4(),revision=1,documentation_url='http://docs',product_package=True,
        tasks=[TaskBundle(task_id=1,epic_id=2,role='application_developer',files={'products/p-012345678abc/../secret.py':'eA=='})])
    with pytest.raises(HTTPException) as e:publish_task(data,data.tasks[0],'0'*40)
    assert e.value.status_code==422
    network.assert_not_called()


def test_product_deployment_waits_for_independent_git_gate(monkeypatch):
    from app import products,delivery
    updates=[]
    monkeypatch.setattr(products.tracker,'honor_remote_stop',lambda *a:None)
    monkeypatch.setattr(products.tracker,'request',lambda *a,**k:{'description':{'raw':'https://github.com/example/pull/7'}})
    monkeypatch.setattr(products,'update',lambda id,**kw:updates.append(kw))
    record={'id':uuid.uuid4(),'status':'PUBLISHED','git':{'pulls':[{'number':7,'commit':'a'*40,'task_id':1,'pull_request_url':'https://github.com/example/pull/7'}]}}
    story={'id':uuid.uuid4()}
    monkeypatch.setattr(delivery,'broker',lambda *a,**k:{'eligible':False})
    publish(story,record)
    assert not updates
    monkeypatch.setattr(delivery,'broker',lambda *a,**k:{'eligible':True})
    publish(story,record)
    assert updates==[{'status':'DEPLOY_READY'}]


def test_non_developer_cannot_overwrite_build_policy():
    data=Publish(run_id=uuid.uuid4(),story_id=uuid.uuid4(),revision=1,documentation_url='http://docs',product_package=True,
        tasks=[TaskBundle(task_id=1,epic_id=2,role='designer',files={'products/p-012345678abc/Dockerfile':'eA=='})])
    with pytest.raises(HTTPException) as e:publish_task(data,data.tasks[0],'0'*40)
    assert e.value.status_code==422


def test_product_readiness_uses_existing_broker_contract(monkeypatch):
    from app import products,delivery
    monkeypatch.setattr(delivery,'_broker',lambda path,payload=None:{'configured':False})
    updates=[]
    monkeypatch.setattr(products,'update',lambda id,**kw:updates.append(kw))
    products.publish({'id':uuid.uuid4()},{'id':uuid.uuid4(),'status':'READY_TO_PUBLISH'})
    assert updates==[{'status':'WAITING_GITHUB'}]
