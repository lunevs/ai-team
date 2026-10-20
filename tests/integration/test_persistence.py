from concurrent.futures import ThreadPoolExecutor
import uuid
import pytest
import psycopg
from app import db
from app.schemas import StoryInput

pytestmark=pytest.mark.integration

def payload(**overrides):
    return StoryInput(text='Синтетический кредитный портфель для проверки восстановления',synthetic_only=True,**overrides)


def test_concurrent_creation_and_conflicting_idempotency_key():
    key='test-'+str(uuid.uuid4())
    with ThreadPoolExecutor(max_workers=6) as pool:
        rows=list(pool.map(lambda _:db.create_story(payload(),key),range(6)))
    assert len({str(row[0]['id']) for row in rows})==1
    assert sum(row[1] for row in rows)==1
    with pytest.raises(db.Conflict):
        db.create_story(StoryInput(text='Иная синтетическая история с другим содержимым',synthetic_only=True),key)
    with db.connection() as conn:
        id=rows[0][0]['id']
        assert conn.execute('SELECT count(*) AS n FROM jobs WHERE story_id=%s',(id,)).fetchone()['n']==1
        conn.execute('DELETE FROM events WHERE story_id=%s',(id,))
        conn.execute('DELETE FROM jobs WHERE story_id=%s',(id,))
        conn.execute('DELETE FROM stories WHERE id=%s',(id,))


def test_budget_reservation_is_atomic():
    row,_=db.create_story(payload(max_calls=1),'budget-'+str(uuid.uuid4()))
    id=row['id']
    def reserve(_):
        try:db.reserve_call(id,'pm',100,0.01);return True
        except db.BudgetExceeded:return False
    with ThreadPoolExecutor(max_workers=6) as pool:
        assert sum(pool.map(reserve,range(6)))==1
    assert db.usage(id)['calls']==1
    with db.connection() as conn:
        conn.execute('DELETE FROM llm_calls WHERE story_id=%s',(id,))
        conn.execute('DELETE FROM events WHERE story_id=%s',(id,))
        conn.execute('DELETE FROM jobs WHERE story_id=%s',(id,))
        conn.execute('DELETE FROM stories WHERE id=%s',(id,))


def test_runtime_role_cannot_create_tables():
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with db.connection() as conn:
            conn.execute('CREATE TABLE runtime_must_not_create(id int)')
