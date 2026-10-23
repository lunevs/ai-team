import pytest
from pydantic import ValidationError
from app.rework_contract import Finding,SourceReview,Triage,validate_triage,fingerprint

F={'summary':'Неверный расчёт результата','criterion':'Результат равен 42','evidence':'Фактический результат 41','severity':'major','verification':'Независимый тест ожидает 42'}

def test_review_requires_evidence_and_consistent_decision():
    with pytest.raises(ValidationError):SourceReview(approved=True,findings=[F])
    with pytest.raises(ValidationError):SourceReview(approved=False,findings=[])
    assert SourceReview(approved=False,findings=[F]).findings[0].evidence


def test_triage_requires_every_finding_exactly_once_and_cannot_discard_failed_execution():
    dismissed=Triage(findings=[{'index':0,'decision':'dismissed','reason':'Файл добавляет платформа; исходники корректны.'}],next_action='Продолжить проверку состава итогового пакета.')
    assert validate_triage([Finding(**F)],dismissed)==dismissed
    with pytest.raises(ValueError):validate_triage([Finding(**F)],dismissed,actual_failure=True)
    with pytest.raises(ValueError):validate_triage([Finding(**F),Finding(**F)],dismissed)
    duplicated=dismissed.model_copy(update={'findings':dismissed.findings*2})
    with pytest.raises(ValueError):validate_triage([Finding(**F)],duplicated)


def test_repeated_issue_keeps_identity_when_only_source_evidence_changes():
    assert fingerprint(F)==fingerprint(F|{'evidence':'Тот же дефект после повторной проверки'})
    assert fingerprint(F)!=fingerprint(F|{'criterion':'Другой ожидаемый результат 43'})


def test_approval_on_an_older_commit_cannot_approve_repaired_head():
    from app.git_broker import evaluate_pull
    pr={'merged':True,'head':{'sha':'b'*40},'user':{'login':'service'}}
    reviews=[{'user':{'login':'human'},'state':'APPROVED','commit_id':'a'*40}]
    checks=[{'name':n,'status':'completed','conclusion':'success'} for n in ['application','validate','workspace-checks']]
    assert not evaluate_pull(pr,reviews,checks,{},'b'*40,workspace=True)['eligible']
    reviews[0]['commit_id']='b'*40
    assert evaluate_pull(pr,reviews,checks,{},'b'*40,workspace=True)['eligible']


def test_repair_commit_is_fast_forward_and_recovers_ambiguous_ref_update(monkeypatch,tmp_path):
    import base64,json,uuid
    from app import git_broker as broker
    from fastapi import HTTPException
    monkeypatch.setenv('GIT_WORKSPACE_ROOT',str(tmp_path))
    run=uuid.uuid4();story=uuid.uuid4();folder=tmp_path/run.hex/'9';folder.mkdir(parents=True)
    # Begin with a previously published task. It can only append from that exact open head.
    folder.joinpath('_commit.json').write_text(json.dumps({'sha':'a'*40,'digest':'old'}))
    refs={'feature/9':'a'*40,'release/8':'b'*40};updates=[];fail=[True];created=[]
    def github(path,method='GET',data=None,missing=False):
        route=path[len(broker.REPO):]
        if route.startswith('/git/ref/heads/'):
            return {'object':{'sha':refs[route[len('/git/ref/heads/'):]]}}
        if route.startswith('/pulls?'):return [{'state':'open','merged_at':None,'number':1,'html_url':'https://example.invalid/pr/1','head':{'sha':refs['feature/9']},'user':{'login':'service'}}]
        if route=='/git/commits/'+'a'*40:return {'tree':{'sha':'c'*40},'message':'old'}
        if route=='/git/blobs':return {'sha':'d'*40}
        if route=='/git/trees':return {'sha':'e'*40}
        if route=='/git/commits':created.append(data);return {'sha':'f'*40}
        if route=='/git/refs/heads/feature/9':
            updates.append(data);refs['feature/9']=data['sha']
            if fail[0]:fail[0]=False;raise HTTPException(503,'Ambiguous reply')
            return {}
        if route=='/git/commits/'+'f'*40:return {'message':created[0]['message']}
        raise AssertionError((route,method))
    monkeypatch.setattr(broker,'github',github)
    task=broker.TaskBundle(task_id=9,epic_id=8,role='qa',files={'qa.json':base64.b64encode(b'{"approved":true}').decode()})
    request=broker.Publish(run_id=run,story_id=story,revision=1,documentation_url='',tasks=[task],supersedes={'9':'a'*40})
    with pytest.raises(HTTPException):broker.publish_task(request,task,'b'*40)
    assert refs['feature/9']=='f'*40 and folder.joinpath('_pending.json').exists()
    result=broker.publish_task(request,task,'b'*40)
    assert result['commit']=='f'*40 and len(created)==len(updates)==1
    assert updates==[{'sha':'f'*40,'force':False}] and created[0]['parents']==['a'*40]
    assert not folder.joinpath('_pending.json').exists()
