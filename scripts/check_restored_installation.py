"""Read-only verification of an already isolated restored installation (operator host)."""
import hashlib,json,subprocess
from pathlib import Path
from urllib.parse import quote

ROOT=Path(__file__).resolve().parents[1]
PREFIX='ai-team-dr-5e000002'

def run(*args,input=None):
    p=subprocess.run(args,input=input,capture_output=True,text=True)
    if p.returncode:raise RuntimeError('Recovery verification command failed: '+args[0])
    return p.stdout.strip()

def main():
    recovered=ROOT/'.local'/PREFIX/'.env'
    values=dict(l.split('=',1) for l in recovered.read_text().splitlines() if '=' in l and not l.startswith('#'))
    assert hashlib.sha256(recovered.read_bytes()).digest()==hashlib.sha256((ROOT/'.env').read_bytes()).digest()
    report={'production_replaced':False,'checks':['encrypted deployment configuration restored byte-for-byte'],'postgres':{}}
    controls={
      'ai_team':"SELECT json_build_object('stories',(SELECT count(*) FROM stories),'calls',(SELECT count(*) FROM llm_calls),'executions',(SELECT count(*) FROM execution_runs),'checkpoints',(SELECT count(*) FROM checkpoints),'checksum',(SELECT checksum FROM synthetic.dataset_info WHERE singleton))::text",
      'openproject':"SELECT json_build_object('tasks',(SELECT count(*) FROM work_packages),'users',(SELECT count(*) FROM users))::text"}
    for db,query in controls.items():
        source=json.loads(run('docker','compose','exec','-T','postgres','psql','-U','postgres','-d',db,'-Atc',query))
        restored=json.loads(run('docker','exec','ai-team-stage5-postgres','psql','-U','postgres','-d',db,'-Atc',query))
        assert source==restored,(db,'control mismatch')
        report['postgres'][db]=restored
    report['checks'].append('PostgreSQL stories, paid ledger, checkpoints, synthetic checksum and OpenProject controls match source')
    sql='SELECT count(*) FROM bookstack.entity_page_data;'
    def maria(container,socket):
        return int(run('docker','exec','-i',container,'sh','-c',
            'IFS= read -r MYSQL_PWD; export MYSQL_PWD; exec mariadb --socket='+socket+' -u root -NBe "$1"','check',sql,
            input=values['MARIADB_ROOT_PASSWORD']+'\n'))
    assert maria('ai-team-bookstack-db-1','/run/mysqld/mysqld.sock')==maria('ai-team-stage5-mariadb','/tmp/mysql.sock')
    report['checks'].append('restored MariaDB BookStack pages match source')
    env={'DATABASE_URL':'postgresql://ai_team_runtime:'+quote(values['APP_RUNTIME_DB_PASSWORD'],safe='')+'@stage5-postgres/ai_team?sslmode=verify-full&sslrootcert=/run/secrets/pg-ca.crt',
         'OTEL_EXPORTER_OTLP_ENDPOINT':''}
    private=ROOT/'.local'/PREFIX/'runtime.env';private.write_text('\n'.join(k+'='+v for k,v in env.items())+'\n');private.chmod(0o600)
    code='''import json,hashlib
from dataclasses import replace
from app import db
from app.storage import read_artifact
from app.portability import database_parameters
from psycopg import connect,errors
with db.connection() as c:
    assert c.execute("SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()").fetchone()['ssl']
    runs=c.execute('SELECT id,artifacts FROM execution_runs WHERE artifacts IS NOT NULL').fetchall()
count=0
for run in runs:
    for name,manifest in run['artifacts'].items():read_artifact(run['id'],name,manifest);count+=1
for statement in ['CREATE TABLE public.stage5_forbidden(x int)','DELETE FROM synthetic.fact_loan']:
    try:
        with db.connection() as c:c.execute(statement)
    except errors.InsufficientPrivilege:pass
    else:raise AssertionError('Runtime privileges expanded')
wrong=db.settings.database_url.replace('@stage5-postgres/','@stage5-wrong-host/')
try:connect(**database_parameters(wrong),connect_timeout=5)
except Exception as e:
    assert type(e).__name__=='OperationalError'
else:raise AssertionError('Hostname verification bypassed')
print(json.dumps({'artifacts_verified':count,'tls_verified':True,'wrong_hostname_rejected':True,'runtime_ddl_and_source_delete_denied':True}))
'''
    args=['docker','run','--rm','--network','ai-team_backend','--env-file',str(private),'--read-only','--user','10001:10001','--cap-drop','ALL',
          '-v',str(ROOT)+':/workspace:ro','-v',str(ROOT/'.local/stage5-services/tls/server.crt')+':/run/secrets/pg-ca.crt:ro',
          '-v',PREFIX+'_delivery_artifacts:/data/artifacts:ro','ai-team-app:0.6.0','python','-c',code]
    report.update(json.loads(run(*args)))
    report['checks'].append('external-host TLS verify-full, negative hostname, restored artifact SHA256 and runtime privilege boundary')
    report['status']='passed';(ROOT/'docs/stage5-recovery-check.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False))
if __name__=='__main__':main()
