"""Trusted isolated checks for a persistent source workspace; no arbitrary host commands."""
import hashlib
import json
from pathlib import Path
import uuid
from app.workspace_policy import inspect_snapshot
from app.product_policy import dockerfile

RUNNER='''import pathlib,unittest,json
root=pathlib.Path('/product');suite=unittest.defaultTestLoader.discover('/product/tests') if (root/'tests').exists() else unittest.TestSuite()
result=unittest.TextTestRunner(verbosity=1).run(suite)
print('WORKSPACE_RESULT='+json.dumps({'passed':result.wasSuccessful(),'tests_run':result.testsRun,'tests_present':result.testsRun>0}))
raise SystemExit(0 if result.wasSuccessful() else 1)
'''


def check_snapshot(content,operation,root=None):
    from scripts import product_operator as host
    metadata,files=inspect_snapshot(content)
    if metadata['task_id']!=operation['task_id']:raise ValueError('Snapshot belongs to another task')
    digest=hashlib.sha256(content).hexdigest()
    if digest!=operation['snapshot_sha256']:raise ValueError('Snapshot digest changed')
    if metadata.get('research_source'):
        from app.source_policy import fetch
        source=next(s for s in metadata['sources'] if s['id']==metadata['research_source'])
        try:
            data,info=fetch(source);info['preview']=data[:4000].decode('utf-8',errors='replace') if source['format'] in {'csv','json','xml'} else 'Binary source: format and SHA256 recorded; inspect using trusted product parser.'
            return {'passed':True,'tests_run':0,'tests_present':False,'source_observation':info,'error_type':'','test_output':'Actual research fetch; runtime download remains a separate check.'}
        except (ValueError,OSError):return {'passed':False,'tests_run':0,'tests_present':False,'error_type':'TestsFailed','test_output':'Approved public source unavailable or blocked by network policy.'}
    directory=(root or host.STATE/'workspaces')/str(uuid.UUID(metadata['workspace_id']))/str(metadata['task_id'])/digest
    directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    if any(p.is_symlink() for p in [directory,*directory.parents]):raise ValueError('Linked workspace directory')
    source=directory/'source';source.mkdir(exist_ok=True)
    for name,text in files.items():
        path=source/name
        if any(p.is_symlink() for p in [path,*path.parents]):raise ValueError('Linked source path')
        path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text)
    (source/'Dockerfile').write_text(dockerfile())
    (source/'runtime-requirements.lock').write_bytes(host.ROOT.joinpath('app/product_templates/runtime-requirements.lock').read_bytes())
    profile=metadata.get('runtime_profile','python-locked-v1')
    if profile=='airflow-postgres-v1':
        from app.product_profiles import owned_files,app_lock
        for name,text in owned_files(metadata.get('sources',[])).items():
            path=source/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text)
        (source/'runtime-requirements.lock').write_text(app_lock())
    (directory/'workspace.json').write_text(json.dumps(metadata,ensure_ascii=False))
    name='ai-workspace-check-'+uuid.UUID(str(operation['operation_id'])).hex[:16]
    image=name+':check';report={'passed':False,'tests_run':0,'tests_present':False,'error_type':'','test_output':''}
    try:
        result=host.docker('build','--tag',image,str(source),timeout=600,check=False)
        if result.returncode:
            report['test_output']=(result.stdout+result.stderr).decode(errors='replace')[-12000:]
            raise host.OperationFailed('BuildFailed')
        network,env_file=host.test_database(name,directory) if metadata.get('needs_database') else ('none',None)
        run=host.docker(*host.restricted(image,name=name,entry='python',network=network,env_file=env_file),'-c',RUNNER,timeout=120,check=False)
        report['test_output']=(run.stdout+run.stderr).decode(errors='replace')[-12000:]
        output=run.stdout.decode(errors='replace')
        marker=next((line for line in reversed(output.splitlines()) if line.startswith('WORKSPACE_RESULT=')),None)
        if marker is None:raise host.OperationFailed('TestsFailed')
        actual=json.loads(marker.split('=',1)[1]);report.update({k:actual[k] for k in ['passed','tests_run','tests_present']})
        if run.returncode or not report['passed']:raise host.OperationFailed('TestsFailed')
        if profile=='airflow-postgres-v1' and any(p.startswith('dags/') and p.endswith('.py') for p in files):
            airflow_image=image+'-airflow'
            built=host.docker('build','--file',str(source/'Airflow.Dockerfile'),'--tag',airflow_image,str(source),timeout=600,check=False)
            if built.returncode:raise host.OperationFailed('BuildFailed')
            dag=host.docker('run','--rm','--network','none','--read-only','--user','50000:0','--cap-drop','ALL','--security-opt','no-new-privileges:true','--memory','512m','--pids-limit','64','--tmpfs','/tmp:size=64m','--entrypoint','python','--env','AIRFLOW_CONFIG=/tmp/airflow.cfg','--env','AIRFLOW__CORE__LOAD_EXAMPLES=false','--env','AIRFLOW__LOGGING__BASE_LOG_FOLDER=/tmp/logs','--env','AIRFLOW__CORE__SIMPLE_AUTH_MANAGER_PASSWORDS_FILE=/tmp/passwords.json',airflow_image,'-c',
                "from airflow.models.dagbag import DagBag;b=DagBag('/opt/airflow/dags',include_examples=False);assert not b.import_errors,b.import_errors;assert b.dags",timeout=60,check=False)
            report['test_output']+='\nDAG import check: '+(dag.stdout+dag.stderr).decode(errors='replace')[-4000:]
            report['test_output']=report['test_output'][-12000:]
            if dag.returncode:raise host.OperationFailed('TestsFailed')
    except host.OperationFailed as exc:report['error_type']=exc.kind;report['passed']=False
    finally:
        host.remove_container(name);host.remove_container(name+'-postgres')
        network=host.docker('network','inspect',name+'-network',check=False)
        if network.returncode==0:
            if json.loads(network.stdout)[0].get('Labels',{}).get('ai-team.product-managed')!='true':raise host.OperationFailed('PolicyRejected')
            host.docker('network','rm',name+'-network')
        host.docker('image','rm',image,check=False)
        if 'airflow_image' in locals():host.docker('image','rm',airflow_image,check=False)
    return report


def once(config):
    from scripts import product_operator as host
    operation=host.api('/workspace-operations/claim',token=config['PRODUCT_OPERATOR_TOKEN'])
    if operation.get('operation') is None:return False
    content=host.api('/workspace-operations/'+operation['id']+'/snapshot',token=config['APP_API_TOKEN'],method='GET',binary=True)
    receipt={'operation_id':operation['operation_id'],'snapshot_sha256':operation['snapshot_sha256'],'passed':False}
    host.api('/workspace-operations/'+operation['id']+'/validate',token=config['PRODUCT_OPERATOR_TOKEN'],data=receipt)
    try:receipt.update(check_snapshot(content,operation))
    except (ValueError,KeyError,host.OperationFailed):receipt.update(passed=False,error_type='PolicyRejected')
    directory=host.STATE/'workspace-receipts';directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    path=directory/(str(operation['operation_id'])+'.json');path.write_text(json.dumps(receipt));path.chmod(0o600)
    result=host.api('/workspace-operations/'+operation['id']+'/report',token=config['PRODUCT_OPERATOR_TOKEN'],data=receipt)
    message=json.dumps({'task':operation['task_id'],'operation':'workspace-check','state':result['state']})
    if host.LOGGER:host.LOGGER.info(message)
    else:print(message,flush=True)
    return True
