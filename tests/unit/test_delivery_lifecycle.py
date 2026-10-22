from datetime import datetime,timezone
from types import SimpleNamespace
import pytest
from app import db,delivery,execution

def test_human_acceptance_release_review_then_done(monkeypatch):
    story={'id':'story','revision':1};updates=[];statuses=[];closed=[];pages=[]
    record={'id':'run','git':{'pulls':[{'epic_id':200,'task_id':201,'number':1,'commit':'a'*40}]},
            'business_accepted_by':'local-owner','business_accepted_at':datetime.now(timezone.utc)}
    monkeypatch.setattr(execution,'update',lambda id,**values:updates.append(values))
    monkeypatch.setattr(execution,'event',lambda *args:None)
    monkeypatch.setattr(delivery,'settings',SimpleNamespace(status_ids={'DONE':12}))
    monkeypatch.setattr(delivery,'tracker',SimpleNamespace(honor_remote_stop=lambda id:None,
        request=lambda path,method='GET',data=None: closed.append(path) if method=='PATCH' else {'lockVersion':1}))
    monkeypatch.setattr(delivery,'knowledge',SimpleNamespace(ensure_page=lambda *args:pages.append(args)))
    monkeypatch.setattr(delivery,'sync_status',lambda id,status:statuses.append(status))
    calls=[];eligible=[False];pending=[True]
    def broker(path,payload):
        calls.append(path)
        if path=='/gate':return {'eligible':eligible[0]}
        if path=='/release':return {'epic_id':200,'number':2,'commit':'b'*40,'pull_request_url':'https://github.com/lunevs/ai-team/pull/2'}
        if path=='/finalize-release':return {'pending':True} if pending[0] else {'epic_id':200,'tag':'epic-200-v0.4.0','commit':'c'*40}
        raise AssertionError(path)
    monkeypatch.setattr(delivery,'broker',broker)
    with pytest.raises(db.Conflict):delivery.complete_acceptance(story,record)
    assert not updates and not pages and not closed
    eligible[0]=True;delivery.complete_acceptance(story,record)
    assert updates[-1]['status']=='RELEASE_REVIEW' and '/release' in calls and not statuses
    record['git']=updates[-1]['git']
    delivery.finalize_delivery(story,record)
    assert updates[-1]['status']=='RELEASE_REVIEW' and not closed and not pages
    pending[0]=False;delivery.finalize_delivery(story,record)
    assert updates[-1]['status']=='DONE' and statuses==['DONE']
    assert closed==['/api/v3/work_packages/201','/api/v3/work_packages/200']
    assert 'epic-200-v0.4.0' in pages[0][-1]
