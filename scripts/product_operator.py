"""Host-only product operator. Docker credentials/socket never enter agent containers.

Run from repository root: python3 scripts/product_operator.py --once
A supervised long-running instance uses --watch. Logs contain identities/status only.
"""
import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import urllib.error
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.product_policy import inspect_package

ROOT=Path(__file__).resolve().parents[1]
STATE=ROOT/'.local/product-operator'
API_URL='http://127.0.0.1:8000'
LOGGER=None

class OperationFailed(Exception):
    def __init__(self,kind):self.kind=kind


def command(args,*,timeout=300,input=None,check=True):
    # Drain bounded pipes so a generated test cannot exhaust host RAM/disk with logs.
    import selectors
    process=None
    try:
        process=subprocess.Popen(args,stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        if input is not None:process.stdin.write(input);process.stdin.close()
        selector=selectors.DefaultSelector();selector.register(process.stdout,selectors.EVENT_READ,'out');selector.register(process.stderr,selectors.EVENT_READ,'err')
        buffers={'out':bytearray(),'err':bytearray()};deadline=time.monotonic()+timeout
        while selector.get_map():
            if time.monotonic()>deadline:raise OperationFailed('OperatorFailed')
            for key,_ in selector.select(timeout=min(0.25,max(0,deadline-time.monotonic()))):
                chunk=os.read(key.fileobj.fileno(),65536)
                if not chunk:selector.unregister(key.fileobj);continue
                buffers[key.data].extend(chunk)
                if sum(map(len,buffers.values()))>4_000_000:raise OperationFailed('OperatorFailed')
        result=subprocess.CompletedProcess(args,process.wait(timeout=max(0.1,deadline-time.monotonic())),bytes(buffers['out']),bytes(buffers['err']))
    except (subprocess.TimeoutExpired,OSError):raise OperationFailed('OperatorFailed') from None
    finally:
        if process and process.poll() is None:process.kill();process.wait()
        if 'selector' in locals():selector.close()
    if check and result.returncode:raise OperationFailed('OperatorFailed')
    return result


def docker(*args,**kwargs):return command(['docker',*args],**kwargs)


def environment():
    values={}
    for line in (ROOT/'.env').read_text().splitlines():
        if '=' in line and not line.startswith('#'):
            key,value=line.split('=',1);values[key]=value
    return values


def api(path,*,token,method='POST',data=None,binary=False):
    request=urllib.request.Request(API_URL+path,headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'},
        data=json.dumps(data or {}).encode() if method=='POST' else None,method=method)
    with urllib.request.urlopen(request,timeout=40) as response:
        raw=response.read(2_000_001)
    if len(raw)>2_000_000:raise OperationFailed('PolicyRejected')
    return raw if binary else json.loads(raw)


def extract(content,directory):
    metadata,files=inspect_package(content)
    destination=directory/'products'/metadata['product_key']
    destination.mkdir(parents=True,mode=0o700)
    for name,data in files.items():
        path=destination/name;path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(data)
    return metadata,destination


def restricted(image,*,name,network='none',volume=None,env_file=None,port=None,detach=False,entry=None,persistent=False):
    args=['run','--name',name,'--label','ai-team.product-managed=true',
        '--user','10001:10001','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges:true',
        '--pids-limit','64','--cpus','1','--memory','512m','--memory-swap','512m',
        '--tmpfs','/tmp:rw,nosuid,nodev,size=64m,mode=1777','--network',network,
        '--log-driver','json-file','--log-opt','max-size=5m','--log-opt','max-file=2']
    if volume:args+=['--mount',f'type=volume,source={volume},target=/data']
    else:args+=['--tmpfs','/data:rw,nosuid,nodev,size=16m,uid=10001,gid=10001']
    if env_file:args+=['--env-file',str(env_file)]
    if port:args+=['--publish',f'127.0.0.1:{port}:8000']
    if persistent:args+=['--restart','unless-stopped']
    if detach:args+=['--detach']
    if entry:args+=['--entrypoint',entry]
    return args+[image]


def remove_container(name):
    result=docker('inspect','--format','{{ index .Config.Labels "ai-team.product-managed" }}',name,check=False)
    if result.returncode==0:
        if result.stdout.strip()!=b'true':raise OperationFailed('PolicyRejected')
        docker('rm','--force',name)


def probe(name,path='/health'):
    # Only trusted probe code is passed via -c; product content is never a host command.
    code="import urllib.request; r=urllib.request.urlopen('http://127.0.0.1:8000"+path+"',timeout=2); assert r.status==200; print(r.read(65537).decode())"
    return docker('exec',name,'python','-c',code,timeout=5,check=False)


def health(name):
    deadline=time.monotonic()+45
    while time.monotonic()<deadline:
        if probe(name).returncode==0:return
        time.sleep(1)
    raise OperationFailed('HealthFailed')


def test_database(name,directory):
    import secrets
    from app.product_policy import BASE_IMAGE
    pg=name+'-postgres';net=name+'-network';password=secrets.token_hex(24)
    image='postgres:17.6-alpine@sha256:ef257d85f76e48da1c64832459b59fcaba1a4dac97bf5d7450c77753542eee94'
    docker('network','create','--internal','--label','ai-team.product-managed=true',net)
    private=directory/'postgres.env';private.write_text('POSTGRES_PASSWORD='+password+'\n');private.chmod(0o600)
    docker('run','--detach','--name',pg,'--label','ai-team.product-managed=true','--network',net,
        '--env-file',str(private),'--memory','256m','--cpus','0.5','--pids-limit','64',
        '--tmpfs','/var/lib/postgresql/data:rw,size=128m',image)
    deadline=time.monotonic()+30
    while docker('exec',pg,'pg_isready','-h','127.0.0.1','-U','postgres',check=False).returncode:
        if time.monotonic()>deadline:raise OperationFailed('TestsFailed')
        time.sleep(0.5)
    sql=f"CREATE ROLE product LOGIN PASSWORD '{password}' NOSUPERUSER NOCREATEDB NOCREATEROLE;CREATE DATABASE product OWNER product;REVOKE CONNECT ON DATABASE product FROM PUBLIC;GRANT CONNECT ON DATABASE product TO product;"
    docker('exec','-i',pg,'psql','-U','postgres','-v','ON_ERROR_STOP=1',input=sql.encode())
    env=directory/'product.env';env.write_text(f'DATABASE_URL=postgresql://product:{password}@{pg}/product\nOUTPUT_DIR=/data\n');env.chmod(0o600)
    return net,env


def check_package(content,operation):
    metadata,_=inspect_package(content)
    if metadata.get('runtime_profile')=='airflow-postgres-v1':
        from scripts.profile_operator import check
        return check(content,operation)
    report={'passed':False,'tests_passed':False,'health_passed':False,'image_id':'','result':{}}
    name='ai-product-check-'+str(operation['operation_id'])[:12]
    try:
        with tempfile.TemporaryDirectory(prefix='ai-product-build-') as temporary:
            metadata,context=extract(content,Path(temporary))
            tag='ai-product-'+metadata['product_key']+':'+metadata['version']
            result=docker('build','--label','ai-team.product-managed=true','--tag',tag,str(context),timeout=300,check=False)
            if result.returncode:raise OperationFailed('BuildFailed')
            image=docker('image','inspect','--format','{{.Id}}',tag).stdout.decode().strip()
            if not re.fullmatch('sha256:[a-f0-9]{64}',image):raise OperationFailed('PolicyRejected')
            report['image_id']=image
            test=command(['docker',*restricted(image,name=name), 'python','-m','unittest','discover','-s','tests','-v'],timeout=90,check=False)
            if test.returncode or not re.search(rb'Ran [1-9][0-9]* tests?',test.stderr):raise OperationFailed('TestsFailed')
            report['tests_passed']=True
            remove_container(name)
            smoke_network,smoke_env=test_database(name,Path(temporary)) if metadata['needs_database'] else ('none',None)
            if metadata['kind']=='service':
                command(['docker',*restricted(image,name=name,detach=True,network=smoke_network,env_file=smoke_env)],timeout=20)
                health(name)
                response=probe(name,'/result')
                if response.returncode:raise OperationFailed('HealthFailed')
                try:
                    value=json.loads(response.stdout)
                    if len(response.stdout)<=8000:report['result']=value if isinstance(value,dict) and len(value)<=8 else {'value':value}
                except (ValueError,TypeError):pass
            else:
                wrapper="import runpy,time;runpy.run_path('app.py',run_name='__main__');open('/tmp/job-complete','w').close();time.sleep(600)"
                command(['docker',*restricted(image,name=name,network=smoke_network,env_file=smoke_env,detach=True),'python','-c',wrapper],timeout=20)
                deadline=time.monotonic()+90
                while docker('exec',name,'python','-c',"import pathlib;assert pathlib.Path('/tmp/job-complete').exists()",check=False).returncode:
                    if time.monotonic()>deadline:raise OperationFailed('TestsFailed')
                    time.sleep(0.5)
            report['health_passed']=True
            from scripts.product_verifier import verify
            verify(sys.modules[__name__],name,operation,report,metadata['kind'])
            report['passed']=True
    except OperationFailed as exc:report['error_type']=exc.kind
    except (ValueError,KeyError,TypeError):report['error_type']='PolicyRejected'
    finally:
        remove_container(name)
        remove_container(name+'-postgres')
        net=docker('network','inspect',name+'-network',check=False)
        if net.returncode==0:
            if json.loads(net.stdout)[0].get('Labels',{}).get('ai-team.product-managed')!='true':raise OperationFailed('PolicyRejected')
            docker('network','rm',name+'-network')
    return report


def network():
    exists=docker('network','inspect','ai-products',check=False)
    if exists.returncode:
        docker('network','create','--internal','--label','ai-team.product-managed=true','ai-products')
    else:
        data=json.loads(exists.stdout)[0]
        if not data['Internal'] or data.get('Labels',{}).get('ai-team.product-managed')!='true':raise OperationFailed('PolicyRejected')


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));return sock.getsockname()[1]


def product_env(key):
    directory=STATE/'credentials';directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    path=directory/(key+'.env')
    if not path.exists():path.write_text('OUTPUT_DIR=/data\n');path.chmod(0o600)
    allowed={'OUTPUT_DIR','DATABASE_URL'}
    for line in path.read_text().splitlines():
        if line and not line.startswith('#') and ('=' not in line or line.split('=',1)[0] not in allowed):raise OperationFailed('PolicyRejected')
    return path


def gateway(name,target,port):
    from app.product_policy import BASE_IMAGE,proxy_code
    remove_container(name)
    args=restricted(BASE_IMAGE,name=name,network='bridge',port=port,persistent=True)
    for flag,value in [('--memory','64m'),('--memory-swap','64m'),('--cpus','0.25'),('--pids-limit','32')]:args[args.index(flag)+1]=value
    args[0]='create'
    command(['docker',*args,'python','-c',proxy_code(target)])
    docker('network','connect','ai-products',name)
    docker('start',name)


def volume_copy(source,target):
    from app.product_policy import BASE_IMAGE
    code="import shutil,os;shutil.copytree('/source','/data',dirs_exist_ok=True,symlinks=True);[(os.chown(os.path.join(root,n),10001,10001,follow_symlinks=False)) for root,ds,fs in os.walk('/data',followlinks=False) for n in ds+fs];os.chown('/data',10001,10001)"
    docker('run','--rm','--network','none','--read-only','--cap-drop','ALL','--cap-add','CHOWN',
        '--security-opt','no-new-privileges:true','--memory','128m','--cpus','0.5','--pids-limit','32',
        '--mount',f'type=volume,source={source},target=/source,readonly',
        '--mount',f'type=volume,source={target},target=/data',BASE_IMAGE,'python','-c',code,timeout=120)


def provision_database(key):
    import secrets
    if not re.fullmatch(r'p-[a-f0-9]{12}',key):raise OperationFailed('PolicyRejected')
    network();STATE.mkdir(parents=True,exist_ok=True,mode=0o700)
    private=STATE/'infrastructure.env'
    if not private.exists():private.write_text('PRODUCTS_POSTGRES_PASSWORD='+secrets.token_hex(32)+'\n');private.chmod(0o600)
    container='ai-products-infra-postgres-1'
    prior=docker('inspect',container,check=False)
    if prior.returncode==0 and json.loads(prior.stdout)[0]['Config']['Labels'].get('ai-team.product-managed')!='true':raise OperationFailed('PolicyRejected')
    compose=['compose','--env-file',str(private),'-f',str(ROOT/'infra/products/docker-compose.yml')]
    docker(*compose,'up','-d','--wait','postgres',timeout=120)
    role=key.replace('-','_');password=secrets.token_hex(32)
    query=f"SELECT count(*) FROM pg_roles WHERE rolname='{role}';"
    count=docker(*compose,'exec','-T','postgres','psql','-U','postgres','-Atc',query).stdout.strip()
    if count!=b'0':raise OperationFailed('PolicyRejected') # never silently rotate existing credentials
    sql=f"CREATE ROLE {role} LOGIN PASSWORD '{password}' NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;\nCREATE DATABASE {role} OWNER {role};\nREVOKE CONNECT ON DATABASE {role} FROM PUBLIC;\nGRANT CONNECT ON DATABASE {role} TO {role};\n"
    docker(*compose,'exec','-T','postgres','psql','-U','postgres','-v','ON_ERROR_STOP=1',input=sql.encode())
    path=product_env(key)
    with path.open('a') as stream:stream.write(f'DATABASE_URL=postgresql://{role}:{password}@products-postgres/{role}\n')
    path.chmod(0o600)


def deploy_package(content,operation):
    metadata,_=inspect_package(content)
    if metadata.get('runtime_profile')=='airflow-postgres-v1':
        from scripts.profile_operator import deploy
        return deploy(content,operation)
    report={'passed':False,'health_passed':False,'image_id':operation['qa']['image_id'],'result':{}}
    metadata,_=inspect_package(content);key=metadata['product_key']
    name='ai-product-'+key;proxy=name+'-gateway';old=name+'-rollback'
    image=report['image_id'];volume='ai-product-data-'+key+'-v'+metadata['version'].replace('.','-')
    network();env_file=product_env(key)
    if metadata['needs_database'] and 'DATABASE_URL=' not in env_file.read_text():
        provision_database(key)
    image_info=docker('image','inspect','--format','{{.Id}}',image,check=False)
    if image_info.returncode or image_info.stdout.decode().strip()!=image:raise OperationFailed('PolicyRejected')
    prior=docker('inspect',name,check=False);previous=None;port=None
    if prior.returncode==0:
        previous=json.loads(prior.stdout)[0]
        if previous['Config']['Labels'].get('ai-team.product-managed')!='true':raise OperationFailed('PolicyRejected')
        if previous['Image']==image:
            if metadata['kind']=='service':
                docker('start',name);health(name)
                proxy_info=docker('inspect',proxy,check=False)
                if proxy_info.returncode==0:
                    bindings=json.loads(proxy_info.stdout)[0]['HostConfig']['PortBindings']
                    port=int(bindings['8000/tcp'][0]['HostPort']);docker('start',proxy)
                else:
                    port=free_port();gateway(proxy,name,port)
            from scripts.product_verifier import verify
            if metadata['kind']=='job':
                job_volume=next(v['Name'] for v in previous['Mounts'] if v['Destination']=='/data')
                command(['docker',*restricted(image,name=name+'-verification',volume=job_volume,detach=True),'python','-c','import time;time.sleep(60)'],timeout=20)
            try:verify(sys.modules[__name__],name if metadata['kind']=='service' else name+'-verification',operation,report,metadata['kind'])
            except OperationFailed as exc:
                report['error_type']=exc.kind
                return report
            finally:
                if metadata['kind']=='job':remove_container(name+'-verification')
            report.update(passed=True,health_passed=True,port=port);return report
        if metadata['needs_database']:
            # Database migrations are not safely reversible generically: keep the running version.
            report['error_type']='DeploymentFailed';return report
        if docker('inspect',old,check=False).returncode==0:raise OperationFailed('PolicyRejected')
        info=docker('inspect',proxy,check=False)
        if info.returncode==0:
            port=int(json.loads(info.stdout)[0]['HostConfig']['PortBindings']['8000/tcp'][0]['HostPort'])
        docker('stop','--time','15',name);docker('rename',name,old)
    existing=docker('volume','inspect',volume,check=False)
    if existing.returncode==0:
        if json.loads(existing.stdout)[0].get('Labels',{}).get('ai-team.product-managed')!='true':raise OperationFailed('PolicyRejected')
    else:docker('volume','create','--label','ai-team.product-managed=true',volume)
    from app.product_policy import BASE_IMAGE
    try:
        if previous:
            source=next(v['Name'] for v in previous['Mounts'] if v['Destination']=='/data')
            if existing.returncode==0:raise OperationFailed('PolicyRejected')
            volume_copy(source,volume)
        else:
            docker('run','--rm','--network','none','--read-only','--cap-drop','ALL','--cap-add','CHOWN',
                '--security-opt','no-new-privileges:true','--mount',f'type=volume,source={volume},target=/data',
                BASE_IMAGE,'chown','10001:10001','/data',timeout=20)
        port=(port or free_port()) if metadata['kind']=='service' else None
        command(['docker',*restricted(image,name=name,network='ai-products',volume=volume,env_file=env_file,detach=metadata['kind']=='service',persistent=metadata['kind']=='service')],timeout=90)
        if metadata['kind']=='service':
            health(name)
            response=probe(name,'/result')
            if response.returncode:raise OperationFailed('HealthFailed')
            # Persist small JSON/scalar results only; other outputs stay behind the product URL.
            try:
                value=json.loads(response.stdout)
                if isinstance(value,dict) and len(value)<=8 and len(response.stdout)<=8000:report['result']=value
            except (ValueError,TypeError):pass
            gateway(proxy,name,port)
            deadline=time.monotonic()+20
            while True:
                try:
                    with urllib.request.urlopen(f'http://127.0.0.1:{port}/health',timeout=3) as r:
                        if r.status!=200:raise OperationFailed('HealthFailed')
                    break
                except (urllib.error.URLError,http.client.RemoteDisconnected,TimeoutError):
                    if time.monotonic()>deadline:raise OperationFailed('HealthFailed')
                    time.sleep(0.5)
        else:
            data=docker('run','--rm','--network','none','--read-only','--user','10001:10001',
                '--cap-drop','ALL','--mount',f'type=volume,source={volume},target=/data,readonly',
                BASE_IMAGE,'python','-c',"import pathlib,json;p=pathlib.Path('/data/result.json');assert p.is_file() and p.stat().st_size<8000;print(p.read_text())")
            value=json.loads(data.stdout)
            report['result']=value if isinstance(value,dict) and len(value)<=8 else {'value':value}
        directory=STATE/'releases'/key/metadata['version']
        directory.mkdir(parents=True,exist_ok=True,mode=0o700)
        (directory/'product.zip').write_bytes(content)
        if not (directory/'products').exists():extract(content,directory)
        from scripts.product_verifier import verify
        if metadata['kind']=='job':
            command(['docker',*restricted(image,name=name+'-verification',network='none',volume=volume,detach=True),'python','-c','import time;time.sleep(60)'],timeout=20)
        try:verify(sys.modules[__name__],name if metadata['kind']=='service' else name+'-verification',operation,report,metadata['kind'])
        finally:
            if metadata['kind']=='job':remove_container(name+'-verification')
        report.update(passed=True,health_passed=True,port=port)
        if previous:remove_container(old) # previous volume/image remain available for rollback
    except (OperationFailed,urllib.error.URLError,http.client.RemoteDisconnected,ValueError,KeyError) as exc:
        report['error_type']=exc.kind if isinstance(exc,OperationFailed) else 'DeploymentFailed'
        remove_container(name)
        if previous:
            docker('rename',old,name);docker('start',name)
            if port:gateway(proxy,name,port)
        else:remove_container(proxy)
    return report


def once():
    config=environment();operator=config.get('PRODUCT_OPERATOR_TOKEN','');token=config.get('APP_API_TOKEN','')
    if not operator or not token:raise SystemExit('Configure local PRODUCT_OPERATOR_TOKEN and APP_API_TOKEN')
    from scripts.workspace_operator import once as workspace_once
    if workspace_once(config):return True
    operation=api('/product-operations/claim',token=operator)
    if operation.get('operation') is None:return False
    content=api('/products/'+operation['id']+'/package',token=token,method='GET',binary=True)
    digest=hashlib.sha256(content).hexdigest()
    if digest!=operation['package']['sha256']:raise OperationFailed('PolicyRejected')
    api('/product-operations/'+operation['id']+'/validate',token=operator,data={'operation_id':operation['operation_id'],'package_sha256':digest,'passed':False})
    try:
        report=check_package(content,operation) if operation['operation']=='check' else deploy_package(content,operation)
    except OperationFailed as exc:report={'passed':False,'error_type':exc.kind}
    report|={'operation_id':operation['operation_id'],'package_sha256':digest}
    # Keep the exact receipt privately for reconciliation after an uncertain HTTP POST.
    STATE.mkdir(parents=True,exist_ok=True,mode=0o700)
    receipt=STATE/(str(operation['operation_id'])+'.json');receipt.write_text(json.dumps(report));receipt.chmod(0o600)
    try:
        result=api('/product-operations/'+operation['id']+'/report',token=operator,data=report)
    except urllib.error.HTTPError as exc:
        if exc.code==409 and operation['operation']=='deploy' and report.get('passed'):
            from scripts.profile_operator import stop_deployed
            if stop_deployed(operation['product_key'],report.get('image_id')):raise
            name='ai-product-'+operation['product_key']
            current=docker('inspect',name,check=False)
            if current.returncode==0 and json.loads(current.stdout)[0]['Image']==report.get('image_id'):
                docker('stop','--time','10',name,check=False)
                docker('stop','--time','10',name+'-gateway',check=False)
        raise
    message=json.dumps({'product':operation['product_key'],'operation':operation['operation'],'status':result['status']})
    if LOGGER:LOGGER.info(message)
    else:print(message,flush=True)
    return True

if __name__=='__main__':
    parser=argparse.ArgumentParser();mode=parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--once',action='store_true');mode.add_argument('--watch',action='store_true')
    args=parser.parse_args()
    if args.once:once()
    else:
        import logging
        from logging.handlers import RotatingFileHandler
        os.umask(0o077)
        STATE.mkdir(parents=True,exist_ok=True,mode=0o700)
        LOGGER=logging.getLogger('product-operator');LOGGER.setLevel(logging.INFO)
        LOGGER.addHandler(RotatingFileHandler(STATE/'operations.log',maxBytes=1_000_000,backupCount=2,encoding='utf-8'))
        while True:
            try:once()
            except (OperationFailed,urllib.error.URLError,ValueError,KeyError):
                LOGGER.warning('Operator unavailable; credentials and model logs withheld')
            time.sleep(5)
