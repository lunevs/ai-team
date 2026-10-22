"""Opt-in host E2E: real services, isolated mock worker, no paid LLM or Git writes.
Run only in the local development installation; briefly pauses its production worker.
"""
exec(open('scripts/run_stage1_checks.py').read().split("call('/stories',authorized=False")[0])
import urllib.parse
import threading
import http.client
import re
# Identity is checked without credentials before the Grafana helper authenticates.
exec(open('scripts/check_observability_stack.py').read().split("report={'grafana_health'")[0])
report={'backend':'mock','checks':[],'story_ids':[]}
resources={'samples':[],'limits_mib':1216,'notes':'Docker working-set RAM. Worker paused for profile-off/idle; two test containers during workload; normal worker restored in final. Installation totals include tracker/knowledge databases. No disk-quota claim.'}
stop_sampling=threading.Event()
def docker(*args):
    return subprocess.run(['docker',*args],check=True,capture_output=True,text=True,timeout=120).stdout

def sample(label):
    rows=[json.loads(line) for line in docker('stats','--no-stream','--format','{{json .}}').splitlines()]
    rows=[{'name':r['Name'],'memory':r['MemUsage'],'cpu':r['CPUPerc']} for r in rows if r['Name'].startswith('ai-team-')]
    def mib(memory):
        number,unit=re.match(r'([\d.]+)(\w+)',memory.split(' / ')[0]).groups()
        return float(number)*{'MiB':1,'GiB':1024,'KiB':1/1024,'B':1/1048576}[unit]
    observer_rows=[r for r in rows if any(r['name'].startswith('ai-team-'+n+'-') for n in ['grafana','prometheus','tempo','loki','otel-collector'])]
    resources['samples'].append({'phase':label,'containers':rows,'observer_mib':round(sum(mib(r['memory']) for r in observer_rows),2),
        'installation_mib':round(sum(mib(r['memory']) for r in rows),2)})

def sampling():
    while not stop_sampling.wait(8):
        try:sample('mock-workload')
        except subprocess.SubprocessError:pass

def metric(token):
    req=urllib.request.Request(BASE+'/metrics',headers={'Authorization':'Bearer '+token})
    try:
        with urllib.request.urlopen(req,timeout=10) as response:return response.status,response.read().decode()
    except urllib.error.HTTPError as exc:return exc.code,exc.read().decode()

def proxy(source,path):return grafana('/api/datasources/proxy/uid/ai-team-'+source+path)
def prom(query):return proxy('prometheus','/api/v1/query?'+urllib.parse.urlencode({'query':query}))['data']['result']
def eventually(fn,timeout=100):
    until=time.monotonic()+timeout;last=None
    while time.monotonic()<until:
        try:
            last=fn()
            if last:return last
        except (urllib.error.HTTPError,urllib.error.URLError,http.client.RemoteDisconnected,TimeoutError):pass
        time.sleep(2)
    raise AssertionError(('telemetry timeout',str(last)[:200]))
def finished(id):
    def ready():
        story=call('/stories/'+id)
        if story['status'] in ['FAILED','BLOCKED']:raise AssertionError((story['status'],story['error_type']))
        return story if story.get('execution') and story['execution']['status']=='WAITING_GITHUB' else None
    return eventually(ready,200)

observers=['grafana','prometheus','tempo','loki','otel-collector']
try:
    docker('compose','stop','worker')
    docker('compose','--profile','observability','stop',*observers);sample('profile-off')
    docker('compose','--profile','observability','up','-d','--no-deps',*observers)
    eventually(lambda:grafana('/api/datasources/uid/ai-team-prometheus/health')['status']=='OK')
    sample('profile-on-idle')
    a,_,_=create('[qa-defect] STAGE4_PRIVATE_PAYLOAD_CANARY Синтетическая витрина и Excel по месяцам')
    b,_,_=create('Синтетическая проверка бюджета наблюдаемости',max_calls=1)
    c,_,_=create('Синтетическая проверка изоляции тестового worker')
    aid,bid,cid=[x['story_id'] for x in [a,b,c]]
    docker('compose','run','-d','--no-deps','--name','ai-team-stage4-git-mock','-e','GITHUB_RUNTIME_TOKEN=','git-broker')
    docker('compose','run','-d','--no-deps','--name','ai-team-stage4-mock','-e','LLM_BACKEND=mock','-e','OPENROUTER_API_KEY=',
        '-e','OPENROUTER_MODEL=mock/contracts-v1','-e','GIT_BROKER_URL=http://ai-team-stage4-git-mock:8000','-e','WORKER_STORY_IDS='+aid+','+bid,'worker')
    thread=threading.Thread(target=sampling,daemon=True);thread.start()
    plan=wait(aid,{'PLAN_REVIEW'});path='/stories/'+aid
    call(path+'/approve-plan','POST',{'revision':1,'plan_digest':plan['plan_digest']});wait(aid,{'IN_PROGRESS'})
    call(path+'/analytics/start','POST',{});spec=wait(aid,{'PLAN_REVIEW'})
    call(path+'/analytics/approve','POST',{'revision':1,'plan_digest':spec['analytics']['digest']});wait(aid,{'IN_PROGRESS'})
    call(path+'/execution/start','POST',{});story=finished(aid)
    assert story['usage']['calls']==7 and story['execution']['attempt']==2 and story['execution']['qa']['passed']
    assert len(story['execution']['rows'])==24
    assert len([e for e in call(path+'/events') if e['type']=='execution.defect'])==1
    report['checks'].append('real SQL, actual tracker Bug and corrected QA; 24 rows, seven mock calls, no Git writes')
    budget=wait(bid,{'BLOCKED'});assert budget['error_type']=='BudgetExceeded' and budget['usage']['calls']==1
    assert call('/stories/'+cid)['status']=='NEW'
    report['checks'].append('scoped worker leaves unrelated story NEW and enforces shared call budget')
    usage,artifacts=story['usage'],story['execution']['artifacts']
    trace_id=story['trace_id']
    docker('restart','ai-team-stage4-mock');call(path+'/execution/retry','POST',{})
    time.sleep(4)
    repeated=finished(aid)
    assert repeated['usage']==usage and repeated['execution']['artifacts']==artifacts and repeated['trace_id']==trace_id
    report['checks'].append('restart and retry preserve ledger usage, artifact hashes and trace ID')
    status,metrics=metric(values['TELEMETRY_METRICS_TOKEN']);assert status==200
    assert metric('')[0]==401 and metric(TOKEN)[0]==401
    req=urllib.request.Request(BASE+'/stories',headers={'Authorization':'Bearer '+values['TELEMETRY_METRICS_TOKEN']})
    try:urllib.request.urlopen(req)
    except urllib.error.HTTPError as exc:assert exc.code==401
    else:raise AssertionError('Metrics token authorized business API')
    report['checks'].append('metrics token grants only metrics; APP token and missing credentials rejected')
    ratio=eventually(lambda:prom('ai_team_story_budget_ratio{story_id="'+bid+'",dimension="calls"}'))
    assert float(ratio[0]['value'][1])==1
    alert=eventually(lambda:prom('ALERTS{alertname="AiTeamBudgetExhausted",story_id="'+bid+'",alertstate="firing"}'))
    report['checks'].append('Prometheus durable budget gauge equals ledger; critical alert actually fires')
    probe_code='''import json
from app.observability import story_trace,tracer,log,flush
with story_trace(json.loads(__import__('sys').argv[1]),'probe'):
    try:
        with tracer.start_as_current_span('telemetry.probe',attributes={'prompt':'STAGE4_SECRET_ATTRIBUTE_CANARY'}):
            log('telemetry.probe',response='STAGE4_SECRET_RESPONSE_CANARY',sql='STAGE4_SQL_BODY_CANARY',credential='STAGE4_SECRET_CREDENTIAL_CANARY')
            raise ValueError('STAGE4_EXCEPTION_BODY_CANARY')
    except ValueError:pass
assert flush()
'''
    probe_story=json.dumps({'id':aid,'revision':1,'trace_id':trace_id})
    docker('exec','ai-team-stage4-mock','python','-c',probe_code,probe_story)
    # Collector downtime must not break authenticated business reads or ledger snapshots.
    docker('compose','--profile','observability','stop','otel-collector')
    docker('exec','ai-team-stage4-mock','python','-c',probe_code,probe_story)
    call('/stories/'+cid+'/cancel','POST',{})
    assert call('/stories/'+cid)['status']=='CANCELLED'
    assert call(path)['usage']==usage and metric(values['TELEMETRY_METRICS_TOKEN'])[0]==200
    docker('compose','--profile','observability','up','-d','--no-deps','otel-collector')
    eventually(lambda:grafana('/api/datasources/uid/ai-team-loki/health')['status']=='OK')
    docker('exec','ai-team-stage4-mock','python','-c',probe_code,probe_story)
    report['checks'].append('Collector outage leaves API/ledger available; export recovers after restart (best effort)')
    query='{service_name=~"ai-team-.*"} | json | story_id="'+aid+'"'
    logs=eventually(lambda:proxy('loki','/loki/api/v1/query_range?'+urllib.parse.urlencode({'query':query,'limit':1000,'start':str(int((time.time()-1800)*1e9))})))
    eventually(lambda:'telemetry.probe' in json.dumps(proxy('loki','/loki/api/v1/query_range?'+urllib.parse.urlencode({'query':query,'limit':1000}))))
    logs=proxy('loki','/loki/api/v1/query_range?'+urllib.parse.urlencode({'query':query,'limit':1000}))
    def read_trace():
        request=urllib.request.Request('http://127.0.0.1:3001/api/datasources/proxy/uid/ai-team-tempo/api/traces/'+trace_id,headers=headers|{'Accept':'application/json'})
        with urllib.request.urlopen(request,timeout=30) as response:return json.load(response)
    trace=eventually(read_trace)
    spans=[s for batch in trace.get('batches',trace.get('resourceSpans',[])) for scope in batch.get('scopeSpans',batch.get('instrumentationLibrarySpans',[])) for s in scope['spans']]
    names={s['name'] for s in spans}
    required={'story.run','llm.request','tool.sql','tracker.create','tool.knowledge','artifact.publish','agent.qa','telemetry.probe'}
    assert required<=names,('missing spans',sorted(required-names),list(trace))
    assert not any(n.startswith('fastapi.') for n in names),'Framework span exported'
    services={a['value']['stringValue'] for b in trace.get('batches',trace.get('resourceSpans',[])) for a in b['resource'].get('attributes',[]) if a['key']=='service.name'}
    assert {'ai-team-api','ai-team-worker'}<=services,services
    serialized=json.dumps({'logs':logs,'traces':trace,'metrics':metrics})
    for marker in ['STAGE4_PRIVATE_PAYLOAD_CANARY','STAGE4_SECRET_ATTRIBUTE_CANARY','STAGE4_SECRET_RESPONSE_CANARY','STAGE4_SECRET_CREDENTIAL_CANARY','STAGE4_EXCEPTION_BODY_CANARY','STAGE4_SQL_BODY_CANARY']:
        assert marker not in serialized,'Payload appeared in telemetry'
    secrets=[v for k,v in values.items() if any(x in k for x in ['TOKEN','PASSWORD','API_KEY','SECRET']) and len(v)>8]
    assert not any(secret in serialized for secret in secrets),'Credential appeared in telemetry'
    assert any(s.get('status',{}).get('code') in [2,'STATUS_CODE_ERROR'] for s in spans if s['name']=='telemetry.probe')
    report['checks'].extend(['API/worker/tool/agent spans share persisted story trace in real Tempo','Loki/Tempo/metrics exclude private payload, SQL/prompt/response/exception canaries and all local credentials'])
    loki=grafana('/api/datasources/uid/ai-team-loki');tempo=grafana('/api/datasources/uid/ai-team-tempo')
    assert loki['jsonData']['derivedFields'][0]['url']=='${__value.raw}'
    assert '${__trace.traceId}' in tempo['jsonData']['tracesToLogsV2']['query']
    report['checks'].append('provisioned Loki→Tempo and Tempo→Loki links preserve Grafana macros')
    report.update(status='passed',trace_id=trace_id,span_names=sorted(names),services=sorted(services),usage=usage,
        qa_attempts=2,rows=24,telemetry_contains_credentials=False)
finally:
    stop_sampling.set()
    for id in report['story_ids']:
        try:call('/stories/'+id+'/cancel','POST',{})
        except Exception:pass
    for name in ['ai-team-stage4-mock','ai-team-stage4-git-mock']:
        subprocess.run(['docker','rm','-f',name],capture_output=True,timeout=30)
    docker('compose','--profile','observability','up','-d','--no-deps','otel-collector','worker')
    sample('profile-on-final')
    Path('docs/stage4-resources.json').write_text(json.dumps(resources,ensure_ascii=False,indent=2)+'\n')
    Path('docs/stage4-check.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(report,ensure_ascii=False,indent=2))
