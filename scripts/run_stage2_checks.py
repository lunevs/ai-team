"""Run with mock worker, real infrastructure. Does not approve user's stories."""
# Reuse only HTTP helpers; do not execute stage1 checks on import.
exec(open('scripts/run_stage1_checks.py').read().split("call('/stories',authorized=False")[0])
report={'backend':'mock','checks':[],'story_ids':[]}
call('/metadata/synthetic',authorized=False,expected=(401,))
meta=call('/metadata/synthetic');assert meta['dataset']['seed']==4182
report['checks'].append('metadata authenticated and fixed synthetic schema')
story,_,_=create('Демонстрационная синтетическая витрина за январь–июнь 2026, RUB: DPD30>=30, DPD90>=90, NPL=DPD90/balance; конец календарного месяца, NULL при пропусках и нулевом знаменателе; доход за месяц.')
id=story['story_id'];path='/stories/'+id
call(path+'/analytics/start','POST',{},expected=(409,))
plan=wait(id,{'PLAN_REVIEW'})
call(path+'/approve-plan','POST',{'revision':1,'plan_digest':plan['plan_digest']})
wait(id,{'IN_PROGRESS'})
call(path+'/analytics/start','POST',{})
call(path+'/analytics/start','POST',{})
spec=wait(id,{'PLAN_REVIEW','BLOCKED'})
assert spec['analytics']['status']=='REVIEW',spec
assert spec['usage']['calls']==4 and spec['analytics']['documentation_url']
assert len(spec['analytics']['reference_rows'])==24
report['checks'].extend(['analytics requires approved plan','start idempotent','two structured analysts and published reference'])
call(path+'/approve-plan','POST',{'revision':1,'plan_digest':plan['plan_digest']},expected=(409,))
call(path+'/analytics/approve','POST',{'revision':1,'plan_digest':'0'*64},expected=(409,))
report['checks'].append('plan approval cannot approve analytics; stale digest rejected')
# Restart only the dedicated test worker, then retain its checkpoint and LLM count.
subprocess.run(['docker','restart','ai-team-stage2-mock'],check=True,capture_output=True,timeout=45)
assert call(path)['usage']['calls']==4
report['checks'].append('analytics checkpoint survives worker restart')
approval={'revision':1,'plan_digest':spec['analytics']['digest']}
call(path+'/analytics/approve','POST',approval)
call(path+'/analytics/approve','POST',approval)
events=call(path+'/events')
assert sum(e['type']=='analytics.approved' for e in events)==1
assert next(e for e in events if e['type']=='analytics.approved')['payload']['sql_executed'] is False
report['checks'].append('analytics approval idempotent; no SQL execution')
page=next(link for link in spec['links'] if link['key']=='doc:r1:analytics')
book_headers={'Authorization':'Token '+values['BOOKSTACK_RUNTIME_TOKEN_ID']+':'+values['BOOKSTACK_RUNTIME_TOKEN_SECRET']}
limit=time.monotonic()+60
while time.monotonic()<limit:
    req=urllib.request.Request('http://localhost:8082/api/pages/'+str(page['external_id']),headers=book_headers)
    with urllib.request.urlopen(req) as response:published=json.load(response)
    if 'analytics-approved:' in published['markdown']:break
    time.sleep(0.5)
assert 'analytics-approved:' in published['markdown']
report['checks'].append('BookStack approval state synchronized without LLM calls')
# Changed acceptance criteria revoke plan approval and require a fresh plan revision.
changed=call(path+'/analytics/revise','POST',{'answers':'Уточнение: отдельно отметить неполный month-end snapshot, паспорт метрик оставить прежним.'})
assert changed['revision']==2 and not changed['approved'] and changed['analytics'] is None
revised=wait(id,{'PLAN_REVIEW'})
assert revised['revision']==2
call(path+'/analytics/start','POST',{},expected=(409,))
report['checks'].append('changes create revision and require renewed plan approval')
call(path+'/cancel','POST',{})
call(path+'/analytics/approve','POST',approval,expected=(409,))
report['checks'].append('cancellation prevents old analytical approval')
# Create a separate budget test which has room for plan but no financial analysis.
budget,_,_=create('Синтетическая витрина для проверки бюджета аналитиков',max_calls=3)
bid=budget['story_id'];bpath='/stories/'+bid
bp=wait(bid,{'PLAN_REVIEW'})
call(bpath+'/approve-plan','POST',{'revision':1,'plan_digest':bp['plan_digest']})
wait(bid,{'IN_PROGRESS'});call(bpath+'/analytics/start','POST',{})
blocked=wait(bid,{'BLOCKED'})
assert blocked['error_type']=='BudgetExceeded' and blocked['usage']['calls']==3
report['checks'].append('shared budget blocks financial call')
report['status']='passed'
Path('docs/stage2-check.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(report,ensure_ascii=False))
