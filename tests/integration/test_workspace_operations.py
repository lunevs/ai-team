"""Real DB checks of source/digest/lease/revision binding; no Docker or external services."""
import uuid
import pytest
from psycopg.types.json import Jsonb
from app import db
from app.schemas import StoryInput
from app.workspace_api import claim,report,validate,WorkspaceReceipt

pytestmark=pytest.mark.integration


def test_workspace_leases_replay_revision_and_cancellation():
    story,_=db.create_story(StoryInput(text='Изолированная проверка рабочей копии задач',synthetic_only=True,delivery_mode='product'),'workspace-'+str(uuid.uuid4()))
    sid=story['id'];wid=uuid.uuid4();tid=uuid.uuid4()
    try:
        with db.connection() as c:
            c.execute("UPDATE stories SET status='IN_PROGRESS',approved=true WHERE id=%s",(sid,))
            c.execute('INSERT INTO product_workspaces(id,story_id,revision,definition) VALUES(%s,%s,1,%s)',(wid,sid,Jsonb({'needs_database':False})))
            c.execute("INSERT INTO workspace_tasks(id,workspace_id,task_key,task_id,epic_id,role,specification,state,files,base_version,intent) VALUES(%s,%s,'test',9999,9998,'qa','{}','CHECK_PENDING',%s,0,'finish')",(tid,wid,Jsonb({'tests/test_one.py':'value=42\n'})))
        first=claim('test');assert first['id']==tid and claim('test')['operation'] is None
        receipt=WorkspaceReceipt(operation_id=first['operation_id'],snapshot_sha256=first['snapshot_sha256'],passed=True,tests_run=1,tests_present=True)
        with pytest.raises(db.Conflict):validate(tid,receipt.model_copy(update={'snapshot_sha256':'0'*64}),'test')
        with db.connection() as c:c.execute("UPDATE workspace_tasks SET lease_until=now()-interval '1 second' WHERE id=%s",(tid,))
        with pytest.raises(db.Conflict):report(tid,receipt,'test')
        second=claim('test');assert second['operation_id']!=first['operation_id']
        with pytest.raises(db.Conflict):report(tid,receipt,'test')
        receipt=receipt.model_copy(update={'operation_id':second['operation_id']})
        with pytest.raises(db.Conflict):report(tid,receipt.model_copy(update={'tests_present':False}),'test')
        assert report(tid,receipt,'test')['state']=='CHECKED'
        assert report(tid,receipt,'test')['replayed']
        with pytest.raises(db.Conflict):report(tid,receipt.model_copy(update={'test_output':'different'}),'test')
        with db.connection() as c:
            c.execute("UPDATE workspace_tasks SET state='CHECK_PENDING' WHERE id=%s",(tid,))
            c.execute('UPDATE stories SET revision=2 WHERE id=%s',(sid,))
        assert claim('test')['operation'] is None
        with pytest.raises(db.Conflict):report(tid,receipt,'test')
        with db.connection() as c:c.execute("UPDATE stories SET revision=1,status='CANCELLED' WHERE id=%s",(sid,))
        assert claim('test')['operation'] is None
        with pytest.raises(db.Conflict):report(tid,receipt,'test')
    finally:
        with db.connection() as c:
            c.execute('DELETE FROM workspace_tasks WHERE workspace_id=%s',(wid,))
            c.execute('DELETE FROM product_workspaces WHERE id=%s',(wid,))
            c.execute('DELETE FROM events WHERE story_id=%s',(sid,))
            c.execute('DELETE FROM jobs WHERE story_id=%s',(sid,))
            c.execute('DELETE FROM stories WHERE id=%s',(sid,))
