"""Isolated planning gate: disposable DB, real tracker/KB, scoped worker; no product runs."""
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
PRIVATE=ROOT/'.local/stage7-checks'
PREFIX='ai-team-stage7-'
NAMES=[PREFIX+x for x in ['worker','api','postgres']]
PGIMAGE='postgres:17.6-alpine@sha256:ef257d85f76e48da1c64832459b59fcaba1a4dac97bf5d7450c77753542eee94'


def wait(fn,timeout=300):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        try:
            result=fn()
            if result:return result
        except (urllib.error.URLError,ConnectionError,TimeoutError):pass
        time.sleep(0.5)
    raise AssertionError('Stage7 timeout')


def main(backend):
    report={'backend':backend,'database':'disposable empty PostgreSQL','checks':[]}
    def check(name,condition):
        if not condition:raise AssertionError(name)
        report['checks'].append(name)
    PRIVATE.mkdir(parents=True,exist_ok=False,mode=0o700)
    config=json.loads(host.docker('compose','config','--format','json').stdout)
    def envfile(name,values):
        path=PRIVATE/name;path.write_text('\n'.join(k+'='+str(v) for k,v in values.items())+'\n');path.chmod(0o600);return str(path)
    token=secrets.token_hex(32);password=secrets.token_hex(24);ids=[]
    host.API_URL='http://127.0.0.1:'+str(host.free_port())
    port=host.API_URL.rsplit(':',1)[1]
    call=lambda path,method='GET',data=None:host.api(path,token=token,method=method,data=data)
    def launch(name,environment,cmd):
        host.docker('run','--detach','--name',name,'--label','ai-team.stage7=true','--network','container:'+NAMES[2],
            '--read-only','--user','10001:10001','--cap-drop','ALL','--security-opt','no-new-privileges:true',
            '--tmpfs','/tmp','--memory','512m','--env-file',environment,'ai-team-app:0.7.0',*cmd)
    def sql_script(script):
        return host.docker('exec','-i',NAMES[1],'python','-',input=script.encode()).stdout.decode().strip()
    def terminal(id):
        row=call('/stories/'+id)
        if row['status']=='FAILED':raise AssertionError('Fixture failed: '+str(row['error_type']))
        return row if row['status'] in ['BLOCKED','PLAN_REVIEW'] else None
    def reject(path,data):
        try:call(path,'POST',data);return False
        except urllib.error.HTTPError as exc:return exc.code==409
    try:
        for name in NAMES:
            if host.docker('inspect',name,check=False).returncode==0:raise RuntimeError('Fixture exists; refuse overwrite')
        host.docker('create','--name',NAMES[2],'--label','ai-team.stage7=true','--network','ai-team_backend',
                    '--publish',f'127.0.0.1:{port}:8000','--memory','512m',
                    '--env-file',envfile('postgres.env',{'POSTGRES_PASSWORD':password}),PGIMAGE)
        host.docker('network','connect','ai-team_frontend',NAMES[2]);host.docker('start',NAMES[2])
        wait(lambda:host.docker('exec',NAMES[2],'pg_isready','-U','postgres',check=False).returncode==0)
        host.docker('exec','-i',NAMES[2],'psql','-U','postgres','-v','ON_ERROR_STOP=1',input=(f"CREATE ROLE ai_team LOGIN PASSWORD '{password}';CREATE ROLE ai_team_runtime LOGIN PASSWORD '{password}';CREATE DATABASE ai_team OWNER ai_team;").encode())
        url=lambda role:f'postgresql://{role}:{password}@127.0.0.1/ai_team'
        host.docker('run','--rm','--network','container:'+NAMES[2],'--env-file',envfile('migrate.env',{'DATABASE_URL':url('ai_team')}),'ai-team-app:0.7.0','python','-m','app.migrate')
        runtime=config['services']['api']['environment']|{'DATABASE_URL':url('ai_team_runtime'),'APP_API_TOKEN':token,'OTEL_EXPORTER_OTLP_ENDPOINT':''}
        launch(NAMES[1],envfile('api.env',runtime),['uvicorn','app.api:app','--host','0.0.0.0','--port','8000','--no-access-log'])
        wait(lambda:call('/health'))
        profile=call('/capabilities')
        check('authenticated_profile_reports_missing_airflow_and_network',not profile['capabilities']['airflow']['supported'] and not profile['capabilities']['external_http']['supported'])
        stories=[
            '[stage7 technical fixture] Создать маленький HTTP-сервис Python: GET /result возвращает value=42 и unit=synthetic-check, GET /health работает. Только стандартная библиотека, синтетический ответ, без БД, внешних данных и расписания. Продукт устанавливается отдельно от команды.',
            '[stage7 technical fixture] Нужен Airflow DAG: ежедневно загружать публичные CSV с https://example.invalid/reports в отдельную базу PostgreSQL. Адрес только маркер теста, реальных сетевых обращений и загрузок не выполнять. CSV UTF-8 имеет ровно два поля: bank_id (целое, уникальное), value (число, NULL запрещён). Полная ежедневная перезагрузка одной таблицы в 03:00 UTC. Авторизация источника не нужна; расчёты и отчёты не требуются. Здесь проверяется выявление отсутствующих возможностей до реализации.'
        ]
        for text in stories:
            request=urllib.request.Request(host.API_URL+'/stories',method='POST',headers={'Authorization':'Bearer '+token,'Content-Type':'application/json','Idempotency-Key':'stage7-'+str(uuid.uuid4())},data=json.dumps({'text':text,'synthetic_only':True,'delivery_mode':'product'}).encode())
            with urllib.request.urlopen(request) as response:ids.append(json.load(response)['story_id'])
        worker=config['services']['worker']['environment']|{'DATABASE_URL':url('ai_team_runtime'),'APP_API_TOKEN':token,'OTEL_EXPORTER_OTLP_ENDPOINT':'','LLM_BACKEND':backend,'WORKER_STORY_IDS':','.join(ids)}
        launch(NAMES[0],envfile('worker.env',worker),['python','-m','app.worker'])
        good=wait(lambda:terminal(ids[0]));bad=wait(lambda:terminal(ids[1]))
        diagnostic={'backend':backend,'supported':good,'unsupported':bad}
        Path('/private/tmp/ai-team-stage7-'+backend+'-'+ids[0]+'-diagnostic.json').write_text(json.dumps(diagnostic,ensure_ascii=False,default=str))
        check('supported_plan_reaches_customer_review',good['status']=='PLAN_REVIEW' and good['feasibility']['status']=='READY')
        check('every_criterion_has_tasks_roles_and_verification',len(good['feasibility']['criteria'])==len(good['pm']['acceptance_criteria']) and all(r['task_keys'] and r['roles'] and r['verification'] for r in good['feasibility']['criteria']))
        check('unsupported_plan_blocked_before_approval',bad['status']=='BLOCKED' and bad['feasibility']['status']=='NEEDS_PLATFORM' and not bad['approved'] and bad['plan_digest'] is None and bad['product'] is None)
        check('technical_gaps_are_not_customer_questions',not bad['questions'])
        check('airflow_schedule_and_network_are_reported',{'airflow','scheduled_jobs','external_http'}<={g['capability'] for g in bad['feasibility']['gaps']})
        check('technical_tasks_and_documentation_created',len(bad['feasibility']['tasks'])>=3 and bool(bad['feasibility']['documentation_url']))
        check('unsupported_approval_rejected',reject('/stories/'+ids[1]+'/approve-plan',{'revision':bad['revision'],'plan_digest':'0'*64}))
        query=lambda statement:sql_script('from app import db\nwith db.connection() as c:\n '+statement)
        query("c.execute(\"UPDATE feasibility_reports SET report=jsonb_set(report,'{registry_digest}',to_jsonb('obsolete'::text)) WHERE story_id=%s\",("+repr(ids[0])+",))")
        check('stale_registry_approval_rejected',reject('/stories/'+ids[0]+'/approve-plan',{'revision':good['revision'],'plan_digest':good['plan_digest']}))
        query("c.execute(\"UPDATE feasibility_reports SET report=jsonb_set(report,'{registry_digest}',to_jsonb(%s::text)) WHERE story_id=%s\",("+repr(profile['digest'])+','+repr(ids[0])+"))")
        # Same-revision checkpoint replay must neither call the model nor duplicate links.
        host.docker('stop',NAMES[0])
        query("db.enqueue(c,"+repr(ids[1])+")")
        host.docker('start',NAMES[0])
        wait(lambda:terminal(ids[1]))
        time.sleep(2)
        repeated=call('/stories/'+ids[1])
        check('checkpoint_replay_keeps_report_links_and_usage',repeated['feasibility']==bad['feasibility'] and repeated['usage']==bad['usage'] and repeated['links']==bad['links'])
        response=call('/stories/'+ids[1]+'/resume','POST',{})
        check('recheck_preserves_inputs_and_increments_revision',response['revision']==bad['revision']+1 and response['text']==bad['text'])
        revised=wait(lambda:terminal(ids[1]))
        check('new_revision_rechecks_and_stays_honest',revised['feasibility']['status']=='NEEDS_PLATFORM' and not revised['questions'])
        report['stories']=ids;report['supported_usage']=good['usage'];report['unsupported_usage']=revised['usage']
        report['no_external_source_requests']=True
        print(json.dumps(report,ensure_ascii=False,indent=2))
        (ROOT/'docs'/('stage7-'+backend+'-check.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    finally:
        for id in ids:
            try:
                call('/stories/'+id+'/cancel','POST',{})
                sql_script('from app.integrations import tracker\ntracker.set_status('+repr(id)+',"CANCELLED")')
            except Exception:pass
        for name in NAMES:
            host.docker('rm','-f','-v',name,check=False)
        shutil.rmtree(PRIVATE)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--backend',choices=['mock','openrouter'],default='mock')
    main(parser.parse_args().backend)
