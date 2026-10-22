"""Operator orchestration; restricted containers perform every query under their own role."""
import json
import subprocess

def inside(service,source):
    result=subprocess.run(['docker','compose','exec','-T',service,'python','-'],input=source,text=True,capture_output=True,timeout=90)
    if result.returncode:raise RuntimeError(service+' security check failed: '+result.stdout+result.stderr)
    return json.loads(result.stdout)

checks=inside('sandbox',r'''
import json,os,socket
import psycopg
from pathlib import Path
checks=[]
for query in ['DELETE FROM synthetic.fact_loan_daily','UPDATE synthetic.fact_loan_daily SET dpd=0',
              'CREATE TABLE public.forbidden(id int)','SELECT * FROM public.stories','SELECT * FROM pg_authid']:
    try:
        with psycopg.connect(os.environ['SANDBOX_DATABASE_URL']) as conn:conn.execute(query)
    except psycopg.errors.InsufficientPrivilege:checks.append('denied: '+query)
    else:raise AssertionError('DB permission unexpectedly allowed')
assert not Path('/var/run/docker.sock').exists()
assert not any(os.getenv(k) for k in ['DATABASE_URL','OPENROUTER_API_KEY','GITHUB_RUNTIME_TOKEN','OPENPROJECT_SA_TOKEN','BOOKSTACK_RUNTIME_TOKEN_SECRET'])
checks.append('sandbox has no application/integration credentials or Docker socket')
try:socket.create_connection(('1.1.1.1',443),timeout=3)
except OSError:checks.append('sandbox external egress unavailable')
else:raise AssertionError('Unexpected external egress')
try:socket.getaddrinfo('git-broker',8000)
except socket.gaierror:checks.append('sandbox cannot resolve isolated Git broker')
else:raise AssertionError('Unexpected broker network access')
print(json.dumps(checks))
''')
checks+=inside('worker',r'''
import json,os,uuid,time
import httpx
checks=[]
assert not os.getenv('SANDBOX_DATABASE_URL') and not os.getenv('GITHUB_RUNTIME_TOKEN')
checks.append('worker has neither sandbox DB login nor GitHub token')
base=os.environ['SANDBOX_URL']
headers={'Authorization':'Bearer '+os.environ['SANDBOX_API_TOKEN']}
def run(sql):return httpx.post(base+'/execute',headers=headers,json={'run_id':str(uuid.uuid4()),'attempt':1,'select_sql':sql},timeout=15)
assert httpx.post(base+'/execute',json={'run_id':str(uuid.uuid4()),'attempt':1,'select_sql':'SELECT 1'}).status_code==401
for sql in ['DROP TABLE synthetic.fact_loan_daily','SELECT pg_read_file(\'/etc/passwd\')','SELECT pg_sleep(1)','SELECT * FROM public.stories','SELECT 1; SELECT 2']:
    assert run(sql).status_code==422
checks.append('AST denies DDL, system/file/sleep functions, private tables and multiple statements')
assert run('SELECT a.date FROM synthetic.dim_calendar a CROSS JOIN synthetic.dim_calendar b').status_code==422
checks.append('1000-row result cap enforced')
started=time.monotonic()
assert run('SELECT count(*) FROM synthetic.dim_calendar a CROSS JOIN synthetic.dim_calendar b CROSS JOIN synthetic.dim_calendar c CROSS JOIN synthetic.dim_calendar d').status_code==422
elapsed=time.monotonic()-started
assert elapsed<12
checks.append('expensive allowed SELECT cancelled by database statement timeout')
print(json.dumps(checks))
''')
print(json.dumps({'checks':checks,'passed':True},ensure_ascii=False,indent=2))
