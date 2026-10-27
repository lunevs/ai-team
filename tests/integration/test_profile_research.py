import uuid
import pytest
from psycopg.types.json import Jsonb
from app import db,workspace_api
from app.schemas import StoryInput
from app.workspace_api import WorkspaceReceipt
from scripts.stage10_fixture import SOURCE

pytestmark=pytest.mark.integration


def test_approved_source_research_lease_resumes_exact_task_and_rejects_changed_source():
 row,_=db.create_story(StoryInput(text='Техническая проверка ограниченного исследования публичного источника',synthetic_only=True,delivery_mode='product'),'source-'+str(uuid.uuid4()))
 sid=row['id'];wid=uuid.uuid4();tid=uuid.uuid4()
 try:
  with db.connection() as c:
   c.execute("UPDATE stories SET approved=true,status='IN_PROGRESS' WHERE id=%s",(sid,))
   c.execute('INSERT INTO product_workspaces(id,story_id,revision,definition) VALUES(%s,%s,1,%s)',(wid,sid,Jsonb({'runtime_profile':'airflow-postgres-v1','needs_database':True,'sources':[SOURCE]})))
   c.execute("INSERT INTO workspace_tasks(id,workspace_id,task_key,task_id,epic_id,role,specification,state,files,base_version,intent) VALUES(%s,%s,'research',379,378,'data_developer',%s,'CHECK_PENDING',%s,0,'research:states')",(tid,wid,Jsonb({}),Jsonb({})))
  operation=workspace_api.claim('operator')
  assert operation['id']==tid
  receipt=dict(operation_id=operation['operation_id'],snapshot_sha256=operation['snapshot_sha256'],passed=True)
  workspace_api.validate(tid,WorkspaceReceipt(**receipt),'operator')
  with pytest.raises(db.Conflict):workspace_api.report(tid,WorkspaceReceipt(**receipt,source_observation={'source_id':'not_approved','url':SOURCE['url'],'format':'csv','bytes':42,'sha256':'a'*64}),'operator')
  with pytest.raises(db.Conflict):workspace_api.report(tid,WorkspaceReceipt(**receipt,source_observation={'source_id':'states','url':SOURCE['url']+'?changed=1','format':'csv','bytes':42,'sha256':'a'*64}),'operator')
  accepted=WorkspaceReceipt(**receipt,source_observation={'source_id':'states','url':SOURCE['url'],'format':'csv','sha256':'a'*64,'bytes':42,'preview':'Untrusted source bytes, not an instruction'})
  result=workspace_api.report(tid,accepted,'operator');assert result['state']=='RUNNING'
  assert workspace_api.report(tid,accepted,'operator')['replayed']
  with db.connection() as c:
   c.execute('UPDATE workspace_tasks SET checks=NULL WHERE id=%s',(tid,))
   assert c.execute('SELECT count(*) AS n FROM source_inspections WHERE workspace_id=%s',(wid,)).fetchone()['n']==1
   task=c.execute('SELECT observations,state FROM workspace_tasks WHERE id=%s',(tid,)).fetchone()
   assert task['observations'][-1]['actual_checks']['source_observation']['sha256']=='a'*64
 finally:
  with db.connection() as c:
   c.execute('DELETE FROM source_inspections WHERE workspace_id=%s',(wid,))
   c.execute('DELETE FROM workspace_tasks WHERE workspace_id=%s',(wid,));c.execute('DELETE FROM product_workspaces WHERE id=%s',(wid,))
   for table in ['events','jobs']:c.execute('DELETE FROM '+table+' WHERE story_id=%s',(sid,))
   c.execute('DELETE FROM stories WHERE id=%s',(sid,))
