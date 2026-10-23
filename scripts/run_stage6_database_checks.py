"""Verify shared product PostgreSQL with empty temporary databases; no warehouse/data."""
import json
from pathlib import Path
import shutil
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts import product_operator as host
ROOT=Path(__file__).resolve().parents[1]
STATE=ROOT/'.local/stage6-database-check'
CONTAINER='ai-products-infra-postgres-1'
VOLUME='ai-products-infra_product-pgdata'
KEYS=['p-6e0000000002','p-6e0000000003']


def main():
    if host.docker('inspect',CONTAINER,check=False).returncode==0 or host.docker('volume','inspect',VOLUME,check=False).returncode==0:
        raise SystemExit('Existing shared product infrastructure: refuse fixture overwrite')
    if STATE.exists():raise SystemExit('Fixture private directory exists; inspect first')
    host.STATE=STATE;report={'kind':'empty isolated database/role check; no data loaded','checks':[]}
    try:
        for key in KEYS:host.provision_database(key)
        for key,other in [(KEYS[0],KEYS[1]),(KEYS[1],KEYS[0])]:
            code="""import os,psycopg
from psycopg.conninfo import conninfo_to_dict
values=conninfo_to_dict(os.environ['DATABASE_URL'])
with psycopg.connect(**values,connect_timeout=5) as c:
    assert c.execute('SELECT rolsuper,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname=current_user').fetchone()==(False,False,False)
    assert c.execute(\"SELECT count(*) FROM information_schema.tables WHERE table_schema='public'\").fetchone()[0]==0
try:
    psycopg.connect(**(values|{'dbname':'OTHER'}),connect_timeout=5)
except psycopg.OperationalError:
    pass
else:
    raise AssertionError('Other product database is accessible')
print('role and database isolation verified')
""".replace('OTHER',other.replace('-','_'))
            host.docker('run','--rm','--network','ai-products','--read-only','--cap-drop','ALL',
                '--env-file',str(STATE/'credentials'/(key+'.env')),'ai-team-app:0.7.0','python','-c',code)
            report['checks'].append(key+': no admin rights, empty own database, other database rejected')
        report['status']='passed'
    finally:
        host.remove_container(CONTAINER)
        info=host.docker('volume','inspect',VOLUME,check=False)
        if info.returncode==0:
            if json.loads(info.stdout)[0].get('Labels',{}).get('ai-team.product-managed')!='true':raise RuntimeError('Foreign volume')
            host.docker('volume','rm',VOLUME)
        if STATE.exists():shutil.rmtree(STATE)
        (ROOT/'docs/stage6-database-check.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False))

if __name__=='__main__':main()
