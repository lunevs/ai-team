"""Real isolated product Docker checks. No CBR product/data and no GitHub writes."""
import hashlib
import json
from pathlib import Path
import shutil
import sys
import time
import uuid
from types import SimpleNamespace
import urllib.request
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts import product_operator as operator
from app.product_policy import build_package,inspect_package

ROOT=Path(__file__).resolve().parents[1]
KEY='p-6e0000000001'
STATE=ROOT/'.local/stage6-product-check'
report={'kind':'synthetic fixture; not a customer product','checks':[]}

def check(name,condition):
    if not condition:raise AssertionError(name)
    report['checks'].append({'name':name,'passed':True})


def fixture(version='1.0.1',value=42,job=False):
    app=(ROOT/'app/product_templates/smoke_app.py').read_text().replace("'value': 42","'value': "+str(value))
    if job:
        app="import json,pathlib\ndef calculate():return {'value':42,'unit':'synthetic-check'}\nif __name__=='__main__':pathlib.Path('/data/result.json').write_text(json.dumps(calculate()))\n"
    test=(ROOT/'app/product_templates/smoke_test.py').read_text().replace("'value':42","'value':"+str(value))
    test+='\n    def test_no_team_secrets(self):\n        import os\n        self.assertFalse(any(k in os.environ for k in ("APP_API_TOKEN","OPENROUTER_API_KEY","GITHUB_RUNTIME_TOKEN","PRODUCT_OPERATOR_TOKEN")))\n'
    s=SimpleNamespace(title='Synthetic product protocol check',kind='job' if job else 'service',output_type='scalar',needs_database=False,
        architecture='Independent synthetic HTTP/job protocol fixture; no external data.',operating_notes='No database or data source. Value 42 only.',
        files={'app.py':app,'tests/test_product.py':test})
    return build_package(s,KEY,version,uuid.UUID('6e000000-0000-0000-0000-000000000001'),1)[0]


def main():
    operator.STATE=STATE
    name='ai-product-'+KEY
    # Never overwrite a previous run silently.
    if operator.docker('inspect',name,check=False).returncode==0:raise RuntimeError('Fixture already exists; inspect before repeating')
    try:
        content=fixture();check('reproducible_package',content==fixture())
        metadata,files=inspect_package(content)
        check('portable_files',{'install.md','Dockerfile','docker-compose.yml','app.py','tests/test_product.py'}<=set(files))
        operation={'operation_id':str(uuid.uuid4())}
        verified=operator.check_package(content,operation)
        check('real_build_unittest_and_health',verified['passed'] and verified['tests_passed'] and verified['health_passed'])
        operation['qa']=verified
        deployed=operator.deploy_package(content,operation)
        check('independent_deployment',deployed['passed'])
        port=deployed['port']
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/result',timeout=5) as r:result=json.load(r)
        check('business_result_42',result=={'value':42,'unit':'synthetic-check'})
        info=json.loads(operator.docker('inspect',name).stdout)[0]
        check('only_product_network',set(info['NetworkSettings']['Networks'])=={'ai-products'})
        check('no_team_mounts_or_credentials',all('/workspace' not in m['Destination'] and 'docker.sock' not in m['Destination'] for m in info['Mounts']) and not any('TOKEN=' in e or 'OPENROUTER' in e for e in info['Config']['Env']))
        check('runtime_limits',info['HostConfig']['ReadonlyRootfs'] and info['HostConfig']['Memory']==512*1024*1024 and info['Config']['User']=='10001:10001')
        retry=operator.deploy_package(content,operation)
        check('deployment_retry_is_idempotent',retry['passed'] and retry['port']==port)
        operator.docker('restart',name)
        operator.health(name)
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/result',timeout=5) as r:check('restart_preserves_product',json.load(r)==result)
        # Upgrade stateless/volume product after copying its volume; previous data remains available.
        upgraded=fixture('1.0.2',43)
        verified2=operator.check_package(upgraded,{'operation_id':str(uuid.uuid4())})
        check('new_version_tests',verified2['passed'])
        deployed2=operator.deploy_package(upgraded,{'qa':verified2})
        check('version_upgrade',deployed2['passed'] and deployed2['port']==port)
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/result',timeout=5) as r:check('upgraded_result_43',json.load(r)['value']==43)
        # Clean re-install uses the exported package alone, without source-tree paths.
        exported=STATE/'releases'/KEY/'1.0.2'/'product.zip'
        check('exported_transfer_package',exported.read_bytes()==upgraded)
        transferred=STATE/'transfer'
        meta,context=operator.extract(exported.read_bytes(),transferred)
        env=transferred/'transfer.env';transfer_port=operator.free_port()
        env.write_text('PRODUCT_PORT='+str(transfer_port)+'\nPRODUCT_DATABASE_URL=\n')
        args=['compose','--env-file',str(env),'-p','ai-stage6-transfer','-f',str(context/'docker-compose.yml')]
        try:
            operator.docker(*args,'build',timeout=300)
            operator.docker(*args,'create','app')
            operator.docker(*args,'run','--rm','--no-deps','--user','0','--cap-add','CHOWN','--entrypoint','/usr/bin/chown','app','10001:10001','/data')
            operator.docker(*args,'run','--rm','--no-deps','app','python','-m','unittest','discover','-s','tests','-v')
            operator.docker(*args,'up','-d','--wait',timeout=120)
            with urllib.request.urlopen(f'http://127.0.0.1:{transfer_port}/result',timeout=5) as r:check('clean_install_by_install_md',json.load(r)['value']==43)
            active=operator.docker('compose','ps','--status','running','--services').stdout.decode().splitlines()
            services=[x for x in ['api','worker','ui','git-broker','sandbox'] if x in active]
            try:
                operator.docker('compose','stop','--timeout','10',*services,timeout=60)
                with urllib.request.urlopen(f'http://127.0.0.1:{transfer_port}/result',timeout=5) as r:check('works_with_team_runtime_stopped',json.load(r)['value']==43)
            finally:
                if services:operator.docker('compose','start',*services,timeout=60)
        finally:operator.docker(*args,'down','--volumes',timeout=60)

        check('old_volume_retained',operator.docker('volume','inspect','ai-product-data-'+KEY+'-v1-0-1',check=False).returncode==0)
        operator.remove_container(name+'-gateway');operator.remove_container(name)
        job=fixture('1.0.3',job=True)
        verified3=operator.check_package(job,{'operation_id':str(uuid.uuid4())})
        check('job_tests_and_result_file',verified3['passed'])
        job_result=operator.deploy_package(job,{'qa':verified3})
        check('job_delivers_scalar',job_result['passed'] and job_result['result']=={'value':42,'unit':'synthetic-check'})
        report['status']='passed'
    finally:
        for suffix in ['-gateway','','-rollback']:
            operator.remove_container(name+suffix)
        for version in ['1-0-1','1-0-2','1-0-3']:
            volume='ai-product-data-'+KEY+'-v'+version
            info=operator.docker('volume','inspect',volume,check=False)
            if info.returncode==0:
                if json.loads(info.stdout)[0].get('Labels',{}).get('ai-team.product-managed')!='true':raise RuntimeError('Foreign volume')
                operator.docker('volume','rm',volume)
        if STATE.exists():shutil.rmtree(STATE)
        (ROOT/'docs/stage6-product-check.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False))

if __name__=='__main__':main()
