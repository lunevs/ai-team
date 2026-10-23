import uuid
import pytest
from app import db,rework,workspaces
from app.schemas import StoryInput
from app.rework_contract import Triage

pytestmark=pytest.mark.integration


def test_repeated_failed_approach_escalates_once_and_replay_keeps_history(monkeypatch):
    row,_=db.create_story(StoryInput(text='Изолированный тест отсутствия прогресса исправлений',synthetic_only=True,delivery_mode='product'),'repair-'+str(uuid.uuid4()))
    sid=row['id'];created=[];statuses=[];calls=[]
    story=dict(row)|{'answers':'','pm':{'acceptance_criteria':['Результат равен 42']}}
    monkeypatch.setattr(rework,'generate',lambda *a,**kw:(calls.append(kw['result_role']) or Triage(findings=[{'index':0,'decision':'confirmed','reason':'Наблюдаемое значение не соответствует согласованному критерию.'}],next_action='Изменить технический подход и повторить независимую проверку.')))
    monkeypatch.setattr(rework.knowledge,'ensure_page',lambda *a:'http://example.invalid/doc')
    monkeypatch.setattr(rework.tracker,'ensure',lambda *a,**kw:(created.append(a) or 9999))
    monkeypatch.setattr(db,'external',lambda *a:{'external_id':1})
    monkeypatch.setattr(workspaces,'set_tracker_status',lambda *a:statuses.append(a))
    monkeypatch.setattr(rework,'sync_status',lambda *a:statuses.append(a))
    finding={'summary':'Неверный результат','criterion':'Результат равен 42','evidence':'Получено значение 41','severity':'major','verification':'Независимый тест ожидает 42'}
    try:
        for n in range(3):result=rework.assess(story,'own_test',n,{'app.py':'value=41'},[finding],{'passed':False},'application_developer',True)
        assert result['stagnation'] and result['escalation']=='TECHNICAL' and not result.get('questions')
        again=rework.assess(story,'own_test',2,{'app.py':'value=41'},[finding],{'passed':False},'application_developer',True)
        assert again==result and len(created)==2 and len(calls)==3
        saved=rework.history(sid,1)
        assert len(saved['defects'])==1 and len(saved['attempts'])==3
        assert saved['attempts'][-1]['decision']['next_action']
    finally:
        with db.connection() as c:
            for table in ['repair_history','product_defects','events','jobs']:c.execute('DELETE FROM '+table+' WHERE story_id=%s',(sid,))
            c.execute('DELETE FROM stories WHERE id=%s',(sid,))


def test_dismissed_github_feedback_waits_without_repeating_llm_or_bypassing_review(monkeypatch):
    from psycopg.types.json import Jsonb
    from app.rework_contract import feedback_key
    from app import delivery
    row,_=db.create_story(StoryInput(text='Проверка ожидания независимого решения после отклонённого замечания',synthetic_only=True,delivery_mode='product'),'review-wait-'+str(uuid.uuid4()))
    sid=row['id'];wid=uuid.uuid4();tid=uuid.uuid4()
    feedback={'origin':'github','findings':[{'summary':'Отсутствует Dockerfile','criterion':'Самостоятельная установка','evidence':'Файл добавит платформа','severity':'minor','verification':'Проверить пакет'}]}
    try:
        with db.connection() as c:
            c.execute('INSERT INTO product_workspaces(id,story_id,revision,definition) VALUES(%s,%s,1,%s)',(wid,sid,Jsonb({})))
            c.execute("INSERT INTO workspace_tasks(id,workspace_id,task_key,task_id,epic_id,role,specification,state,files,base_version,git,repair_context) VALUES(%s,%s,'build',9998,9997,'application_developer',%s,'REVIEW',%s,0,%s,%s)",
                (tid,wid,Jsonb({'key':'build','title':'Создать сервис','depends_on':[]}),Jsonb({'app.py':'value=42'}),Jsonb({'number':99,'commit':'a'*40}),Jsonb({'dismissed_feedback':feedback_key(feedback)})))
        story=dict(row)|{'plan':{'epics':[{'key':'one','tasks':[{'key':'build'}]}]}}
        monkeypatch.setattr(db,'external',lambda *a:{'external_id':9997})
        monkeypatch.setattr(delivery,'broker',lambda path,payload=None:{'eligible':False} if path=='/gate' else feedback)
        monkeypatch.setattr(rework,'generate',lambda *a,**kw:pytest.fail('No repeated LLM call for unchanged dismissed feedback'))
        workspaces.advance(story);workspaces.advance(story)
        with db.connection() as c:
            saved=c.execute('SELECT state,attempt FROM workspace_tasks WHERE id=%s',(tid,)).fetchone()
            assert saved=={'state':'REVIEW','attempt':0}
            assert c.execute('SELECT version FROM product_workspaces WHERE id=%s',(wid,)).fetchone()['version']==0
    finally:
        with db.connection() as c:
            c.execute('DELETE FROM workspace_tasks WHERE workspace_id=%s',(wid,));c.execute('DELETE FROM product_workspaces WHERE id=%s',(wid,))
            for table in ['events','jobs']:c.execute('DELETE FROM '+table+' WHERE story_id=%s',(sid,))
            c.execute('DELETE FROM stories WHERE id=%s',(sid,))


def test_repeated_file_action_preserves_code_and_routes_to_analysis(monkeypatch):
    from psycopg.types.json import Jsonb
    from app.workspace_contract import WorkspaceAction
    row,_=db.create_story(StoryInput(text='Проверка остановки повторяющегося чтения без прогресса',synthetic_only=True,delivery_mode='product'),'action-stall-'+str(uuid.uuid4()))
    sid=row['id'];wid=uuid.uuid4();tid=uuid.uuid4();calls=[]
    try:
        with db.connection() as c:
            c.execute('INSERT INTO product_workspaces(id,story_id,revision,definition) VALUES(%s,%s,1,%s)',(wid,sid,Jsonb({})))
            c.execute("INSERT INTO workspace_tasks(id,workspace_id,task_key,task_id,epic_id,role,specification,state,files,base_version) VALUES(%s,%s,'build',9998,9997,'application_developer',%s,'RUNNING',%s,0)",
                (tid,wid,Jsonb({'key':'build','title':'Создать сервис','depends_on':[],'acceptance_criteria':['Результат 42']}),Jsonb({'app.py':'value=41'})))
        story=dict(row)|{'plan':{'epics':[{'key':'one','tasks':[{'key':'build'}]}]}}
        monkeypatch.setattr(db,'external',lambda *a:{'external_id':9997})
        monkeypatch.setattr(workspaces,'generate',lambda *a,**kw:WorkspaceAction(operation='read',path='app.py',summary='Прочитать текущие исходники'))
        monkeypatch.setattr(rework,'task_failure',lambda *a,**kw:calls.append((a,kw)))
        for _ in range(3):workspaces.advance(story)
        with db.connection() as c:
            saved=c.execute('SELECT state,files,checks FROM workspace_tasks WHERE id=%s',(tid,)).fetchone()
            assert saved['state']=='CHECK_FAILED' and saved['files']=={'app.py':'value=41'}
            assert saved['checks']['error_type']=='NoProgress' and saved['checks']['tests_run']==0
        workspaces.advance(story)
        assert calls[0][0][3]['stalled_action'] and not calls[0][1]['actual_failure']
    finally:
        with db.connection() as c:
            c.execute('DELETE FROM workspace_tasks WHERE workspace_id=%s',(wid,));c.execute('DELETE FROM product_workspaces WHERE id=%s',(wid,))
            for table in ['events','jobs']:c.execute('DELETE FROM '+table+' WHERE story_id=%s',(sid,))
            c.execute('DELETE FROM stories WHERE id=%s',(sid,))
