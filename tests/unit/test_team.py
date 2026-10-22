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
