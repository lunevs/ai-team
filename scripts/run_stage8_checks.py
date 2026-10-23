"""Disposable DB + real tracker/KB + mock LLM + tokenless Git broker. No user product."""
import hashlib
import argparse
import json
from pathlib import Path
import secrets
import shutil
import sys
import time
import urllib.error
import urllib.request
import uuid
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts import product_operator as host
ROOT=Path(__file__).resolve().parents[1]
PRIVATE=ROOT/'.local/stage8-workflow'
PREFIX='ai-team-stage8-'
NAMES=[PREFIX+x for x in ['worker','api','broker','postgres']]
PGIMAGE='postgres:17.6-alpine@sha256:ef257d85f76e48da1c64832459b59fcaba1a4dac97bf5d7450c77753542eee94'
BACKEND='mock'
report={'backend':BACKEND,'database':'disposable empty PostgreSQL','github':'tokenless simulated review gates; no GitHub writes','checks':[]}


def check(name,condition=True):
    if not condition:raise AssertionError(name)
    report['checks'].append(name)


def envfile(name,values):
    path=PRIVATE/name;path.write_text('\n'.join(k+'='+str(v) for k,v in values.items())+'\n');path.chmod(0o600);return str(path)


def launch(name,env,cmd,volume=None):
    args=['run','--detach','--name',name,'--label','ai-team.stage8=true','--network','container:'+NAMES[-1],
        '--read-only','--user','10001:10001','--cap-drop','ALL','--security-opt','no-new-privileges:true',
        '--tmpfs','/tmp','--memory','512m','--env-file',env]
    if volume:args+=['--mount',volume]
    host.docker(*args,'ai-team-app:0.7.0',*cmd)


def wait(fn,timeout=480):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        try:
            value=fn()
            if value:return value
        except (urllib.error.URLError,ConnectionError,TimeoutError):pass
        time.sleep(0.5)
    raise AssertionError('Stage6 workflow timeout')


def main():
    PRIVATE.mkdir(parents=True,exist_ok=False,mode=0o700)
    config=json.loads(host.docker('compose','config','--format','json').stdout)
    token=secrets.token_hex(32);operator=secrets.token_hex(32);password=secrets.token_hex(24)
    port=host.free_port();host.API_URL='http://127.0.0.1:'+str(port)
    artifact=PREFIX+'artifacts';id=None
    for name in NAMES:
        if host.docker('inspect',name,check=False).returncode==0:raise RuntimeError('Fixture exists; refuse overwrite')
    call=lambda path,method='GET',data=None,op=False,binary=False:host.api(path,token=operator if op else token,method=method,data=data,binary=binary)
    try:
        pg_env=envfile('postgres.env',{'POSTGRES_PASSWORD':password})
        host.docker('create','--name',NAMES[-1],'--label','ai-team.stage8=true','--network','ai-team_backend',
            '--publish',f'127.0.0.1:{port}:8000','--memory','512m','--env-file',pg_env,PGIMAGE)
        host.docker('network','connect','ai-team_frontend',NAMES[-1]);host.docker('start',NAMES[-1])
        wait(lambda:host.docker('exec',NAMES[-1],'pg_isready','-U','postgres',check=False).returncode==0)
        sql=f"CREATE ROLE ai_team LOGIN PASSWORD '{password}';CREATE ROLE ai_team_runtime LOGIN PASSWORD '{password}';CREATE DATABASE ai_team OWNER ai_team;"
        host.docker('exec','-i',NAMES[-1],'psql','-U','postgres','-v','ON_ERROR_STOP=1',input=sql.encode())
        url=lambda role:f'postgresql://{role}:{password}@127.0.0.1/ai_team'
        migration=envfile('migrate.env',{'DATABASE_URL':url('ai_team')})
        host.docker('run','--rm','--network','container:'+NAMES[-1],'--env-file',migration,'ai-team-app:0.7.0','python','-m','app.migrate')
        runtime_env=envfile('runtime-test.env',{'DATABASE_URL':url('ai_team_runtime')})
        host.docker('run','--rm','--network','container:'+NAMES[-1],'--env-file',runtime_env,
            '--mount',f'type=bind,source={ROOT}/tests,target=/workspace/tests,readonly','ai-team-app:0.7.0',
            'python','-m','pytest','-q','tests/integration','-p','no:cacheprovider')
        check('postgres_persistence_and_operator_leases')
        host.docker('volume','create','--label','ai-team.stage8=true',artifact)
        broker=envfile('broker.env',{'GIT_BROKER_API_TOKEN':token})
        launch(NAMES[2],broker,['uvicorn','scripts.stage8_broker_fixture:app','--host','0.0.0.0','--port','8001','--no-access-log'])
        api=config['services']['api']['environment']|{'DATABASE_URL':url('ai_team_runtime'),'APP_API_TOKEN':token,'PRODUCT_OPERATOR_TOKEN':operator,
            'OTEL_EXPORTER_OTLP_ENDPOINT':'','GIT_BROKER_URL':'http://127.0.0.1:8001','GIT_BROKER_API_TOKEN':token,'ARTIFACT_BACKEND':'local'}
        launch(NAMES[1],envfile('api.env',api),['uvicorn','app.api:app','--host','0.0.0.0','--port','8000','--no-access-log'],f'type=volume,source={artifact},target=/data/artifacts,readonly')
        wait(lambda:call('/health'))

        request=urllib.request.Request(host.API_URL+'/stories',method='POST',headers={'Authorization':'Bearer '+token,'Content-Type':'application/json','Idempotency-Key':'stage8-'+str(uuid.uuid4())},data=json.dumps({'text':'[stage8 technical fixture] Создать самостоятельный синтетический Python HTTP продукт в двух эпиках: сначала модуль данных и GET /result value=42 unit=synthetic-check, затем сохранить первый результат и добавить GET /extra value=43 unit=synthetic-check с независимыми тестами. GET /health работает. Без БД и внешних данных. Только проверка механизма команды.','synthetic_only':True,'delivery_mode':'product'}).encode())
        with urllib.request.urlopen(request,timeout=60) as r:id=json.load(r)['story_id']
        path='/stories/'+id;report['story_id']=id
        worker=config['services']['worker']['environment']|{'DATABASE_URL':url('ai_team_runtime'),'LLM_BACKEND':'mock','WORKER_STORY_IDS':id,'OTEL_EXPORTER_OTLP_ENDPOINT':'',
            'GIT_BROKER_URL':'http://127.0.0.1:8001','GIT_BROKER_API_TOKEN':token,'ARTIFACT_BACKEND':'local'}
        launch(NAMES[0],envfile('worker.env',worker),['python','-m','app.worker'],f'type=volume,source={artifact},target=/data/artifacts')
        def current():
            story=call(path)
            if story['status'] in ['FAILED','BLOCKED']:raise AssertionError('Fixture stopped: '+str(story.get('error_type')))
            return story
        plan=wait(lambda:(r if (r:=current())['status']=='PLAN_REVIEW' else None))
        check('dynamic_two_epics_and_trusted_packaging',len(plan['plan']['epics'])==2 and plan['plan']['epics'][-1]['tasks'][-1]['kind']=='package')
        call(path+'/approve-plan','POST',{'revision':1,'plan_digest':plan['plan_digest']})
        def query(statement):
            return host.docker('exec','-i',NAMES[1],'python','-',input=('from app import db\nwith db.connection() as c:\n '+statement).encode()).stdout.decode().strip()
        def wake():query('db.enqueue(c,'+repr(id)+')')
        def allow(number):
            host.docker('exec','-i',NAMES[1],'python','-',input=('import httpx,os\nr=httpx.post("http://127.0.0.1:8001/allow",headers={"Authorization":"Bearer "+os.environ["GIT_BROKER_API_TOKEN"]},json={"number":'+str(number)+'});r.raise_for_status()').encode())
            wake()
        def ws():return current().get('workspace')
        # Every check uses a real isolated Docker image. Only review decisions are simulated.
        done=set();release_allowed=False;restarted=False;receipts=[];saw_read_edit=False
        deadline=time.monotonic()+600
        while time.monotonic()<deadline:
            row=current();workspace=row.get('workspace')
            if row.get('product') and row['product']['status']=='CHECK_PENDING':break
            if workspace:
                active=next((t for t in workspace['tasks'] if t['state'] not in ['PENDING','MERGED']),None)
                if active and active['state']=='CHECK_PENDING':
                    try:
                        call('/workspace-operations/claim','POST');raise AssertionError('Operator auth bypass')
                    except urllib.error.HTTPError as e:check('workspace_operator_auth_'+active['task_key'],e.code==401)
                    op=call('/workspace-operations/claim','POST',op=True)
                    content=call('/workspace-operations/'+op['id']+'/snapshot',binary=True)
                    from scripts.workspace_operator import check_snapshot
                    receipt={'operation_id':op['operation_id'],'snapshot_sha256':op['snapshot_sha256'],'passed':False}
                    bad=receipt|{'snapshot_sha256':'0'*64}
                    try:call('/workspace-operations/'+op['id']+'/validate','POST',bad,op=True);raise AssertionError('Digest bypass')
                    except urllib.error.HTTPError as e:check('snapshot_binding_'+active['task_key'],e.code==409)
                    verified=check_snapshot(content,op,PRIVATE/'workspaces');check('docker_check_'+active['task_key'],verified['passed'])
                    receipt.update(verified)
                    before=current()['usage']
                    if not restarted:
                        host.docker('restart',NAMES[0]);restarted=True
                        check('restart_preserves_pending_snapshot',call('/workspace-operations/'+op['id']+'/snapshot',binary=True)==content and current()['usage']==before)
                    result=call('/workspace-operations/'+op['id']+'/report','POST',receipt,op=True)
                    # Stop worker so immediate action cannot consume the receipt before replay.
                    if result['state']=='CHECKED':
                        repeated=call('/workspace-operations/'+op['id']+'/report','POST',receipt,op=True)
                        check('receipt_replay_'+active['task_key'],repeated.get('replayed',False))
                    receipts.append({'task_key':active['task_key'],'tests_run':verified['tests_run']})
                elif active and active['state']=='REVIEW' and active['task_key'] not in done:
                    before=workspace['version'];time.sleep(1)
                    check('no_integration_before_review_'+active['task_key'],ws()['version']==before)
                    allow(active['git']['number']);done.add(active['task_key'])
                elif workspace.get('milestones') and not release_allowed:
                    check('second_epic_waits_for_release',workspace['epic_index']==0 and all(t['state']=='PENDING' for t in workspace['tasks'] if t['task_key'] in ['extend','quality']))
                    allow(workspace['milestones'][0]['number']);release_allowed=True
            time.sleep(0.3)
        else:raise AssertionError('Task workflow timeout')
        final=current();workspace=final['workspace']
        check('check_resumes_before_finish',next(t for t in workspace['tasks'] if t['task_key']=='extend')['sequence']==8)
        check('all_four_tasks_integrated',workspace['version']==4 and {t['state'] for t in workspace['tasks']}=={'MERGED'})
        check('two_epics_released_in_order',workspace['epic_index']==1 and release_allowed)
        op=call('/product-operations/claim','POST',op=True)
        content=call('/products/'+op['id']+'/package',binary=True)
        verified=host.check_package(content,op);check('final_real_docker_tests_and_health',verified['passed'])
        check('first_endpoint_preserved',verified['result']=={'value':42,'unit':'synthetic-check'})
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            metadata,context=host.extract(content,Path(td));tag='ai-product-'+metadata['product_key']+':'+metadata['version'];probe_name=PREFIX+'endpoint'
            try:
                host.docker(*host.restricted(tag,name=probe_name,detach=True))
                host.health(probe_name)
                check('second_endpoint_independent_probe',json.loads(host.probe(probe_name,'/extra').stdout)=={'value':43,'unit':'synthetic-check'})
            finally:host.remove_container(probe_name)
        receipt=verified|{'operation_id':op['operation_id'],'package_sha256':hashlib.sha256(content).hexdigest()}
        call('/product-operations/'+op['id']+'/report','POST',receipt,op=True)
        finished=wait(lambda:(r if (r:=current()).get('product',{}).get('status')=='PUBLISHED' else None))
        check('independent_qa_and_architecture_reviews',finished['product']['review']['qa']['approved'] and finished['product']['review']['architecture']['approved'])
        check('final_delivery_still_requires_review',call('/product-operations/claim','POST',op=True)['operation'] is None)
        report['usage']=finished['usage'];report['task_checks']=receipts;report['status']='passed'
        (ROOT/'docs/stage8-mock-check.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps(report,ensure_ascii=False,indent=2))
    except Exception:
        diagnostic=host.docker('logs','--tail','80',NAMES[0],check=False)
        (ROOT/'.local/stage8-worker-diagnostic.log').write_bytes(diagnostic.stdout+diagnostic.stderr)
        raise
    finally:
        if id:
            try:
                call('/stories/'+id+'/cancel','POST',{})
                host.docker('exec','-i',NAMES[1],'python','-',input=('from app.integrations import tracker\ntracker.set_status('+repr(id)+',"CANCELLED")').encode())
            except Exception:pass
        for name in NAMES:host.docker('rm','-f','-v',name,check=False)
        host.docker('volume','rm',artifact,check=False)
        shutil.rmtree(PRIVATE)

if __name__=='__main__':main()
