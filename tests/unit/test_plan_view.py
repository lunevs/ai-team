from ui.plan_view import task_states,ROLES
from app.feasibility import explicit_capabilities


def test_real_bi_requirement_and_negative_capability_caveat_are_distinct():
    assert 'external_bi' in explicit_capabilities('Построй график в BI системе')
    assert 'pptx' not in explicit_capabilities('PPTX не поддержаны; отдельная BI-система не поставляется')
    assert 'external_bi' not in explicit_capabilities('PPTX не поддержаны; отдельная BI-система не поставляется')


def test_proposal_marks_unsupported_tasks_and_propagates_dependencies():
    s={'plan':{'epics':[{'tasks':[{'key':'data','role':'data_developer','depends_on':[]},{'key':'bi','role':'application_developer','depends_on':['data']},{'key':'release','role':'qa','depends_on':['bi']}]}]},
       'feasibility':{'status':'NEEDS_PLATFORM','gaps':[{'capability':'external_bi'}],'criteria':[{'task_keys':['bi'],'capability_ids':['external_bi']}]}}
    labels=task_states(s)
    assert 'не может' in labels['bi'] and 'Ждёт' in labels['release']
    assert not labels['data']
    assert ROLES['data_developer']=='Разработчик данных'


def test_only_customer_scope_change_allows_replacing_original_bi_request():
    from app.feasibility import evaluate,PlanAssessment,CriterionCheck,scope_exclusions
    story={'text':'Построй график в BI системе','answers':'Вместо BI системы достаточно встроенного графика'}
    plan={'epics':[{'tasks':[{'key':'chart','role':'application_developer'}]}]}
    assessment=PlanAssessment(criteria=[CriterionCheck(criterion_index=0,capability_ids=['embedded_dashboard'],task_keys=['chart'],verification='Проверить числовые данные графика')])
    assert scope_exclusions(story['answers'])=={'external_bi'}
    assert evaluate(story,{'acceptance_criteria':['Встроенный график содержит данные']},plan,assessment)['status']=='READY'
    story['answers']='Отдельная BI-система не поддерживается'
    assert not scope_exclusions(story['answers'])
    assert evaluate(story,{'acceptance_criteria':['Встроенный график содержит данные']},plan,assessment)['status']=='NEEDS_PLATFORM'
