"""Disposable PostgreSQL for regression and product evidence integration tests."""
import json
from pathlib import Path
import secrets
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts import product_operator as host
from app.product_profiles import POSTGRES_IMAGE
ROOT=Path(__file__).resolve().parents[1]
STATE=ROOT/'.local/stage11-integration'
NAME='ai-stage11-integration-postgres'

def main():
 STATE.mkdir(parents=True,mode=0o700,exist_ok=True)
 password=secrets.token_hex(24)
 def env(name,values):
  path=STATE/name;path.write_text('\n'.join(k+'='+v for k,v in values.items())+'\n');path.chmod(0o600);return str(path)
 mounts=[]
 for folder in ['app','scripts','tests']:
  mounts+=['--mount',f'type=bind,source={ROOT/folder},target=/workspace/{folder},readonly']
 def run(config,*cmd):return host.docker('run','--rm','--network','container:'+NAME,'--env-file',config,*mounts,'ai-team-app:0.7.0',*cmd,timeout=240,check=False)
 try:
  host.docker('run','--detach','--name',NAME,'--label','ai-team.product-managed=true','--network','none','--memory','256m','--env-file',env('postgres.env',{'POSTGRES_PASSWORD':password}),POSTGRES_IMAGE)
  deadline=time.monotonic()+30
  while host.docker('exec',NAME,'pg_isready','-h','127.0.0.1','-U','postgres',check=False).returncode:
   if time.monotonic()>deadline:raise AssertionError('Postgres startup')
   time.sleep(0.5)
  host.docker('exec','-i',NAME,'psql','-U','postgres','-v','ON_ERROR_STOP=1',input=f"CREATE ROLE ai_team LOGIN PASSWORD '{password}';CREATE ROLE ai_team_runtime LOGIN PASSWORD '{password}';CREATE DATABASE ai_team OWNER ai_team;".encode())
  url=lambda role:f'postgresql://{role}:{password}@127.0.0.1/ai_team'
  migration=run(env('migrate.env',{'DATABASE_URL':url('ai_team')}),'python','-m','app.migrate')
  if migration.returncode:raise AssertionError('Disposable migration failed')
  result=run(env('runtime.env',{'DATABASE_URL':url('ai_team_runtime')}),'python','-m','pytest','tests/integration','-q','-p','no:cacheprovider')
  text=(result.stdout+result.stderr).decode();print(text)
  if result.returncode:raise AssertionError('Integration checks failed')
  (ROOT/'docs/stage11-integration-check.json').write_text(json.dumps({'status':'passed','database':'disposable PostgreSQL; no team tables read or changed','output':text.strip()},ensure_ascii=False,indent=2)+'\n')
 finally:host.remove_container(NAME)

if __name__=='__main__':main()
