import base64
import uuid
import pytest
from fastapi import HTTPException
from app import git_broker as broker

def test_partial_git_publication_reconciles_same_branch_and_pr(monkeypatch,tmp_path):
    monkeypatch.setenv('GIT_WORKSPACE_ROOT',str(tmp_path))
    refs={'release/188':'a'*40};commits={'a'*40:{'tree':{'sha':'b'*40},'message':'base'}};pulls=[];created=[]
    def github(path,method='GET',data=None,missing=False):
        route=path[len(broker.REPO):]
        if route.startswith('/git/ref/heads/'):
            sha=refs.get(route[len('/git/ref/heads/'):]);return {'object':{'sha':sha}} if sha else None
        if route=='/git/refs':refs[data['ref'][11:]]=data['sha'];return {'object':{'sha':data['sha']}}
        if route.startswith('/git/commits/') and method=='GET':return commits[route.rsplit('/',1)[-1]]
        if route=='/git/blobs':return {'sha':'c'*40}
        if route=='/git/trees':return {'sha':'d'*40}
        if route=='/git/commits':
            created.append(data);commits['e'*40]={'tree':{'sha':data['tree']},'message':data['message']};return {'sha':'e'*40}
        if route.startswith('/pulls?'):return pulls
        if route=='/pulls':
            pulls.append({'number':1,'html_url':'https://github.com/lunevs/ai-team/pull/1','user':{'login':'service'},'state':'open','head':{'sha':'e'*40}})
            return pulls[0]
        raise AssertionError((route,method))
    monkeypatch.setattr(broker,'github',github)
    task=broker.TaskBundle(task_id=201,epic_id=200,role='data_developer',files={'query.sql':base64.b64encode(b'SELECT 1').decode()})
    request=broker.Publish(run_id=uuid.uuid4(),story_id=uuid.uuid4(),revision=1,documentation_url='http://localhost:8082',tasks=[task])
    first=broker.publish_task(request,task,'a'*40)
    second=broker.publish_task(request,task,'a'*40)
    assert first==second and len(created)==len(pulls)==1
    assert refs['feature/201']=='e'*40 and refs['release/200']=='a'*40
    del refs['feature/201']
    pulls[0].update(state='closed',merged_at='2026-10-22')
    assert broker.publish_task(request,task,'a'*40)==first
    assert len(created)==1 and 'feature/201' not in refs
    assert (tmp_path/request.run_id.hex/'201'/'query.sql').read_bytes()==b'SELECT 1'
    refs['feature/201']='f'*40
    commits['f'*40]=commits['e'*40] # amend --no-edit preserves marker, but must invalidate SHA.
    with pytest.raises(HTTPException) as amended:broker.publish_task(request,task,'a'*40)
    assert amended.value.status_code==409
    refs['feature/201']='e'*40
    task.files['query.sql']=base64.b64encode(b'SELECT 2').decode()
    with pytest.raises(HTTPException) as error:broker.publish_task(request,task,'a'*40)
    assert error.value.status_code==409 and refs['feature/201']=='e'*40

def test_git_bundle_rejects_path_traversal_before_any_github_call(monkeypatch):
    def forbidden(*args,**kwargs):raise AssertionError('GitHub should not be called')
    monkeypatch.setattr(broker,'github',forbidden)
    task=broker.TaskBundle(task_id=1,epic_id=2,role='qa',files={'../secret':base64.b64encode(b'x').decode()})
    request=broker.Publish(run_id=uuid.uuid4(),story_id=uuid.uuid4(),revision=1,documentation_url='',tasks=[task])
    with pytest.raises(HTTPException) as error:broker.publish_task(request,task,'a'*40)
    assert error.value.status_code==422

def test_release_tags_only_verified_merge_commit(monkeypatch):
    monkeypatch.setenv('GIT_BROKER_API_TOKEN','internal-test')
    requests=[]
    pr={'base':{'ref':'main'},'head':{'ref':'release/200'},'merge_commit_sha':'f'*40,
        'html_url':'https://github.com/lunevs/ai-team/pull/10'}
    def github(path,method='GET',data=None,missing=False):
        requests.append((path,method,data))
        if '/pulls/' in path:return pr
        if '/git/ref/tags/' in path:return None
        if path.endswith('/git/refs'):return {}
        raise AssertionError(path)
    monkeypatch.setattr(broker,'github',github)
    monkeypatch.setattr(broker,'gate',lambda *a:{'eligible':False})
    request=broker.Finalize(epic_id=200,number=10,commit='e'*40)
    with pytest.raises(HTTPException):broker.finalize_release(request,'Bearer internal-test')
    assert not any(method=='POST' for _,method,_ in requests)
    monkeypatch.setattr(broker,'gate',lambda *a:{'eligible':True})
    result=broker.finalize_release(request,'Bearer internal-test')
    assert result['tag']=='epic-200-v0.4.0'
    assert requests[-1][2]=={'ref':'refs/tags/epic-200-v0.4.0','sha':'f'*40}

def test_runtime_requires_merged_implementation_base(monkeypatch):
    monkeypatch.setenv('GIT_BROKER_API_TOKEN','internal-test')
    monkeypatch.setenv('GITHUB_RUNTIME_TOKEN','github-test')
    contract=[None]
    product_contract=[None]
    def github(path,method='GET',data=None,missing=False):
        assert method=='GET'
        if '/git/ref/heads/' in path:return {'object':{'sha':'a'*40}}
        if '/contents/app/runtime_contract.json' in path:return contract[0]
        if '/contents/app/product_runtime_contract.json' in path:return product_contract[0]
        if '/contents/app/workspace_runtime_contract.json' in path:return None
        raise AssertionError(path)
    monkeypatch.setattr(broker,'github',github)
    assert broker.ready('Bearer internal-test')['base_ready'] is False
    task=broker.TaskBundle(task_id=201,epic_id=200,role='qa',files={'qa.json':'e30='})
    request=broker.Publish(run_id=uuid.uuid4(),story_id=uuid.uuid4(),revision=1,documentation_url='',tasks=[task])
    with pytest.raises(HTTPException) as error:broker.publish(request,'Bearer internal-test')
    assert error.value.status_code==409
    contract[0]={'content':base64.b64encode(b'{"version":4}').decode()}
    assert broker.ready('Bearer internal-test')['base_ready'] is True
    assert broker.ready('Bearer internal-test')['product_base_ready'] is False
    product_contract[0]={'content':base64.b64encode(b'{"version":1,"profile":"python-locked-v1","required_check":"product-checks"}').decode()}
    assert broker.ready('Bearer internal-test')['product_base_ready'] is True
