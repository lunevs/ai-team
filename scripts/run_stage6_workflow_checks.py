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
PRIVATE=ROOT/'.local/stage6-workflow'
PREFIX='ai-team-stage6-'
NAMES=[PREFIX+x for x in ['worker','api','broker','postgres']]
PGIMAGE='postgres:17.6-alpine@sha256:ef257d85f76e48da1c64832459b59fcaba1a4dac97bf5d7450c77753542eee94'
BACKEND='mock'
report={'backend':BACKEND,'database':'disposable empty PostgreSQL','github':'tokenless broker; no PR writes','checks':[]}


def check(name,condition=True):
    if not condition:raise AssertionError(name)
    report['checks'].append(name)


def envfile(name,values):
    path=PRIVATE/name;path.write_text('\n'.join(k+'='+str(v) for k,v in values.items())+'\n');path.chmod(0o600);return str(path)


def launch(name,env,cmd,volume=None):
    args=['run','--detach','--name',name,'--label','ai-team.stage6=true','--network','container:'+NAMES[-1],
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
        host.docker('create','--name',NAMES[-1],'--label','ai-team.stage6=true','--network','ai-team_backend',
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
        host.docker('volume','create','--label','ai-team.stage6=true',artifact)
        broker=envfile('broker.env',{'GITHUB_RUNTIME_TOKEN':'','GIT_BROKER_API_TOKEN':config['services']['api']['environment']['GIT_BROKER_API_TOKEN']})
        launch(NAMES[2],broker,['uvicorn','app.git_broker:app','--host','0.0.0.0','--port','8001','--no-access-log'])
        api=config['services']['api']['environment']|{'DATABASE_URL':url('ai_team_runtime'),'APP_API_TOKEN':token,'PRODUCT_OPERATOR_TOKEN':operator,
            'OTEL_EXPORTER_OTLP_ENDPOINT':'','GIT_BROKER_URL':'http://127.0.0.1:8001','ARTIFACT_BACKEND':'local'}
        launch(NAMES[1],envfile('api.env',api),['uvicorn','app.api:app','--host','0.0.0.0','--port','8000','--no-access-log'],f'type=volume,source={artifact},target=/data/artifacts,readonly')
        wait(lambda:call('/health'))

        request=urllib.request.Request(host.API_URL+'/stories',method='POST',headers={'Authorization':'Bearer '+token,'Content-Type':'application/json','Idempotency-Key':'stage6-'+str(uuid.uuid4())},data=json.dumps({'text':'[stage6 technical fixture] Самостоятельный синтетический HTTP продукт: GET /result возвращает value=42, unit=synthetic-check. Без внешних данных и финансовых расчётов; проверка протокола, не продукт заказчика.','synthetic_only':True,'delivery_mode':'product'}).encode())
        with urllib.request.urlopen(request,timeout=60) as r:story=json.load(r)
        id=story['story_id'];report['story_id']=id;path='/stories/'+id
        worker=config['services']['worker']['environment']|{'DATABASE_URL':url('ai_team_runtime'),'LLM_BACKEND':BACKEND,'OPENROUTER_API_KEY':config['services']['worker']['environment'].get('OPENROUTER_API_KEY','') if BACKEND=='openrouter' else '',
            'OPENROUTER_MODEL':config['services']['worker']['environment'].get('OPENROUTER_MODEL','') if BACKEND=='openrouter' else 'mock/product-contracts-v1','WORKER_STORY_IDS':id,'OTEL_EXPORTER_OTLP_ENDPOINT':'',
            'GIT_BROKER_URL':'http://127.0.0.1:8001','ARTIFACT_BACKEND':'local'}
        launch(NAMES[0],envfile('worker.env',worker),['python','-m','app.worker'],f'type=volume,source={artifact},target=/data/artifacts')
        def state(expected):
            current=call(path)
            if current['status']=='FAILED':raise RuntimeError('Workflow failed: '+str(current['error_type']))
            return current if current['status']==expected else None
        plan=wait(lambda:state('PLAN_REVIEW'))
        check('product_plan_without_credit_defaults',plan['delivery_mode']=='product' and 'synthetic-check' in ' '.join(plan['pm']['acceptance_criteria']))
        call(path+'/approve-plan','POST',{'revision':1,'plan_digest':plan['plan_digest']})
        def product_status(status):
            current=call(path)
            if current['status']=='FAILED':raise RuntimeError('Workflow failed: '+str(current['error_type']))
            return current if current.get('product') and current['product']['status']==status else None
        packaged=wait(lambda:product_status('CHECK_PENDING'))
        check('six_roles_and_product_package',len(packaged['plan']['epics'][0]['tasks'])==6 and bool(packaged['product']['package']))
        try:call('/product-operations/claim','POST');raise AssertionError('Operator auth bypass')
        except urllib.error.HTTPError as e:check('separate_operator_authorization',e.code==401)
        operation=call('/product-operations/claim','POST',op=True)
        content=call('/products/'+operation['id']+'/package',binary=True)
        digest=hashlib.sha256(content).hexdigest();check('package_sha256',digest==operation['package']['sha256'])
        verified=host.check_package(content,operation);check('real_product_container_tests',verified['passed'])
        check('independent_fixture_result_42',verified['result']=={'value':42,'unit':'synthetic-check'})
        bad=verified|{'operation_id':operation['operation_id'],'package_sha256':'0'*64}
        try:call('/product-operations/'+operation['id']+'/report','POST',bad,op=True);raise AssertionError('Digest bypass')
        except urllib.error.HTTPError as e:check('stale_digest_rejected',e.code==409)
        receipt=verified|{'operation_id':operation['operation_id'],'package_sha256':digest}
        call('/product-operations/'+operation['id']+'/report','POST',receipt,op=True)
        finished=wait(lambda:product_status('WAITING_GITHUB'))
        check('independent_qa_and_architecture_reviews',finished['product']['review']['qa']['approved'] and finished['product']['review']['architecture']['approved'])
        check('nine_accounted_'+BACKEND+'_calls_including_feasibility',finished['usage']['calls']==9 and finished['usage']['backends']==[BACKEND] and finished['usage']['estimated_cost_usd']<=1)
        report['usage']=finished['usage']
        check('no_deployment_without_git_review_merge',call('/product-operations/claim','POST',op=True)['operation'] is None)
        host.docker('restart',NAMES[0]);call(path+'/product/refresh','POST')
        time.sleep(2)
        repeated=wait(lambda:product_status('WAITING_GITHUB'))
        check('restart_no_duplicate_llm_or_package',repeated['usage']==finished['usage'] and repeated['product']['package']==finished['product']['package'])
        # Current revision can no longer be executed after cancellation.
        call(path+'/cancel','POST')
        wait(lambda:state('CANCELLED'))
        check('cancel_prevents_operator_claim',call('/product-operations/claim','POST',op=True)['operation'] is None)
        report['status']='passed'
    finally:
        if id:
            try:
                call('/stories/'+id+'/cancel','POST')
                # Reconcile the real tracker before disposable state disappears.
                sync="from app.integrations import tracker;tracker.set_status('"+id+"','CANCELLED')"
                for attempt in range(5):
                    result=host.docker('exec',NAMES[0],'python','-c',sync,check=False)
                    if result.returncode==0:break
                    time.sleep(1)
                else:raise RuntimeError('Tracker cancellation pending')
            except Exception:report['cleanup_warning']='Check fixture tracker cancellation'
        for name in NAMES:
            info=host.docker('inspect',name,check=False)
            if info.returncode==0:
                data=json.loads(info.stdout)[0]
                if data['Config']['Labels'].get('ai-team.stage6')!='true':raise RuntimeError('Foreign container')
                host.docker('rm','--force','--volumes',name)
        info=host.docker('volume','inspect',artifact,check=False)
        if info.returncode==0 and json.loads(info.stdout)[0].get('Labels',{}).get('ai-team.stage6')=='true':host.docker('volume','rm',artifact)
        if PRIVATE.exists():shutil.rmtree(PRIVATE)
        (ROOT/('docs/stage6-openrouter-workflow-check.json' if BACKEND=='openrouter' else 'docs/stage6-workflow-check.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--backend',choices=['mock','openrouter'],default='mock')
    BACKEND=parser.parse_args().backend;report['backend']=BACKEND
    main()
