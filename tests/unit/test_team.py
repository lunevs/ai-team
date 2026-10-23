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
    assert app['current']=='Пишет приложение и тесты'
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
    assert 'Ждёт запуска worker' in current(closed,False)
