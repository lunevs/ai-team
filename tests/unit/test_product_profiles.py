import hashlib
import io
import json
from pathlib import Path
import socket
import uuid
import zipfile
import pytest
from pydantic import ValidationError
from app.source_policy import source_definition,public_addresses,fetch
from app.product_contract import ProductSolution,build_package,inspect_package
from app.schemas import ProductDefinition
from app.product_profiles import compose,PROFILE,AIRFLOW_IMAGE,owned_files
from scripts.stage10_fixture import package,SOURCE

@pytest.mark.parametrize('url',[
 'http://example.com/a','https://localhost/a','https://127.0.0.1/a','https://169.254.169.254/a',
 'https://[::1]/a','https://a.internal/a','https://x.local/a','https://example.com:444/a',
 'https://user:pass@example.com/a','https://example.com/a#secret','https://example.com/a\nX:evil',
 'https://example.com./a','https://example.com/\\evil','https://example.com/has space','https://example.com/a?api_key=secret'])
def test_sources_reject_ambient_access(url):
 with pytest.raises(ValueError):source_definition(SOURCE|{'url':url})

@pytest.mark.parametrize('addresses',[['127.0.0.1'],['169.254.169.254'],['10.1.2.3'],['::1'],['fd00::1'],['93.184.216.34','192.168.0.1']])
def test_dns_rebinding_private_and_mixed_answers_blocked(monkeypatch,addresses):
 monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**k:[(2,1,6,'',(ip,443)) for ip in addresses])
 with pytest.raises(ValueError):public_addresses('example.com')


def test_public_dns_socket_is_pinned_and_redirect_not_followed(monkeypatch):
 import app.source_policy as policy
 seen=[]
 class Reply:
  status=302
  def getheader(self,*a):return None
 class Connection:
  def __init__(self,host,ip):seen.append((host,ip))
  def request(self,*a,**k):seen.append(a)
  def getresponse(self):return Reply()
  def close(self):pass
 monkeypatch.setattr(policy,'public_addresses',lambda h:['93.184.216.34'])
 monkeypatch.setattr(policy,'PinnedHTTPS',Connection)
 with pytest.raises(ValueError,match='redirects'):fetch(SOURCE)
 assert seen[0]==('raw.githubusercontent.com','93.184.216.34')
 assert len(seen)==2


def test_multiservice_package_is_independent_and_fixed():
 content,manifest=package();checked,files=inspect_package(content)
 assert checked==manifest and checked['runtime_profile']==PROFILE
 assert files['sources.json']==owned_files([SOURCE])['sources.json'].encode()
 stack=json.loads(files['docker-compose.yml']);services=stack['services']
 assert set(services)=={'postgres','app','source-gateway','gateway','airflow-api','airflow-scheduler','airflow-dag-processor'}
 assert stack['networks']['private']['internal']
 assert 'egress' in services['source-gateway']['networks']
 for name in ['app','postgres','airflow-api','airflow-scheduler','airflow-dag-processor']:
  assert services[name]['networks']==['private'] and 'ports' not in services[name]
 assert services['airflow-scheduler']['environment']['AIRFLOW__CORE__PARALLELISM']=='1'
 assert '@sha256:' in AIRFLOW_IMAGE
 assert b'AI' in files['install.md']


def test_new_profile_cannot_be_claimed_without_database_or_dag():
 values=dict(title='Profile test',kind='job',output_type='file',needs_database=False,architecture='Architecture ' * 5,operating_notes='Operating ' * 5,runtime_profile=PROFILE)
 with pytest.raises(ValidationError):ProductDefinition(**values)
 values.update(kind='service',needs_database=True)
 with pytest.raises(ValidationError):ProductSolution(**values,files={'app.py':'pass','tests/test_x.py':'pass'})
 with pytest.raises(ValidationError):ProductDefinition(**(values|{'runtime_profile':'python-locked-v1','sources':[SOURCE]}))


def test_profile_tampering_fails_even_after_manifest_rehash():
 content,_=package();output=io.BytesIO()
 with zipfile.ZipFile(io.BytesIO(content)) as z:files={n:z.read(n) for n in z.namelist()}
 root='products/p-10aabbccddee/'
 files[root+'source_gateway.py']=b'print("unrestricted")'
 manifest=json.loads(files[root+'product.json'])
 manifest['files']['source_gateway.py']={'sha256':hashlib.sha256(files[root+'source_gateway.py']).hexdigest(),'size':len(files[root+'source_gateway.py'])}
 files[root+'product.json']=json.dumps(manifest).encode()
 with zipfile.ZipFile(output,'w') as z:
  for p,data in files.items():z.writestr(p,data)
 with pytest.raises(ValueError,match='Profile policy'):inspect_package(output.getvalue())


def test_workspace_sources_cannot_escape_approved_plan():
 from app.workspace_policy import snapshot,inspect_snapshot
 with pytest.raises(ValueError):snapshot({}, {'runtime_profile':PROFILE,'sources':[SOURCE],'research_source':'another'})
 content,_=snapshot({}, {'runtime_profile':PROFILE,'sources':[SOURCE],'research_source':'states'})
 assert inspect_snapshot(content)[0]['research_source']=='states'


def test_safe_source_parsers_reject_zip_traversal_bomb_and_xml_entities():
 import importlib.util
 spec=importlib.util.spec_from_file_location('source_client',Path('app/product_templates/source_client.py'))
 module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
 for name,data in [('../secret',b'a'),('good',b'x'*32_000_001)]:
  out=io.BytesIO()
  with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:z.writestr(name,data)
  with pytest.raises(ValueError):module.parse(out.getvalue(),'zip')
 assert module.parse(b'key,value\na,42\n','csv')==[{'key':'a','value':'42'}]
 assert module.parse(b'{"value":42}','json')=={'value':42}


def test_source_change_invalidates_plan_approval_digest():
 from app.feasibility import plan_digest
 first={'product_definition':{'runtime_profile':PROFILE,'sources':[SOURCE]}}
 second={'product_definition':{'runtime_profile':PROFILE,'sources':[SOURCE|{'url':SOURCE['url']+'?different=1'}]}}
 assert plan_digest({},first)!=plan_digest({},second)


def test_profile_request_cannot_publish_from_old_runtime_base(monkeypatch):
 import base64
 from fastapi import HTTPException
 from app import git_broker as broker
 monkeypatch.setenv('GIT_BROKER_API_TOKEN','test')
 monkeypatch.setattr(broker,'ready',lambda *a:{'configured':True,'workspace_base_ready':True,'workspace_base_sha':'a'*40,'workspace_base_ref':'release/370','workspace_profiles':['python-locked-v1']})
 monkeypatch.setattr(broker,'publish_task',lambda *a:pytest.fail('Unverified profile must never publish'))
 task=broker.TaskBundle(task_id=379,epic_id=378,role='application_developer',files={'workspaces/p-10aabbccddee/workspace.json':base64.b64encode(json.dumps({'runtime_profile':PROFILE}).encode()).decode()})
 data=broker.Publish(run_id=uuid.uuid4(),story_id=uuid.uuid4(),revision=1,documentation_url='',tasks=[task],workspace_task=True)
 with pytest.raises(HTTPException) as failure:broker.publish(data,'Bearer test')
 assert failure.value.status_code==409


def test_external_sources_require_the_isolated_profile_and_exact_urls():
 from app.feasibility import evaluate
 from app.feasibility import PlanAssessment,CriterionCheck
 story={'text':'Airflow ежедневно получает https://example.com/source.csv','answers':''};pm={'acceptance_criteria':['Источник загружается']}
 plan={'epics':[{'tasks':[{'key':'load','role':'data_developer'}]}]}
 assessment=PlanAssessment(criteria=[CriterionCheck(criterion_index=0,capability_ids=['python_service'],task_keys=['load'],verification='Сверить источник и строки после повторной загрузки')])
 assert evaluate(story,pm,plan,assessment)['status']=='NEEDS_PLATFORM'
 plan['product_definition']={'runtime_profile':PROFILE,'sources':[]}
 assert evaluate(story,pm,plan,assessment)['status']=='NEEDS_ANALYSIS'
 plan['product_definition']['sources']=[SOURCE]
 assert evaluate(story,pm,plan,assessment)['status']=='READY'


def test_legacy_product_keeps_preexisting_source_names():
 from types import SimpleNamespace
 solution=SimpleNamespace(title='Legacy source test',kind='service',output_type='scalar',needs_database=False,architecture='An existing legacy package',operating_notes='No migrations',files={'app.py':'pass','tests/test_old.py':'pass','source_client.py':'LEGACY=True','data/profile_marker.md':'Old customer documentation'})
 content,_=build_package(solution,'p-012345678abc','1.0.1',uuid.uuid4(),1)
 _,files=inspect_package(content)
 assert files['source_client.py']==b'LEGACY=True' and files['data/profile_marker.md']==b'Old customer documentation'
