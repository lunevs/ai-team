from app.team import project_team


def projection(remote=None, available=True, status='PLAN_REVIEW',approved=False):
    story={'revision':2,'status':status,'approved':approved,'plan':{'epics':[{'tasks':[
        {'key':'a','title':'Подготовить витрину','role':'data_developer'}]}]}}
    return project_team(story,[{'role':'pm','revision':1},{'role':'pm','revision':2}],{},[],
        [{'key':'r2:task:a','external_id':12,'url':'http://localhost:8080/work_packages/12'}],
        {12:remote} if remote else {},available,False)


def test_counts_and_history_do_not_treat_a_plan_as_execution():
    data=projection({'_embedded':{'status':{'name':'New','isClosed':False}}})
    pm=data['agents'][0]
    dev=next(x for x in data['agents'] if x['role']=='data_developer')
    assert len(data['agents'])==7
    assert pm['completed']==1 and len(pm['history'])==2
    assert dev['completed']==0 and dev['remaining']==1
    assert dev['current']=='Ожидает одобрения плана'
    analytics=projection({'_embedded':{'status':{'name':'New','isClosed':False}}},approved=True)
    assert next(a for a in analytics['agents'] if a['role']=='data_developer')['current']=='Ожидает одобрения спецификации'


def test_tracker_closure_and_outage():
    data=projection({'_embedded':{'status':{'name':'Closed','isClosed':True}},'updatedAt':'2026-10-20'})
    dev=next(x for x in data['agents'] if x['role']=='data_developer')
    assert dev['completed']==1 and dev['remaining']==0 and len(dev['history'])==1
    data=projection(available=False)
    assert not data['tracker_available']
    assert next(x for x in data['agents'] if x['role']=='data_developer')['completed']==0


def test_cancelled_story_never_claims_active_work():
    data=projection({'_embedded':{'status':{'name':'Working','isInProgress':True}}},status='CANCELLED')
    assert all(x['current']=='История отменена' for x in data['agents'])


def test_product_activity_and_history_use_product_roles():
    story={'revision':1,'status':'IN_PROGRESS','approved':True,'delivery_mode':'product','plan':None,'product':{'status':'DEVELOPING'}}
    result=project_team(story,[{'role':'product_solution','revision':1}],{},
        [{'role':'application_developer','revision':1,'state':'reserved'}],[],{},True,True)
    app=next(a for a in result['agents'] if a['role']=='application_developer')
    assert app['current']=='Создаёт приложение'
    assert app['history'][0]['title']=='Подготовил исходники и тесты'
    assert app['completed']==0 # source preparation does not close the tracker task


def test_blocked_explains_questions_and_budget_separately():
    base={'revision':1,'status':'BLOCKED','questions':['Which forms?'],'plan':None}
    cards=project_team(base,[],{},[],[],{},True,False)['agents']
    assert all('ответов заказчика' in c['current'] for c in cards)
    base['error_type']='BudgetExceeded'
    cards=project_team(base,[],{},[],[],{},True,False)['agents']
    assert all('лимита' in c['current'] for c in cards)


def test_queue_explains_dependency_then_next_task_and_worker_wait():
    story={'revision':1,'status':'IN_PROGRESS','plan':{'epics':[{'tasks':[
        {'key':'source','title':'Спроектировать источники','role':'system_analyst'},
        {'key':'build','title':'Разработать загрузку','role':'data_developer','depends_on':['source']}]}]}}
    links=[{'key':'r1:task:source','external_id':1,'url':'/1'}]
    def current(packages,worker):
        cards=project_team(story,[],{},[],links,packages,True,worker)['agents']
        return next(c['current'] for c in cards if c['role']=='data_developer')
    assert 'Ждёт завершения: Спроектировать источники' in current({},True)
    closed={1:{'_embedded':{'status':{'isClosed':True}}}}
    assert current(closed,True)=='Готовится приступить к задаче: Разработать загрузку'
    assert 'Ждёт возобновления работы команды' in current(closed,False)


def test_product_wait_is_personal_and_based_on_current_revision_results():
    story={'revision':2,'status':'IN_PROGRESS','delivery_mode':'product','product':{'status':'CHECK_PENDING'}}
    results=[{'role':role,'revision':2} for role in ['pm','system_analyst','product_financial_analyst','product_designer','product_data_developer','product_solution']]
    results.append({'role':'product_qa','revision':1})
    agents={a['role']:a for a in project_team(story,results,{},[],[],{},True,False)['agents']}
    assert agents['pm']['current']=='Цель и требования подготовлены'
    assert agents['financial_analyst']['current']=='Описание показателей подготовлено'
    assert agents['data_developer']['current']=='Схема хранения данных подготовлена'
    assert agents['application_developer']['current']=='Приложение подготовлено; ждёт проверки'
    assert agents['qa']['current']=='Ждёт запуска проверки приложения'
    assert agents['system_analyst']['current']=='План подготовлен; ждёт результатов проверки'
    assert len({a['current'] for a in agents.values()})==7
    assert agents['application_developer']['completed']==0
    story['status']='CANCELLED'
    assert all(a['current']=='История отменена' for a in project_team(story,results,{},[],[],{},True,False)['agents'])


def test_active_product_role_takes_priority_over_prepared_result():
    from app.team import product_activity
    assert product_activity('application_developer','CHECK_PENDING',{'product_solution'},True,'')=='Создаёт приложение'


def test_product_escalation_keeps_personal_results_and_explains_rework():
    story={'revision':1,'status':'BLOCKED','delivery_mode':'product','product':{'status':'ESCALATED'},'questions':[]}
    results=[{'role':role,'revision':1} for role in ['pm','product_financial_analyst','product_qa','product_review']]
    cards={a['role']:a for a in project_team(story,results,{},[],[],{},True,False)['agents']}
    assert cards['pm']['current']=='Цель и требования подготовлены'
    assert cards['financial_analyst']['current']=='Описание показателей подготовлено'
    assert cards['application_developer']['current']=='Приложение требует доработки после проверки'
    assert cards['qa']['current']=='Проверка завершена; ждёт исправлений'


def test_feasibility_tasks_are_visible_on_analyst_card():
    story={'revision':1,'status':'BLOCKED','delivery_mode':'product','feasibility':{
        'status':'NEEDS_PLATFORM','gaps':[{'capability':'airflow','title':'Загрузки Airflow'}],
        'tasks':[{'capability':'airflow','task_id':123,'url':'/123'}]}}
    remote={123:{'_embedded':{'status':{'name':'New','isClosed':False}}}}
    cards={a['role']:a for a in project_team(story,[],{},[],[],remote,True,False)['agents']}
    assert cards['system_analyst']['remaining']==1
    assert cards['system_analyst']['current']=='Нужно подготовить: Загрузки Airflow'
    assert cards['system_analyst']['next_task']=='Загрузки Airflow'
    assert cards['pm']['current']=='Ждёт подготовки среды для планирования'
    assert cards['qa']['current']=='Ждёт выполнимого плана'
