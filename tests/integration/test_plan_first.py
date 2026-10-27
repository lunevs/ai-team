"""A capability blocker must leave a complete assigned plan visible without approval."""
import uuid
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from app import db,workflow,feasibility
from app.schemas import StoryInput
from app.providers import MockProvider
pytestmark=pytest.mark.integration


@pytest.mark.parametrize('needs_questions',[False,True])
def test_product_plan_is_saved_and_published_even_when_platform_is_missing(monkeypatch,needs_questions):
    story,_=db.create_story(StoryInput(text='Техническая fixture: создать отчёт в отдельной BI системе',delivery_mode='product',synthetic_only=True),'plan-first-'+str(uuid.uuid4()))
    sid=story['id'];published=[]
    monkeypatch.setattr(workflow,'ensure_main',lambda *a:1)
    monkeypatch.setattr(workflow.tracker,'honor_remote_stop',lambda *a:None)
    monkeypatch.setattr(workflow,'sync_status',lambda id,status:db.set_status(id,status))
    def generate(id,role,schema,context,**kw):
        result=MockProvider().generate_structured(role,__import__('json').dumps(context),schema)[0]
        if role=='pm' and needs_questions:result.questions=['Какие сегменты нужны?']
        return result
    monkeypatch.setattr(workflow,'generate',generate)
    def assess(story,pm,plan):
        assert db.get_story(sid)['plan'] is not None
        return {'status':'NEEDS_PLATFORM','criteria':[],'documentation_url':'http://example.invalid/doc'}
    monkeypatch.setattr(feasibility,'assess',assess)
    monkeypatch.setattr(workflow,'publish_plan',lambda id,pm,plan,**kw:(published.append(plan) or 'http://example.invalid/plan'))
    try:
        workflow.build_graph(InMemorySaver()).invoke({'story_id':str(sid)},{'configurable':{'thread_id':str(sid)}})
        row=db.get_story(sid)
        assert row['status']=='BLOCKED' and row['plan'] and row['plan_digest'] and not row['approved']
        assert published and all(t['role'] for e in row['plan']['epics'] for t in e['tasks'])
    finally:
        with db.connection() as c:
            for table in ('events','jobs'):c.execute('DELETE FROM '+table+' WHERE story_id=%s',(sid,))
            c.execute('DELETE FROM stories WHERE id=%s',(sid,))
