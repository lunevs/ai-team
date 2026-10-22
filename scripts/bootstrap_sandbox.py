"""Operator-only restricted login. No admin credentials in sandbox service."""
from pathlib import Path
import secrets
import subprocess

path=Path('.env')
values=dict(line.split('=',1) for line in path.read_text().splitlines() if '=' in line and not line.startswith('#'))
new={k:secrets.token_hex(32) for k in ['SANDBOX_DB_PASSWORD','SANDBOX_API_TOKEN','GIT_BROKER_API_TOKEN'] if not values.get(k)}
if new:
 with path.open('a') as file:
  file.write('\n'+''.join(k+'='+v+'\n' for k,v in new.items()))
 path.chmod(0o600);values.update(new)
# psql runs inside existing PostgreSQL, with SQL provided on stdin (never command line secrets).
password=values['SANDBOX_DB_PASSWORD'].replace("'","''")
statement=f"""
DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='ai_team_sandbox') THEN CREATE ROLE ai_team_sandbox LOGIN; END IF; END $$;
ALTER ROLE ai_team_sandbox PASSWORD '{password}' NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 2;
ALTER ROLE ai_team_sandbox SET statement_timeout='5s';
ALTER ROLE ai_team_sandbox SET idle_in_transaction_session_timeout='10s';
ALTER ROLE ai_team_sandbox SET temp_file_limit='64MB';
ALTER ROLE ai_team_sandbox SET max_parallel_workers_per_gather=0;
ALTER ROLE ai_team_sandbox SET search_path=pg_catalog,synthetic;
GRANT CONNECT ON DATABASE ai_team TO ai_team_sandbox;
CREATE SCHEMA IF NOT EXISTS sandbox AUTHORIZATION ai_team;
GRANT USAGE,CREATE ON SCHEMA sandbox TO ai_team_sandbox;
GRANT USAGE ON SCHEMA synthetic TO ai_team_sandbox;
GRANT SELECT ON synthetic.dim_customer,synthetic.dim_product,synthetic.fact_loan,synthetic.dim_calendar,synthetic.fact_loan_daily TO ai_team_sandbox;
"""
result=subprocess.run(['docker','compose','exec','-T','postgres','psql','-U','postgres','-d','ai_team','-v','ON_ERROR_STOP=1'],input=statement,text=True,capture_output=True)
if result.returncode:raise RuntimeError('Sandbox provisioning failed; inspect operator logs without secrets')
print('Restricted sandbox login and local secrets ready; no administrative grants.')
