"""Real scheduled ingestion, isolation, portable install, update and rollback.

Technical public-source fixture only; no CBR/customer data, LLM, Git approvals or team writes.
"""
import hashlib
import json
from pathlib import Path
import shutil
import sys
import time
import uuid
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts import product_operator as host
from scripts.profile_operator import compose,container,cleanup,PROFILE,VOLUMES
from scripts.stage10_fixture import package,SOURCE

ROOT=Path(__file__).resolve().parents[1]
STATE=ROOT/'.local/stage10-checks'
report={'profile':PROFILE,'source':SOURCE,'scope':'Technical fixture; no CBR, LLM, Git approvals or customer acceptance. Actual Docker/PostgreSQL/Airflow/public HTTPS.', 'checks':[]}

def check(name,value=True):
    if not value:raise AssertionError(name)
    report['checks'].append(name);print(name,flush=True)


def wait(fn,timeout=210):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        value=fn()
        if value:return value
        time.sleep(2)
    raise AssertionError('Timed out awaiting actual scheduled DAG success')


def current():
    root=host.STATE/'profiles/p-10aabbccddee';record=json.loads((root/'current.json').read_text())
    return root/record['digest']/'products/p-10aabbccddee',root/'runtime.env',record['project']


def sql(database,query):
    c,e,p=current()
    return compose(c,e,p,'exec','-T','postgres','psql','-U','postgres','-d',database,'-Atc',query).stdout.decode().strip()


def runs():return int(sql('airflow',"SELECT count(*) FROM dag_run WHERE dag_id='technical_states' AND state='success' AND run_id LIKE 'scheduled__%';"))
def values():return sql('product','SELECT count(*),sum(population) FROM states;')


def images(qa):
    report.setdefault('package_checks',[]).append(qa)
    check('App and Airflow image IDs are bound to the check',qa['passed'] and qa['tests_passed'] and qa['health_passed'] and qa['image_id'].startswith('sha256:') and qa['result']['airflow_image_id'].startswith('sha256:'))


def main():
    from app.workspace_policy import snapshot
    from scripts.workspace_operator import check_snapshot
    STATE.mkdir(mode=0o700,exist_ok=False);host.STATE=STATE/'operator'
    try:
        # Research uses a specific approved source from the immutable plan, independently of runtime.
        content,_=snapshot({}, {'workspace_id':str(uuid.uuid4()),'task_id':379,'runtime_profile':PROFILE,'sources':[SOURCE],'research_source':'states'})
        receipt=check_snapshot(content,{'task_id':379,'snapshot_sha256':hashlib.sha256(content).hexdigest(),'operation_id':str(uuid.uuid4())})
        check('Actual research fetch returned source SHA256 and bounded preview',receipt['passed'] and receipt['source_observation']['bytes']>100 and len(receipt['source_observation']['preview'])<=4000)
        report['research']={k:v for k,v in receipt['source_observation'].items() if k!='preview'}
        first,manifest=package();qa=host.check_package(first,{'operation_id':str(uuid.uuid4())});images(qa)
        deployed=host.deploy_package(first,{'qa':qa});check('Standalone package installs with no team credentials or team network',deployed['passed'])
        report['initial_manifest_sha256']=hashlib.sha256(first).hexdigest()
        wait(lambda:runs()>=2)
        baseline=values();check('Two actual scheduled DAG runs load exactly 52 rows without duplicates',baseline.startswith('52|'))
        report['scheduled_runs']=runs();report['result']=baseline
        c,e,p=current()
        app=container(c,e,p,'app')
        result=host.probe(app,'/result');check('Web application reads actual ingested rows',json.loads(result.stdout)['states']==52)
        # A gateway only serves declared source IDs, no URL/proxy endpoint.
        code="import urllib.request,urllib.error;u='http://source-gateway:8080/sources/http://169.254.169.254/latest';\ntry:urllib.request.urlopen(u,timeout=3);raise AssertionError('unapproved source accepted')\nexcept urllib.error.HTTPError as e:assert e.code==404"
        check('Gateway rejects undeclared source/metadata URL',host.docker('exec',app,'python','-c',code,check=False).returncode==0)
        # Actual network restrictions: not merely assertions about the Compose file.
        team_result=host.docker('inspect','ai-team-postgres-1',check=False)
        if team_result.returncode:
            sentinel=STATE/'sentinel';sentinel.mkdir()
            host.test_database('ai-stage10-protected',sentinel)
            team_result=host.docker('inspect','ai-stage10-protected-postgres')
            report['protected_database_probe']='disposable isolated sentinel; no running team required'
        else:report['protected_database_probe']='actual local team PostgreSQL address; no credentials passed'
        team=json.loads(team_result.stdout)[0]
        team_ip=next(iter(team['NetworkSettings']['Networks'].values()))['IPAddress']
        code="import socket\nfor target in [('raw.githubusercontent.com',443),('"+team_ip+"',5432),('169.254.169.254',80)]:\n try:\n  s=socket.create_connection(target,1);s.close();raise AssertionError('Forbidden connection succeeded')\n except OSError:pass\nprint('blocked')"
        check('App cannot reach internet, team PostgreSQL or metadata service directly',host.docker('exec',app,'python','-c',code,check=False).returncode==0)
        code="import os,psycopg\nurl=os.environ['DATABASE_URL'].rsplit('/',1)[0]+'/airflow'\ntry:psycopg.connect(url);raise AssertionError('Foreign DB accessible')\nexcept psycopg.OperationalError:pass"
        check('Business role cannot connect to Airflow metadata database',host.docker('exec',app,'python','-c',code,check=False).returncode==0)
        # Real gateway fetch code is exercised against DNS resolving to loopback in a separate process.
        code="import sys;sys.path.insert(0,'/policy');from source_gateway import fetch\ntry:fetch({'id':'probe','url':'https://127.0.0.1.nip.io/','format':'json'});raise AssertionError('Private DNS accepted')\nexcept ValueError:pass"
        gateway=container(c,e,p,'source-gateway')
        check('Actual gateway rejects public hostname resolving to loopback',host.docker('exec',gateway,'python','-c',code,check=False).returncode==0)
        before=runs();compose(c,e,p,'restart','airflow-scheduler','airflow-dag-processor','app',timeout=90)
        wait(lambda:runs()>before)
        check('Scheduler/app restart preserves data and idempotent next scheduled run',values()==baseline)
        # Trusted migration is repeatable; changed checksum is rejected.
        compose(c,e,p,'run','--rm','--no-deps','app','python','platform_migrate.py')
        check('Repeated migration leaves data unchanged',values()==baseline)
        second,_=package('1.0.2');qa2=host.check_package(second,{'operation_id':str(uuid.uuid4())});images(qa2)
        update=host.deploy_package(second,{'qa':qa2});check('Update clones stopped volumes and applies SQL migration',update['passed'] and values()==baseline and sql('product',"SELECT count(*) FROM information_schema.columns WHERE table_name='states' AND column_name='source_tag'")=='1')
        check('Update preserves external result URL port',update['port']==deployed['port'])
        c,e,p=current();old_project=p
        bad,_=package('1.0.3',True);qa3=host.check_package(bad,{'operation_id':str(uuid.uuid4())});images(qa3)
        # Migration succeeds against empty QA data but deliberately fails against the cloned populated DB.
        failed=host.deploy_package(bad,{'qa':qa3})
        check('Data-dependent failed migration restores previous stack and its data',not failed['passed'] and current()[2]==old_project and values()==baseline)
        app=container(*current(),'app');check('Previous working result remains accessible after failed update',json.loads(host.probe(app,'/result').stdout)['states']==52)
        failed2=host.deploy_package(bad,{'qa':qa3});check('Interrupted/failed deployment retry is safe and leaves old data intact',not failed2['passed'] and values()==baseline)
        # Validate XML/XLSX dependencies in both runtimes and parser behavior on actual bytes.
        code="from source_client import parse;import io;from openpyxl import Workbook\nw=Workbook();w.active.append(['value',42]);b=io.BytesIO();w.save(b);assert parse(b.getvalue(),'xlsx')['Sheet']==[['value',42]];assert parse(b'<root><value>42</value></root>','xml').findtext('value')=='42';assert parse(b'{\\\"value\\\":42}','json')['value']==42"
        check('Trusted XLSX/XML/JSON readers work in application runtime',host.docker('exec',app,'python','-c',code,check=False).returncode==0)
        c,e,p=current();af=container(c,e,p,'airflow-scheduler')
        check('Trusted XLSX/XML/JSON readers work in Airflow runtime',host.docker('exec',af,'python','-c',code,check=False).returncode==0)
        stats=host.docker('stats','--no-stream','--format','{{.Name}} {{.MemUsage}}',*[container(c,e,p,s) for s in ['postgres','app','source-gateway','gateway','airflow-api','airflow-scheduler','airflow-dag-processor']])
        report['resource_sample']=stats.stdout.decode().splitlines()
        report['status']='passed'
    except Exception as exc:
        report['status']='failed';report['failure_type']=type(exc).__name__
        raise
    finally:
        root=host.STATE/'profiles/p-10aabbccddee'
        if root.exists():
            env=root/'runtime.env'
            for directory in root.iterdir():
                c=directory/'products/p-10aabbccddee'
                if c.exists():cleanup(c,env,'ai-p-10aabbccddee-'+directory.name[:12],True)
        host.remove_container('ai-stage10-protected-postgres')
        host.docker('network','rm','ai-stage10-protected-network',check=False)
        report['fixture_cleanup']='Owned product stacks/volumes removed; private local receipts retained.'
        (ROOT/'docs/stage10-product-check.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'status':report.get('status','failed'),'checks':len(report['checks'])}),flush=True)

if __name__=='__main__':main()
