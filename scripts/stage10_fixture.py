"""Technical stack fixture, not a customer product or CBR ingestion."""
from types import SimpleNamespace
from pathlib import Path
from app.product_policy import build_package

SOURCE={'id':'states','format':'csv','url':'https://raw.githubusercontent.com/plotly/datasets/83665a724897ce3cac8db11ff2f53b216167a806/2014_usa_states.csv'}

def package(version='1.0.1',broken=False):
    files={
    'app.py':'''import json,os
from http.server import BaseHTTPRequestHandler,HTTPServer
import psycopg
class Handler(BaseHTTPRequestHandler):
 def do_GET(self):
  if self.path=='/health':value={'status':'ok'}
  elif self.path=='/result':
   with psycopg.connect(os.environ['DATABASE_URL']) as c:
    row=c.execute('SELECT count(*),coalesce(sum(population),0) FROM states').fetchone()
   value={'states':row[0],'population':int(row[1])}
  else:self.send_error(404);return
  data=json.dumps(value).encode();self.send_response(200);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
 def log_message(self,*args):pass
if __name__=='__main__':HTTPServer(('0.0.0.0',8000),Handler).serve_forever()
''',
    'migrations/001_states.sql':'CREATE TABLE states (postal text PRIMARY KEY,state text NOT NULL,population bigint NOT NULL CHECK(population>0));',
    'data/loader.py':'''import os
from decimal import Decimal
import psycopg2
from source_client import download,parse

def load():
 rows=parse(download('states'),'csv')
 assert len(rows)==52
 with psycopg2.connect(os.environ['DATABASE_URL']) as c:
  with c.cursor() as cursor:
   for row in rows:cursor.execute('INSERT INTO states(postal,state,population) VALUES(%s,%s,%s) ON CONFLICT(postal) DO UPDATE SET state=excluded.state,population=excluded.population',(row['Postal'],row['State'],int(Decimal(row['Population']))))
 return len(rows)
''',
    'dags/load_states.py':'''from datetime import timedelta
from airflow.sdk import DAG,task
import pendulum
with DAG('technical_states',schedule='* * * * *',start_date=pendulum.datetime(2026,1,1,tz='UTC'),catchup=False,max_active_runs=1) as dag:
 @task(retries=1,retry_delay=timedelta(seconds=5))
 def ingest():
  from data.loader import load
  return load()
 ingest()
''',
    'tests/test_product.py':'''import unittest
from source_client import parse
class Parsing(unittest.TestCase):
 def test_csv(self):self.assertEqual(parse(b'key,value\\na,42\\n','csv'),[{'key':'a','value':'42'}])
 def test_xml_no_entity(self):
  with self.assertRaises(ValueError):parse(b'<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><x>&e;</x>','xml')
'''}
    if version!='1.0.1':files['migrations/002_tag.sql']="ALTER TABLE states ADD COLUMN source_tag text NOT NULL DEFAULT 'technical';"
    if broken:files['migrations/003_fault.sql']="ALTER TABLE states ADD COLUMN fault_marker boolean;DO $$ BEGIN IF EXISTS(SELECT 1 FROM states) THEN RAISE EXCEPTION 'stage10 technical fault injection'; END IF;END $$;"
    solution=SimpleNamespace(title='Техническая проверка профиля',kind='service',output_type='dashboard',needs_database=True,
        architecture='PostgreSQL, Airflow LocalExecutor and web app; technical fixture only.',
        operating_notes='Minute schedule; UPSERT by postal. No CBR data. Stop all services before physical volume snapshots.',
        runtime_profile='airflow-postgres-v1',sources=[SimpleNamespace(model_dump=lambda:SOURCE)],files=files)
    return build_package(solution,'p-10aabbccddee',version,'10aabbcc-ddee-4000-8000-000000000000',1)

if __name__=='__main__':
    content,_=package();Path('/private/tmp/ai-team-stage10-product.zip').write_bytes(content)
