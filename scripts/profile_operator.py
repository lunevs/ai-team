"""Host-only execution of the fixed airflow-postgres-v1 contract.

Compose is reconstructed/verified by inspect_package, never accepted from generated code.
Updates clone stopped volumes into a new project and retain the old stack for rollback.
"""
import base64
import hashlib
import json
from pathlib import Path
import re
import secrets
import time
import uuid
from app.product_profiles import PROFILE

VOLUMES=['postgres-data','product-data','airflow-logs']

def host():
    from scripts import product_operator
    return product_operator


def private_env(path,port=None):
    if path.exists():return
    content='PRODUCT_PORT='+str(port or host().free_port())+'\n'
    for key in ['POSTGRES_ADMIN_PASSWORD','PRODUCT_DB_PASSWORD','AIRFLOW_DB_PASSWORD','AIRFLOW_JWT_SECRET','AIRFLOW_ADMIN_PASSWORD']:
        content+=key+'='+secrets.token_hex(32)+'\n'
    content+='AIRFLOW_FERNET_KEY='+base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()+'\n'
    path.write_text(content);path.chmod(0o600)


def compose(context,env,project,*args,**kwargs):
    if not re.fullmatch(r'ai-[a-z0-9-]{1,90}',project):raise ValueError('Invalid project')
    override=context.parent.parent/'images.json'
    return host().docker('compose','--project-name',project,'--env-file',str(env),'-f',str(context/'docker-compose.yml'),*(['-f',str(override)] if override.exists() else []),*args,**kwargs)



def pin_images(context,app,airflow):
    for value in [app,airflow]:
        if not re.fullmatch(r'sha256:[a-f0-9]{64}',value):raise host().OperationFailed('PolicyRejected')
    data={'services':{'app':{'image':app},**{s:{'image':airflow} for s in ['airflow-api','airflow-scheduler','airflow-dag-processor']}}}
    path=context.parent.parent/'images.json';path.write_text(json.dumps(data));path.chmod(0o600)

def container(context,env,project,service):
    value=compose(context,env,project,'ps','-aq',service).stdout.decode().strip()
    if not re.fullmatch(r'[a-f0-9]{12,64}',value):raise host().OperationFailed('DeploymentFailed')
    return value


def initialize(context,env,project):
    compose(context,env,project,'up','-d','--wait','postgres',timeout=90)
    compose(context,env,project,'run','--rm','--no-deps','--user','0','--cap-add','CHOWN','--entrypoint','chown','app','10001:0','/data')
    compose(context,env,project,'run','--rm','--no-deps','--user','0','--cap-add','FOWNER','--entrypoint','chmod','app','770','/data')
    compose(context,env,project,'run','--rm','--no-deps','--user','0','--cap-add','CHOWN','--entrypoint','chown','airflow-api','50000:0','/opt/airflow/logs')
    compose(context,env,project,'run','--rm','--no-deps','app','python','platform_migrate.py',timeout=90)
    compose(context,env,project,'run','--rm','--no-deps','airflow-api','db','migrate',timeout=120)


def start(context,env,project):
    compose(context,env,project,'up','-d',timeout=90)
    name=container(context,env,project,'app');host().health(name)
    # Wait for the execution API, scheduler and DAG parser independently of the app health.
    deadline=time.monotonic()+90
    while True:
        result=compose(context,env,project,'exec','-T','airflow-api','python','-c',"import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/api/v2/monitor/health',timeout=3)",check=False,timeout=10)
        if result.returncode==0:break
        if time.monotonic()>deadline:raise host().OperationFailed('HealthFailed')
        time.sleep(2)
    for service,job in [('airflow-scheduler','SchedulerJob'),('airflow-dag-processor','DagProcessorJob')]:
        deadline=time.monotonic()+90
        while True:
            result=compose(context,env,project,'exec','-T',service,'airflow','jobs','check','--job-type',job,'--local',check=False,timeout=30)
            if result.returncode==0:break
            if time.monotonic()>deadline:raise host().OperationFailed('HealthFailed')
            time.sleep(2)
    errors=compose(context,env,project,'exec','-T','airflow-scheduler','airflow','dags','list-import-errors','--output','json',timeout=30)
    # CLI may emit warnings before JSON, but an actual import failure always has a traceback.
    if 'traceback' in errors.stdout.decode(errors='replace').lower():raise host().OperationFailed('TestsFailed')
    dag=compose(context,env,project,'exec','-T','airflow-scheduler','python','-c',
        "from airflow.models.dagbag import DagBag;b=DagBag('/opt/airflow/dags',include_examples=False);assert b.dags and not b.import_errors,b.import_errors",timeout=60,check=False)
    if dag.returncode:raise host().OperationFailed('TestsFailed')
    return name


def cleanup(context,env,project,volumes=False):
    compose(context,env,project,'down',*(['--volumes'] if volumes else []),'--remove-orphans',timeout=90,check=False)


def check(content,operation):
    import tempfile
    from app.product_policy import inspect_package
    metadata,_=inspect_package(content)
    report={'passed':False,'tests_passed':False,'health_passed':False,'image_id':'','result':{}}
    project='ai-profile-check-'+uuid.UUID(str(operation['operation_id'])).hex[:12]
    with tempfile.TemporaryDirectory(prefix='ai-profile-check-') as temporary:
        directory=Path(temporary);_,context=host().extract(content,directory);env=directory/'runtime.env';private_env(env)
        phase='build'
        try:
            compose(context,env,project,'build',timeout=600)
            tag=f"ai-product-{metadata['product_key']}:{metadata['version']}"
            report['result']['airflow_image_id']=host().docker('image','inspect','--format','{{.Id}}',f"ai-product-airflow-{metadata['product_key']}:{metadata['version']}").stdout.decode().strip()
            report['image_id']=host().docker('image','inspect','--format','{{.Id}}',tag).stdout.decode().strip()
            pin_images(context,report['image_id'],report['result']['airflow_image_id'])
            phase='database_and_migrations'
            initialize(context,env,project)
            phase='unit_tests'
            test=compose(context,env,project,'run','--rm','--no-deps','app','python','-m','unittest','discover','-s','tests','-v',timeout=120,check=False)
            if test.returncode or not re.search(rb'Ran [1-9][0-9]* tests?',test.stderr+test.stdout):raise host().OperationFailed('TestsFailed')
            report['tests_passed']=True
            phase='services_and_dags'
            name=start(context,env,project)
            if host().probe(name,'/result').returncode:raise host().OperationFailed('HealthFailed')
            report.update(passed=True,health_passed=True)
        except host().OperationFailed as exc:
            report['error_type']=exc.kind;report['result']['failed_step']=phase
        finally:cleanup(context,env,project,True)
    return report


def clone_volume(source,target):
    """Copy a stopped managed volume, preserving ownership including PostgreSQL UID."""
    from app.product_policy import BASE_IMAGE
    h=host()
    for name in [source,target]:
        if not re.fullmatch(r'ai-[a-z0-9-]+_(postgres-data|product-data|airflow-logs)',name):raise h.OperationFailed('PolicyRejected')
    info=h.docker('volume','inspect',source)
    if json.loads(info.stdout)[0].get('Labels',{}).get('ai-team.product-managed')!='true':raise h.OperationFailed('PolicyRejected')
    if h.docker('volume','inspect',target,check=False).returncode==0:raise h.OperationFailed('PolicyRejected')
    h.docker('volume','create','--label','ai-team.product-managed=true',target)
    code='''import os,shutil,stat
for name in os.listdir('/source'):shutil.copytree('/source/'+name,'/target/'+name,symlinks=True) if os.path.isdir('/source/'+name) and not os.path.islink('/source/'+name) else shutil.copy2('/source/'+name,'/target/'+name,follow_symlinks=False)
for root,ds,fs in os.walk('/source',followlinks=False):
 for name in ['']+ds+fs:
  src=os.path.join(root,name);dst='/target/'+os.path.relpath(src,'/source');s=os.lstat(src);os.chown(dst,s.st_uid,s.st_gid,follow_symlinks=False)
  if not stat.S_ISLNK(s.st_mode):os.chmod(dst,stat.S_IMODE(s.st_mode))
'''
    h.docker('run','--rm','--network','none','--read-only','--cap-drop','ALL','--cap-add','CHOWN','--cap-add','DAC_OVERRIDE','--cap-add','FOWNER','--security-opt','no-new-privileges:true','--memory','128m','--pids-limit','32','--cpus','0.5',
        '--mount',f'type=volume,source={source},target=/source,readonly','--mount',f'type=volume,source={target},target=/target',BASE_IMAGE,'python','-c',code,timeout=180)


def deploy(content,operation):
    from app.product_policy import inspect_package
    h=host();metadata,_=inspect_package(content);key=metadata['product_key'];digest=hashlib.sha256(content).hexdigest()
    root=h.STATE/'profiles'/key;root.mkdir(parents=True,exist_ok=True,mode=0o700)
    pointer=root/'current.json';old=json.loads(pointer.read_text()) if pointer.exists() else None
    env=root/'runtime.env';private_env(env)
    directory=root/digest;directory.mkdir(exist_ok=True,mode=0o700)
    if not (directory/'products').exists():_,context=h.extract(content,directory)
    else:context=directory/'products'/key
    (directory/'product.zip').write_bytes(content)
    project='ai-'+key+'-'+digest[:12]
    report={'passed':False,'health_passed':False,'image_id':operation['qa']['image_id'],'result':{}}
    oldcontext=root/old['digest']/'products'/key if old else None
    # Both images are verified: the second one is built from a digest-pinned trusted Airflow file.
    appimage=h.docker('image','inspect','--format','{{.Id}}',f"ai-product-{key}:{metadata['version']}").stdout.decode().strip()
    if appimage!=operation['qa']['image_id']:raise h.OperationFailed('PolicyRejected')
    afimage=h.docker('image','inspect','--format','{{.Id}}',f"ai-product-airflow-{key}:{metadata['version']}").stdout.decode().strip()
    if afimage!=operation['qa'].get('result',{}).get('airflow_image_id'):raise h.OperationFailed('PolicyRejected')
    pin_images(context,appimage,afimage)
    journal=directory/'deployment.json';phase='prepare'
    try:
        if old and old['digest']!=digest:
            if journal.exists():
                # An incomplete prior attempt can only own this new, unpublished project.
                cleanup(context,env,project)
                for volume in VOLUMES:
                    target=project+'_'+volume;existing=h.docker('volume','inspect',target,check=False)
                    if existing.returncode==0:
                        if json.loads(existing.stdout)[0].get('Labels',{}).get('ai-team.product-managed')!='true':raise h.OperationFailed('PolicyRejected')
                        h.docker('volume','rm',target)
            journal.write_text(json.dumps({'state':'PREPARING','previous':old['digest']}));journal.chmod(0o600)
            phase='snapshot_stopped_data'
            compose(oldcontext,env,old['project'],'stop',timeout=90)
            for volume in VOLUMES:clone_volume(old['project']+'_'+volume,project+'_'+volume)
        phase='database_and_migrations'
        if not old or old['digest']!=digest:initialize(context,env,project)
        phase='services_and_dags'
        name=start(context,env,project)
        if h.probe(name,'/result').returncode:raise h.OperationFailed('HealthFailed')
        port=int(next(line.split('=',1)[1] for line in env.read_text().splitlines() if line.startswith('PRODUCT_PORT=')))
        record={'digest':digest,'project':project,'version':metadata['version'],'previous':old['digest'] if old and old['digest']!=digest else (old or {}).get('previous')}
        temporary=pointer.with_suffix('.tmp');temporary.write_text(json.dumps(record));temporary.chmod(0o600);temporary.replace(pointer)
        journal.write_text(json.dumps({'state':'ACTIVE','digest':digest}));journal.chmod(0o600)
        report.update(passed=True,health_passed=True,port=port)
    except h.OperationFailed as exc:
        report['error_type']=exc.kind;report['result']['failed_step']=phase
        if not old or old['digest']!=digest:
            cleanup(context,env,project)
            if old:
                start(oldcontext,env,old['project']);report['result']['previous_version_restored']=True
    return report


def stop_deployed(key,expected_image):
    if not re.fullmatch(r'p-[a-f0-9]{12}',key):raise host().OperationFailed('PolicyRejected')
    root=host().STATE/'profiles'/key;pointer=root/'current.json'
    if not pointer.exists():return False
    record=json.loads(pointer.read_text());context=root/record['digest']/'products'/key;env=root/'runtime.env'
    name=container(context,env,record['project'],'app')
    actual=host().docker('inspect','--format','{{.Image}}',name).stdout.decode().strip()
    if actual==expected_image:compose(context,env,record['project'],'stop',timeout=90)
    return True
