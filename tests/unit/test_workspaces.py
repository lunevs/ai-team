import hashlib
import io
import json
import zipfile
import uuid
import pytest
from pydantic import ValidationError
from app.workspace_contract import WorkspaceAction,apply_action,snapshot,inspect_snapshot,source_digest,file_hash
from app.planning import prepare_product_plan
from app.schemas import SAResult


def action(op,**kwargs):return WorkspaceAction(operation=op,summary='Результат текущей проверяемой задачи',**kwargs)


def test_incremental_tools_preserve_other_files_and_require_current_hash():
    initial={'app.py':'value=42\n','data/previous.py':'previous=True\n'}
    files,reply,_=apply_action(initial,'application_developer',action('read',path='app.py',limit=5))
    assert reply['content']=='value' and reply['next_offset']==5
    files,reply,_=apply_action(files,'application_developer',action('search',content='previous'))
    assert reply['matches'][0]['path']=='data/previous.py'
    edited,_,_=apply_action(files,'application_developer',action('edit',path='app.py',expected_sha256=file_hash(initial['app.py']),old_text='42',new_text='43'))
    assert edited['app.py']=='value=43\n' and edited['data/previous.py']==initial['data/previous.py']
    assert initial['app.py']=='value=42\n'
    with pytest.raises(ValueError):apply_action(edited,'application_developer',action('write',path='app.py',expected_sha256=file_hash(initial['app.py']),content='value=0'))


@pytest.mark.parametrize('path',['.env','../private.py','/tmp/code.py','Dockerfile','docs/tasks/5.md'])
def test_tools_cannot_change_host_build_secrets_or_platform_notes(path):
    with pytest.raises(ValueError):apply_action({},'application_developer',action('write',path=path,expected_sha256='new',content='x=1'))


def test_role_boundaries_and_syntax_are_enforced():
    with pytest.raises(ValueError):apply_action({},'designer',action('write',path='app.py',expected_sha256='new',content='x=1'))
    with pytest.raises(SyntaxError):apply_action({},'application_developer',action('write',path='app.py',expected_sha256='new',content='import ('))
    files,_,_=apply_action({},'qa',action('write',path='tests/test_independent.py',expected_sha256='new',content='import unittest\n'))
    assert 'tests/test_independent.py' in files


def test_snapshot_identity_and_tampering():
    files={'data/value.py':'value=42\n'};metadata={'task_id':5,'workspace_id':str(uuid.uuid4())}
    content,manifest=snapshot(files,metadata)
    assert snapshot(files,metadata)[0]==content
    parsed,restored=inspect_snapshot(content)
    assert restored==files and parsed['source_digest']==source_digest(files)
    z=zipfile.ZipFile(io.BytesIO(content));output=io.BytesIO()
    with zipfile.ZipFile(output,'w') as other:
        for path in z.namelist():other.writestr(path,b'value=99\n' if path=='source/data/value.py' else z.read(path))
    with pytest.raises(ValueError):inspect_snapshot(output.getvalue())


def test_dynamic_plan_keeps_two_epics_and_adds_only_platform_packaging():
    raw={'architecture':'Проверяемая архитектура продукта с двумя последовательными эпиками','epics':[
        {'key':'base','title':'Базовый продукт','tasks':[{'key':'build','title':'Создать базовый сервис','role':'application_developer','acceptance_criteria':['База работает']}]},
        {'key':'extend','title':'Расширенный продукт','tasks':[{'key':'extend','title':'Доработать сервис','role':'application_developer','acceptance_criteria':['Расширение работает'],'depends_on':['build']}]}]}
    result=prepare_product_plan(SAResult.model_validate(raw))
    assert len(result.epics)==2 and result.development_mode=='workspace'
    assert result.epics[-1].tasks[-1].kind=='package'
    assert result.epics[-1].tasks[-1].depends_on==['build','extend']
    raw['epics'][0]['tasks'][0]['depends_on']=['extend'];raw['epics'][1]['tasks'][0]['depends_on']=[]
    with pytest.raises(ValueError):prepare_product_plan(SAResult.model_validate(raw))


def test_workspace_git_profile_demands_its_own_ci_check():
    from app.git_broker import evaluate_pull
    pr={'state':'closed','merged':True,'head':{'sha':'a'*40},'user':{'login':'author'}}
    reviews=[{'user':{'login':'reviewer'},'state':'APPROVED','commit_id':'a'*40,'submitted_at':'2026-10-23T00:00:00Z'}]
    checks=[{'name':name,'status':'completed','conclusion':'success','head_sha':'a'*40} for name in ['validate','application']]
    result=evaluate_pull(pr,reviews,checks,{},'a'*40,workspace=True)
    assert not result['eligible']
    checks.append({'name':'workspace-checks','status':'completed','conclusion':'success','head_sha':'a'*40})
    assert evaluate_pull(pr,reviews,checks,{},'a'*40,workspace=True)['eligible']


def test_broker_rejects_snapshot_from_another_task_before_github(monkeypatch):
    import base64
    from app import git_broker as broker
    from fastapi import HTTPException
    story=uuid.uuid4();run=uuid.uuid4();root='workspaces/p-'+story.hex[:12]+'/'
    content,_=snapshot({'app.py':'value=42\n'},{'story_id':str(story),'revision':1,'task_id':5,'epic_id':8,'role':'application_developer','task_run_id':str(run)})
    with zipfile.ZipFile(io.BytesIO(content)) as z:files={root+p:base64.b64encode(z.read(p)).decode() for p in z.namelist()}
    task=broker.TaskBundle(task_id=6,epic_id=8,role='application_developer',files=files)
    request=broker.Publish(run_id=run,story_id=story,revision=1,documentation_url='',tasks=[task],workspace_task=True)
    monkeypatch.setattr(broker,'github',lambda *a,**kw:pytest.fail('Invalid snapshot must never reach GitHub'))
    with pytest.raises(HTTPException) as error:broker.publish_task(request,task,'a'*40)
    assert error.value.status_code==422


def test_next_epic_requires_verified_previous_release(monkeypatch):
    from app import git_broker as broker
    from fastapi import HTTPException
    monkeypatch.setenv('GIT_BROKER_API_TOKEN','test')
    monkeypatch.setattr(broker,'ready',lambda *a:{'workspace_base_ready':True,'workspace_base_sha':'a'*40})
    monkeypatch.setattr(broker,'gate',lambda *a:{'eligible':False})
    monkeypatch.setattr(broker,'github',lambda *a,**kw:pytest.fail('Unverified release must never become a base'))
    task=broker.TaskBundle(task_id=6,epic_id=8,role='application_developer',files={'x':'eA=='})
    request=broker.Publish(run_id=uuid.uuid4(),story_id=uuid.uuid4(),revision=1,documentation_url='',tasks=[task],workspace_task=True,
        previous_release={'number':1,'epic_id':7,'commit':'b'*40})
    with pytest.raises(HTTPException) as error:broker.publish(request,'Bearer test')
    assert error.value.status_code==409


def test_check_resumes_agent_and_finish_requests_publication():
    files={'app.py':'value=42\n'}
    assert apply_action(files,'application_developer',action('check'))[2]=='continue'
    assert apply_action(files,'application_developer',action('finish'))[2]=='finish'
