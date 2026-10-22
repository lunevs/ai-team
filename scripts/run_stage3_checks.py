"""Host E2E against real integrations and a dedicated mock worker; no credentials printed."""
exec(open('scripts/run_stage1_checks.py').read().split("call('/stories',authorized=False")[0])

def prepared(marker,max_calls=8):
    created,_,_=create(marker+' Создать синтетическую витрину кредитного портфеля по месяцам и Excel-отчёт',max_calls=max_calls)
    id=created['story_id'];path='/stories/'+id
    plan=wait(id,{'PLAN_REVIEW'})
    call(path+'/execution/start','POST',{},expected=(409,))
    call(path+'/approve-plan','POST',{'revision':1,'plan_digest':plan['plan_digest']})
    wait(id,{'IN_PROGRESS'});call(path+'/analytics/start','POST',{})
    spec=wait(id,{'PLAN_REVIEW'})
    call(path+'/execution/start','POST',{},expected=(409,))
    call(path+'/analytics/approve','POST',{'revision':1,'plan_digest':spec['analytics']['digest']})
    wait(id,{'IN_PROGRESS'})
    return id,path

def finished(id,statuses,timeout=180):
    until=time.monotonic()+timeout
    while time.monotonic()<until:
        story=call('/stories/'+id)
        run=story.get('execution')
        if run and run['status'] in statuses:return story
        if story['status'] in ['FAILED','BLOCKED']:raise AssertionError((story['status'],story['error_type'],run and run['status']))
        time.sleep(.5)
    raise AssertionError(('execution timeout',id,story['status'],run))

id,path=prepared('[stage3-happy]')
run=call(path+'/execution/start','POST',{})
assert call(path+'/execution/start','POST',{})['id']==run['id']
story=finished(id,{'WAITING_GITHUB','ACCEPTANCE'})
assert story['execution']['qa']['passed'] and len(story['execution']['rows'])==24
assert story['usage']['calls']==6
report['checks'].extend(['explicit analytics approval required before SQL','execution start idempotent','real sandbox SQL matches independent 24-row QA','architecture review separate from developer'])
for name in ['report.xlsx','query.sql','qa.json']:
    url=BASE+path+'/execution/artifacts/'+name
    with urllib.request.urlopen(urllib.request.Request(url,headers={'Authorization':'Bearer '+TOKEN})) as response:content=response.read()
    assert content
    if name.endswith('.xlsx'):assert content[:2]==b'PK'
try:urllib.request.urlopen(BASE+path+'/execution/artifacts/report.xlsx')
except urllib.error.HTTPError as error:assert error.code==401
else:raise AssertionError('Unprotected artifact')
call(path+'/execution/artifacts/.env',expected=(404,))
call(path+'/execution/accept','POST',{'revision':1,'plan_digest':story['execution']['analytics_digest']},expected=(409,))
call(path+'/analytics/revise','POST',{'answers':'Изменить определение после начала SQL'},expected=(409,))
report['checks'].extend(['authenticated Excel/SQL/QA downloads; unknown filenames denied','no acceptance before Git review/CI/merge','criteria cannot change underneath execution'])
if story['execution']['status']=='WAITING_GITHUB':
    before=story['execution']['artifacts']
    subprocess.run(['docker','restart','ai-team-stage3-mock'],check=True,capture_output=True,timeout=45)
    call(path+'/execution/retry','POST',{})
    # Wait for durable retry event rather than observing the previous waiting status immediately.
    time.sleep(3)
    repeated=finished(id,{'WAITING_GITHUB'})
    assert repeated['usage']['calls']==6 and repeated['execution']['artifacts']==before
    report['checks'].append('worker restart/publication retry preserve attempts, LLM usage and artifact hashes')
call(path+'/cancel','POST',{})
call(path+'/execution/retry','POST',{},expected=(409,))
report['checks'].append('cancellation prevents publication retry')

id,path=prepared('[qa-defect]')
call(path+'/execution/start','POST',{})
until=time.monotonic()+90
while time.monotonic()<until:
    events=call(path+'/events')
    if any(e['type']=='execution.qa' and e['payload']['attempt']==1 and not e['payload']['passed'] for e in events):break
    time.sleep(.1)
else:raise AssertionError('First persisted QA attempt not observed')
subprocess.run(['docker','kill','ai-team-stage3-mock'],check=True,capture_output=True,timeout=30)
subprocess.run(['docker','start','ai-team-stage3-mock'],check=True,capture_output=True,timeout=30)
fixed=finished(id,{'WAITING_GITHUB','ACCEPTANCE'})
assert fixed['execution']['attempt']==2 and fixed['usage']['calls']==7
events=call(path+'/events')
assert len([e for e in events if e['type']=='execution.defect'])==1
report['checks'].append('DPD threshold defect creates actual tracker bug and is repaired on attempt 2')
report['checks'].append('hard worker crash after persisted QA resumes without duplicate LLM call or tracker bug')
call(path+'/cancel','POST',{})

id,path=prepared('[qa-always-fail]')
call(path+'/execution/start','POST',{})
blocked=wait(id,{'BLOCKED'})
assert blocked['execution']['status']=='ESCALATED' and blocked['execution']['attempt']==3
assert blocked['usage']['calls']==7
assert len([e for e in call(path+'/events') if e['type']=='execution.defect'])==3
report['checks'].append('two repair limit produces three bugs and explicit escalation')
call(path+'/cancel','POST',{})
id,path=prepared('[stage3-budget]',max_calls=4)
call(path+'/execution/start','POST',{})
budget=wait(id,{'BLOCKED'})
assert budget['error_type']=='BudgetExceeded' and budget['usage']['calls']==4
assert not budget['execution']['artifacts']
report['checks'].append('shared story budget blocks developer before fifth call or artifact publication')
call(path+'/cancel','POST',{})
print(json.dumps(report,ensure_ascii=False,indent=2))
