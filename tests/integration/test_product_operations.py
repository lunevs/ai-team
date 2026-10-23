import uuid
import pytest
from psycopg.types.json import Jsonb
from app import db
from app.schemas import StoryInput
from app.product_api import claim,report,OperatorReport

pytestmark=pytest.mark.integration


def test_operator_leases_bind_digest_revision_and_cancellation():
    story,_=db.create_story(StoryInput(text='Изолированная проверка состояния продуктового оператора',synthetic_only=True,delivery_mode='product'),'product-lease-'+str(uuid.uuid4()))
    id=story['id'];release=uuid.uuid4();digest='a'*64
    try:
        with db.connection() as conn:
            conn.execute("UPDATE stories SET status='IN_PROGRESS',approved=true WHERE id=%s",(id,))
            conn.execute("INSERT INTO product_releases(id,story_id,revision,product_key,version,status,package) VALUES(%s,%s,1,%s,'1.0.1','CHECK_PENDING',%s)",
                (release,id,'p-'+id.hex[:12],Jsonb({'sha256':digest,'size':0,'backend':'local'})))
        first=claim('test-operator')
        assert first['id']==release and first['operation']=='check'
        assert claim('test-operator')['operation'] is None
        with pytest.raises(db.Conflict):
            report(release,OperatorReport(operation_id=first['operation_id'],package_sha256='b'*64,passed=True),'test-operator')
        with db.connection() as conn:conn.execute("UPDATE product_releases SET lease_until=now()-interval '1 second' WHERE id=%s",(release,))
        second=claim('test-operator')
        assert second['operation_id']!=first['operation_id']
        with pytest.raises(db.Conflict):
            report(release,OperatorReport(operation_id=first['operation_id'],package_sha256=digest,passed=True),'test-operator')
        with db.connection() as conn:conn.execute("UPDATE stories SET status='CANCELLED' WHERE id=%s",(id,))
        with pytest.raises(db.Conflict):
            report(release,OperatorReport(operation_id=second['operation_id'],package_sha256=digest,passed=True),'test-operator')
        assert claim('test-operator')['operation'] is None
    finally:
        with db.connection() as conn:
            conn.execute('DELETE FROM product_releases WHERE story_id=%s',(id,))
            conn.execute('DELETE FROM events WHERE story_id=%s',(id,))
            conn.execute('DELETE FROM jobs WHERE story_id=%s',(id,))
            conn.execute('DELETE FROM stories WHERE id=%s',(id,))
