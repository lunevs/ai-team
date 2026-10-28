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


def test_followup_preserves_prior_customer_scope_in_new_revision():
    from app.api import resume
    from app.schemas import Resume
    from app.feasibility import scope_exclusions
    sid=db.create_story(StoryInput(text='История сохранения уточнений для Excel-отчёта вместо BI системы',delivery_mode='product',synthetic_only=True),'scope-history-'+str(uuid.uuid4()))[0]['id']
    try:
        with db.connection() as c:c.execute("UPDATE stories SET status='BLOCKED',answers=%s WHERE id=%s",('замени BI-систему в результате на excel-файл с рядом данных',sid))
        response=resume(sid,Resume(answers='Сегменты: физические и юридические лица'),'fixture-customer')
        saved=db.get_story(sid)
        assert saved['revision']==2 and not saved['approved'] and saved['plan'] is None
        assert 'excel-файл' in saved['answers'] and 'юридические лица' in saved['answers']
        assert response['answers']==saved['answers']
        assert scope_exclusions(saved['answers'])=={'external_bi'}
    finally:
        with db.connection() as c:
            for table in ('events','jobs'):c.execute('DELETE FROM '+table+' WHERE story_id=%s',(sid,))
            c.execute('DELETE FROM stories WHERE id=%s',(sid,))
