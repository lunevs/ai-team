"""Operator E2E on already restored test volumes/services; no production DB mutation.
Preparation: docs/operations.md. Model=mock, isolated broker has no GitHub credential.
"""
exec(open('scripts/run_stage1_checks.py').read().split("call('/stories',authorized=False")[0])
from urllib.parse import quote
import hashlib,os,http.client
ROOT=Path.cwd();PREFIX='ai-team-dr-5e000002'
values=dict(l.split('=',1) for l in (ROOT/'.local'/PREFIX/'.env').read_text().splitlines() if '=' in l and not l.startswith('#'))
BASE='http://127.0.0.1:18000';report={'backend':'mock','database':'isolated restored PostgreSQL with TLS','storage':'real local Garage/S3','checks':[],'story_ids':[]}
private=ROOT/'.local/stage5-check';private.mkdir(mode=0o700,exist_ok=True)

def docker(*args):
    p=subprocess.run(['docker',*args],capture_output=True,text=True,timeout=180)
    if p.returncode:raise RuntimeError('Test Docker command failed (output withheld)')
    return p.stdout

def envfile(name,settings):
    path=private/name;path.write_text('\n'.join(k+'='+v for k,v in settings.items())+'\n');path.chmod(0o600);return str(path)

def launch(name,environment,command,networks,volumes=(),port=None):
    args=['create','--name',name,'--network',networks[0],'--read-only','--user','10001:10001','--cap-drop','ALL',
        '--security-opt','no-new-privileges:true','--tmpfs','/tmp','--memory','512m','--env-file',environment]
    for volume in volumes:args+=['-v',volume]
    if port:args+=['-p',port]
    args+=['ai-team-app:0.6.0',*command];docker(*args)
    for network in networks[1:]:docker('network','connect',network,name)
    docker('start',name)

def eventually(fn,timeout=180):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        try:
            result=fn()
            if result:return result
        except (urllib.error.URLError,AssertionError,http.client.RemoteDisconnected,TimeoutError):pass
        time.sleep(1)
    raise AssertionError('Stage5 timeout')

config=json.loads(docker('compose','config','--format','json'))
writer=dict(l.split('=',1) for l in (ROOT/'.local/stage5-services/garage/writer.env').read_text().splitlines())
reader=dict(l.split('=',1) for l in (ROOT/'.local/stage5-services/garage/reader.env').read_text().splitlines())
dburl=lambda role,password:'postgresql://'+role+':'+quote(password,safe='')+'@stage5-postgres/ai_team?sslmode=verify-full&sslrootcert=/run/secrets/pg-ca.crt'
ca=str(ROOT/'.local/stage5-services/tls/server.crt')+':/run/secrets/pg-ca.crt:ro'
api=config['services']['api']['environment']|reader|{'DATABASE_URL':dburl('ai_team_runtime',values['APP_RUNTIME_DB_PASSWORD']),'OTEL_EXPORTER_OTLP_ENDPOINT':'','GIT_BROKER_URL':'http://ai-team-stage5-git-mock:8000'}
worker=config['services']['worker']['environment']|writer|{'DATABASE_URL':api['DATABASE_URL'],'LLM_BACKEND':'mock','OPENROUTER_API_KEY':'','OPENROUTER_MODEL':'mock/contracts-v1','OTEL_EXPORTER_OTLP_ENDPOINT':'','SANDBOX_URL':'http://ai-team-stage5-sandbox:8000','GIT_BROKER_URL':'http://ai-team-stage5-git-mock:8000'}
sandbox={'SANDBOX_DATABASE_URL':dburl('ai_team_sandbox',values['SANDBOX_DB_PASSWORD']),'SANDBOX_API_TOKEN':values['SANDBOX_API_TOKEN']}
broker={'GITHUB_RUNTIME_TOKEN':'','GIT_BROKER_API_TOKEN':values['GIT_BROKER_API_TOKEN'],'GITHUB_BASE_REF':'release/263'}
containers=['ai-team-stage5-worker','ai-team-stage5-api','ai-team-stage5-sandbox','ai-team-stage5-git-mock']
try:
    docker('network','create','--internal','ai-team-stage5-execution')
    docker('network','connect','--alias','stage5-postgres','ai-team-stage5-execution','ai-team-stage5-postgres')
    launch(containers[3],envfile('broker.env',broker),['uvicorn','app.git_broker:app','--host','0.0.0.0','--port','8000','--no-access-log'],['ai-team_backend'])
    launch(containers[2],envfile('sandbox.env',sandbox),['uvicorn','app.sandbox_service:app','--host','0.0.0.0','--port','8000','--no-access-log'],['ai-team-stage5-execution'],[ca])
    launch(containers[1],envfile('api.env',api),['uvicorn','app.api:app','--host','0.0.0.0','--port','8000','--no-access-log'],['ai-team_backend','ai-team_observability-ui'],[ca,PREFIX+'_delivery_artifacts:/data/artifacts:ro'],'127.0.0.1:18000:8000')
    eventually(lambda:call('/health'))
    # Confirm backward compatibility by downloading original real OpenRouter artifact from restored volume.
    old=call('/stories/379f6486-4111-404e-a7fb-3272f1ef9d22');old_manifest=old['execution']['artifacts']['report.xlsx']
    def download(path):
        request=urllib.request.Request(BASE+path,headers={'Authorization':'Bearer '+TOKEN})
        with urllib.request.urlopen(request,timeout=30) as response:return response.read()
    prior=download('/stories/'+old['story_id']+'/execution/artifacts/report.xlsx')
    assert hashlib.sha256(prior).hexdigest()==old_manifest['sha256']
    report['checks'].append('legacy local report downloads through new API from restored volume with same SHA256')
    created,_,_=create('[qa-defect] Создать синтетическую витрину и Excel; этап 5 S3 recovery')
    id=created['story_id'];path='/stories/'+id;worker['WORKER_STORY_IDS']=id
    launch(containers[0],envfile('worker.env',worker),['python','-m','app.worker'],['ai-team_backend','ai-team_frontend','ai-team-stage5-execution'],[ca])
    plan=wait(id,{'PLAN_REVIEW'});call(path+'/approve-plan','POST',{'revision':1,'plan_digest':plan['plan_digest']});wait(id,{'IN_PROGRESS'})
    call(path+'/analytics/start','POST',{});analytics=wait(id,{'PLAN_REVIEW'})
    call(path+'/analytics/approve','POST',{'revision':1,'plan_digest':analytics['analytics']['digest']});wait(id,{'IN_PROGRESS'})
    call(path+'/execution/start','POST',{})
    def finished():
        story=call(path)
        if story['status'] in ['FAILED','BLOCKED']:raise RuntimeError('Workflow stopped: '+str(story['error_type']))
        return story if story.get('execution') and story['execution']['status']=='WAITING_GITHUB' else None
    result=eventually(finished)
    assert result['execution']['qa']['passed'] and len(result['execution']['rows'])==24 and result['execution']['attempt']==2
    assert result['usage']['calls']==7 and result['usage']['backends']==['mock']
    artifacts=result['execution']['artifacts'];assert len(artifacts)==9 and all(m['backend']=='s3' for m in artifacts.values())
    for name,manifest in artifacts.items():
        content=download(path+'/execution/artifacts/'+name)
        assert len(content)==manifest['size'] and hashlib.sha256(content).hexdigest()==manifest['sha256']
    report['checks'].extend(['full mock workflow on restored TLS PostgreSQL with real tracker/BookStack/sandbox','actual QA defect repaired on attempt 2; 24 correct rows','all nine artifacts stored in real S3 and downloaded by separate read-only API credential'])
    # A container restart removes all /tmp files; retry must use S3, not a hidden local cache.
    docker('restart',containers[0]);call(path+'/execution/retry','POST',{});time.sleep(4)
    repeated=eventually(finished);assert repeated['usage']==result['usage'] and repeated['execution']['artifacts']==artifacts
    assert download(path+'/execution/artifacts/report.xlsx')[:2]==b'PK'
    report['checks'].append('worker restart clears tmp workspace; retry preserves usage/manifests and S3 download')
    # Reader writes must be rejected by the real object service, independently of Python policy.
    probe="import uuid; from app.storage import store,StorageError; s=store();\ntry:s.put(uuid.uuid4(),'qa.json',b'forbidden')\nexcept StorageError:pass\nelse:raise AssertionError('reader wrote object')"
    docker('exec',containers[1],'python','-c',probe)
    report['checks'].append('S3 rejects PUT with the actual API reader key')
    report.update(status='passed',usage=result['usage'],rows=24,artifacts=len(artifacts),qa_attempts=2,production_database_used=False)
finally:
    for id in report['story_ids']:
        try:
            call('/stories/'+id+'/cancel','POST',{})
            docker('exec',containers[0],'python','-c',"from app.integrations import sync_status; import sys; sync_status(sys.argv[1],'CANCELLED')",id)
        except Exception:pass
    for name in containers:subprocess.run(['docker','rm','-f',name],capture_output=True,timeout=40)
    subprocess.run(['docker','network','disconnect','ai-team-stage5-execution','ai-team-stage5-postgres'],capture_output=True,timeout=20)
    subprocess.run(['docker','network','rm','ai-team-stage5-execution'],capture_output=True,timeout=20)
    Path('docs/stage5-check.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(report,ensure_ascii=False,indent=2))
