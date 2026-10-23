import pytest
from app import db
from app.capabilities import registry
from app.feasibility import PlanAssessment,CriterionCheck,evaluate,require_ready,plan_digest


def inputs():
    story={'id':'fixture','revision':1,'text':'Небольшое приложение возвращает локальное контрольное значение','answers':''}
    pm={'acceptance_criteria':['Приложение возвращает контрольное значение']}
    plan={'epics':[{'key':'one','tasks':[{'key':'build','role':'application_developer'}]}]}
    assessment=PlanAssessment(criteria=[CriterionCheck(criterion_index=0,capability_ids=['python_service'],task_keys=['build'],verification='Сравнить HTTP результат с независимым ожидаемым значением')])
    return story,pm,plan,assessment


def test_supported_criterion_has_task_owner_and_verification():
    story,pm,plan,a=inputs();report=evaluate(story,pm,plan,a)
    assert report['status']=='READY'
    assert report['criteria'][0]['roles']==['application_developer']
    assert report['plan_digest']==plan_digest(pm,plan)


def test_model_cannot_hide_explicit_capabilities_in_customer_answers():
    story,pm,plan,a=inputs();story['answers']='Airflow ежедневно загружает данные с https://example.invalid/reports'
    report=evaluate(story,pm,plan,a)
    assert report['status']=='NEEDS_PLATFORM'
    assert {'airflow','scheduled_jobs','external_http'}<={g['capability'] for g in report['gaps']}
    assert all(g['owner']=='system_analyst' for g in report['gaps'])


@pytest.mark.parametrize('mutation',['missing','duplicate','unknown_task','unknown_capability','unknown_requirement'])
def test_incomplete_or_unknown_assessment_never_allows_approval(mutation):
    story,pm,plan,a=inputs()
    if mutation=='missing':pm['acceptance_criteria'].append('Второй критерий')
    elif mutation=='duplicate':a.criteria.append(a.criteria[0])
    elif mutation=='unknown_task':a.criteria[0].task_keys=['invented']
    elif mutation=='unknown_capability':a.criteria[0].capability_ids=['model_claims_magic']
    else:a.unknown_requirements=['Требуется проверить техническую предпосылку']
    assert evaluate(story,pm,plan,a)['status']!='READY'


def test_two_epics_cannot_silently_pass_current_profile():
    story,pm,plan,a=inputs();plan['epics'].append({'key':'two','tasks':[]})
    assert evaluate(story,pm,plan,a)['status']=='READY'
    plan['epics']*=7
    assert evaluate(story,pm,plan,a)['status']=='NEEDS_ANALYSIS'


def test_approval_requires_exact_plan_and_registry_version(monkeypatch):
    from app import feasibility
    story,pm,plan,a=inputs();report=evaluate(story,pm,plan,a)
    story['plan_digest']=report['plan_digest']
    monkeypatch.setattr(feasibility,'get',lambda *args:report)
    require_ready(story)
    report['registry_digest']='old'
    with pytest.raises(db.Conflict):require_ready(story)
    report['registry_digest']=registry()['digest'];story['plan_digest']='changed'
    with pytest.raises(db.Conflict):require_ready(story)
    monkeypatch.setattr(feasibility,'get',lambda *args:None)
    with pytest.raises(db.Conflict):require_ready(story)


def test_explicit_exclusions_are_not_required_capabilities():
    from app.feasibility import explicit_capabilities
    assert 'airflow' not in explicit_capabilities('Небольшой сервис без Airflow')


def test_technical_choices_do_not_block_supported_plan():
    story,pm,plan,a=inputs()
    a.technical_decisions=['Аналитик выбирает JSON status=ok для /health в задаче build']
    report=evaluate(story,pm,plan,a)
    assert report['status']=='READY'
    assert report['technical_decisions']==a.technical_decisions


def test_explicit_missing_tools_are_caught_before_model_or_business_questions(monkeypatch):
    from app import feasibility
    story,_,_,_=inputs();story['answers']='Нужен Airflow для ежедневного задания'
    monkeypatch.setattr(feasibility,'get',lambda *args:None)
    saved=[]
    monkeypatch.setattr(feasibility,'save',lambda s,r:saved.append(r))
    monkeypatch.setattr(feasibility,'publish',lambda s,r:r|{'tasks':[{'task_id':1}]})
    report=feasibility.precheck(story)
    assert report['status']=='NEEDS_PLATFORM'
    assert report['source']=='explicit_requirement_precheck'
    assert 'airflow' in {gap['capability'] for gap in report['gaps']}
    assert len(saved)==2
