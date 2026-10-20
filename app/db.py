from contextlib import contextmanager
import hashlib
import json
import uuid
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from app.config import settings

class Conflict(Exception): pass
class Cancelled(Exception): pass
class BudgetExceeded(Exception): pass

@contextmanager
def connection():
    with psycopg.connect(settings.database_url, row_factory=dict_row, connect_timeout=5) as conn:
        yield conn

@contextmanager
def story_lock(story_id, blocking=True):
    with connection() as conn:
        conn.autocommit = True
        lock = int.from_bytes(hashlib.sha256(str(story_id).encode()).digest()[:8], 'big', signed=True)
        if blocking:
            conn.execute('SELECT pg_advisory_lock(%s)', (lock,))
            acquired = True
        else:
            acquired = conn.execute('SELECT pg_try_advisory_lock(%s) AS ok', (lock,)).fetchone()['ok']
        try:
            yield acquired
        finally:
            if acquired:
                conn.execute('SELECT pg_advisory_unlock(%s)', (lock,))

def get_story(story_id, conn=None):
    if conn is not None:
        return conn.execute('SELECT * FROM stories WHERE id=%s', (story_id,)).fetchone()
    with connection() as conn:
        return get_story(story_id, conn)

def active(story_id):
    row = get_story(story_id)
    if row is None or row['status'] == 'CANCELLED':
        raise Cancelled()
    return row

def event(conn, row, type_, payload=None, key=None):
    conn.execute('INSERT INTO events(story_id,event_key,type,payload,trace_id) VALUES(%s,%s,%s,%s,%s) ON CONFLICT(event_key) DO NOTHING',
                 (row['id'], key, type_, Jsonb(payload or {}), row['trace_id']))

def set_status(story_id, status, error_type=None):
    with connection() as conn:
        row = conn.execute('SELECT * FROM stories WHERE id=%s FOR UPDATE', (story_id,)).fetchone()
        if row['status'] == 'CANCELLED':
            raise Cancelled()
        if row['status'] != status:
            conn.execute('UPDATE stories SET status=%s,error_type=%s,updated_at=now() WHERE id=%s', (status,error_type,story_id))
            event(conn,row,'status.changed',{'status':status},f"{story_id}:{row['revision']}:status:{status}")

def enqueue(conn, story_id):
    conn.execute("INSERT INTO jobs(story_id) VALUES(%s) ON CONFLICT(story_id) DO UPDATE SET state='ready',available_at=now(),attempts=0,lease_until=NULL,owner=NULL",(story_id,))

def create_story(data, key):
    payload=data.model_dump()
    fingerprint=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
    story_id=uuid.uuid4()
    with connection() as conn:
        inserted=conn.execute('''INSERT INTO stories(id,idempotency_key,request_hash,text,synthetic_only,max_calls,max_tokens,max_cost_usd,trace_id)
          VALUES(%s,%s,%s,%s,true,%s,%s,%s,%s) ON CONFLICT(idempotency_key) DO NOTHING RETURNING id''',
          (story_id,key,fingerprint,data.text,data.max_calls,data.max_tokens,data.max_cost_usd,uuid.uuid4().hex)).fetchone()
        row=conn.execute('SELECT * FROM stories WHERE idempotency_key=%s',(key,)).fetchone()
        if row['request_hash'] != fingerprint:
            raise Conflict('Idempotency key reused for different request')
        if inserted:
            event(conn,row,'story.created',key=f'{story_id}:created')
            enqueue(conn,row['id'])
        return row, bool(inserted)

def external(story_id,key):
    with connection() as conn:
        return conn.execute('SELECT * FROM external_ids WHERE story_id=%s AND key=%s',(story_id,key)).fetchone()

def save_external(story_id,key,id_,url):
    with connection() as conn:
        conn.execute('INSERT INTO external_ids VALUES(%s,%s,%s,%s) ON CONFLICT(story_id,key) DO UPDATE SET external_id=excluded.external_id,url=excluded.url',(story_id,key,id_,url))

def usage(story_id):
    with connection() as conn:
        return conn.execute('''SELECT count(*)::int AS calls,coalesce(sum(coalesce(actual_tokens,reserved_tokens)),0)::int AS tokens,
          coalesce(sum(coalesce(actual_cost,reserved_cost)),0)::float AS estimated_cost_usd,
          count(*) FILTER(WHERE actual_cost IS NULL)::int AS estimated_calls,
          coalesce(array_agg(DISTINCT backend),'{}'::text[]) AS backends,
          coalesce(array_agg(DISTINCT model),'{}'::text[]) AS models
          FROM llm_calls WHERE story_id=%s''',(story_id,)).fetchone()

def reserve_call(story_id,role,tokens,cost):
    with connection() as conn:
        row=conn.execute('SELECT * FROM stories WHERE id=%s FOR UPDATE',(story_id,)).fetchone()
        if row['status']=='CANCELLED': raise Cancelled()
        used=conn.execute('''SELECT count(*) AS calls,coalesce(sum(coalesce(actual_tokens,reserved_tokens)),0) AS tokens,
          coalesce(sum(coalesce(actual_cost,reserved_cost)),0) AS cost FROM llm_calls WHERE story_id=%s''',(story_id,)).fetchone()
        if used['calls']+1>row['max_calls'] or used['tokens']+tokens>row['max_tokens'] or float(used['cost'])+cost>float(row['max_cost_usd']):
            raise BudgetExceeded()
        call_id=uuid.uuid4()
        conn.execute('INSERT INTO llm_calls(id,story_id,role,revision,backend,model,state,reserved_tokens,reserved_cost) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)',
          (call_id,story_id,role,row['revision'],settings.backend,settings.model,'reserved',tokens,cost))
        event(conn,row,'llm.reserved',{'role':role,'call_id':str(call_id)})
        return call_id
