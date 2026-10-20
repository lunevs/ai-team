"""Host-side real API e2e with Docker restart; runtime containers never get Docker socket."""
import concurrent.futures
import json
from pathlib import Path
import subprocess
import time
import urllib.request
import urllib.error
import uuid

values=dict(line.split('=',1) for line in Path('.env').read_text().splitlines() if '=' in line and not line.startswith('#'))
TOKEN=values['APP_API_TOKEN']
BASE='http://localhost:8000'
report={'backend':'mock','checks':[],'story_ids':[]}

def call(path,method='GET',data=None,key=None,expected=(200,201,202),authorized=True):
    headers={'Content-Type':'application/json'}
    if authorized:headers['Authorization']='Bearer '+TOKEN
    if key:headers['Idempotency-Key']=key
    request=urllib.request.Request(BASE+path,data=None if data is None else json.dumps(data).encode(),headers=headers,method=method)
    try:
        with urllib.request.urlopen(request,timeout=120) as response:status=response.status;body=json.load(response)
    except urllib.error.HTTPError as exc:status=exc.code;body=json.loads(exc.read())
    assert status in expected,(path,status,body)
    return body

def wait(id,target,timeout=180):
    until=time.monotonic()+timeout
    while time.monotonic()<until:
        story=call('/stories/'+id)
        if story['status'] in target:return story
        if story['status']=='FAILED':raise AssertionError(('workflow failed',story['error_type']))
        time.sleep(0.5)
    raise AssertionError(('timeout',id,story['status'],story.get('error_type')))

def create(text,**overrides):
    key='e2e-'+str(uuid.uuid4())
    payload={'text':text,'synthetic_only':True,**overrides}
    story=call('/stories','POST',payload,key)
    report['story_ids'].append(story['story_id'])
    return story,key,payload

call('/stories',authorized=False,expected=(401,))
report['checks'].append('unauthorized API denied')
story,key,payload=create('Создать витрину кредитного портфеля по месяцам на синтетических данных')
id=story['story_id']
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
    repeats=list(pool.map(lambda _:call('/stories','POST',payload,key),range(4)))
assert all(s['story_id']==id for s in repeats)
call('/stories','POST',{'text':'Другая синтетическая история с тем же ключом','synthetic_only':True},key,expected=(409,))
plan=wait(id,{'PLAN_REVIEW'})
assert plan['plan'] and plan['openproject_work_package_id']
assert plan['usage']['calls']==2 and not plan['approved']
assert len([x for x in plan['links'] if ':task:' in x['key'] and not x['key'].startswith('doc:')])==4
call('/stories/'+id+'/approve-plan','POST',{'revision':plan['revision'],'plan_digest':'0'*64},expected=(409,))
report['checks'].extend(['concurrent POST idempotency','conflicting key denied','PM + SA plan and task links','stale approval denied'])
# Crash and recover with an approval interrupt persisted in PostgreSQL.
subprocess.run(['docker','compose','restart','worker'],check=True,capture_output=True,timeout=50)
until=time.monotonic()+60
while time.monotonic()<until:
    try:
        call('/ready');break
    except (AssertionError,urllib.error.URLError):time.sleep(0.5)
assert call('/stories/'+id)['status']=='PLAN_REVIEW'
assert call('/stories/'+id)['usage']['calls']==2
approval={'revision':plan['revision'],'plan_digest':plan['plan_digest']}
call('/stories/'+id+'/approve-plan','POST',approval)
accepted=wait(id,{'IN_PROGRESS'})
call('/stories/'+id+'/approve-plan','POST',approval)
events=call('/stories/'+id+'/events')
assert sum(e['type']=='plan.approved' for e in events)==1
assert any(e['type']=='stage1.completed' and e['payload']['sql_executed'] is False for e in events)
report['checks'].extend(['checkpoint survives worker restart','approval persisted once','no SQL executed before or after stage 1 approval'])
clarify,_,_=create('[уточнить] Подготовить синтетическую витрину с неоднозначным срезом месяца')
blocked=wait(clarify['story_id'],{'BLOCKED'})
assert blocked['questions']
call('/stories/'+clarify['story_id']+'/resume','POST',{'answers':'Использовать последний календарный день месяца; пропуски отмечать отдельно.'})
new_plan=wait(clarify['story_id'],{'PLAN_REVIEW'})
assert new_plan['revision']==2
report['checks'].append('questions and resume create revision 2')
budget,_,_=create('Синтетический портфель: проверить ограничение числа LLM-вызовов',max_calls=1)
budget_blocked=wait(budget['story_id'],{'BLOCKED'})
assert budget_blocked['error_type']=='BudgetExceeded' and budget_blocked['usage']['calls']==1
report['checks'].append('budget blocks further LLM calls')
cancelled,_,_=create('Синтетический портфель: отменить историю до одобрения плана')
call('/stories/'+cancelled['story_id']+'/cancel','POST',{})
assert wait(cancelled['story_id'],{'CANCELLED'})['status']=='CANCELLED'
call('/stories/'+cancelled['story_id']+'/approve-plan','POST',{'revision':1,'plan_digest':'0'*64},expected=(409,))
report['checks'].append('cancellation prevents approval')
report['status']='passed'
Path('docs/stage1-check.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(report,ensure_ascii=False))
