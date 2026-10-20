import os
import signal
import threading
import time
import uuid
from opentelemetry import trace
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from app import db
from app.config import settings
from app.integrations import RetryableIntegrationError,RemoteBlocked,sync_status,tracker
from app.providers import RetryableProviderError
from app.workflow import run_story
from app.observability import log,tracer

stopping=threading.Event()
owner=str(uuid.uuid4())
current=[None]

def heartbeat():
    while not stopping.is_set():
        try:
            with db.connection() as conn:
                conn.execute('INSERT INTO worker_heartbeats(id) VALUES(%s) ON CONFLICT(id) DO UPDATE SET last_seen=now()',(owner,))
                if current[0]:
                    conn.execute("UPDATE jobs SET lease_until=now()+interval '30 seconds' WHERE story_id=%s AND owner=%s AND state='running'",(current[0],owner))
        except Exception as exc:
            log('worker.heartbeat.failed',error_type=type(exc).__name__)
        stopping.wait(5)

def claim():
    with db.connection() as conn:
        job=conn.execute("""SELECT * FROM jobs WHERE (state='ready' AND available_at<=now()) OR
            (state='running' AND lease_until<now()) ORDER BY available_at FOR UPDATE SKIP LOCKED LIMIT 1""").fetchone()
        if job:
            conn.execute("UPDATE jobs SET state='running',owner=%s,lease_until=now()+interval '30 seconds',attempts=attempts+1 WHERE story_id=%s",(owner,job['story_id']))
        return job

def finish(story_id,state):
    with db.connection() as conn:
        conn.execute('UPDATE jobs SET state=%s,lease_until=NULL WHERE story_id=%s AND owner=%s',(state,story_id,owner))

def execute(job):
    id=job['story_id']
    with db.story_lock(id,blocking=False) as acquired:
        if not acquired:
            with db.connection() as conn:
                conn.execute("UPDATE jobs SET state='ready',available_at=now()+interval '2 seconds',lease_until=NULL WHERE story_id=%s AND owner=%s",(id,owner))
            return
        current[0]=id
        try:
            with db.connection() as checkpoint_conn:
                checkpoint_conn.autocommit=True
                saver=PostgresSaver(checkpoint_conn,serde=JsonPlusSerializer(allowed_msgpack_modules=[('app.schemas','Role')]))
                trace_id=int(db.get_story(id)['trace_id'],16)
                parent=trace.NonRecordingSpan(trace.SpanContext(trace_id,uuid.uuid4().int & ((1<<64)-1),is_remote=True,trace_flags=trace.TraceFlags(1)))
                with tracer.start_as_current_span('story.run',context=trace.set_span_in_context(parent),attributes={'story_id':str(id)}):
                    run_story(id,saver)
            finish(id,'waiting' if db.get_story(id)['status'] in ['PLAN_REVIEW','BLOCKED'] else 'done')
            log('story.checkpoint',story_id=str(id),status=db.get_story(id)['status'])
        except db.Cancelled:
            # A cancellation may arrive while an HTTP request is in flight.
            # Enqueued cancellation sync is kept if API reset the owner.
            finish(id,'ready')
        except RemoteBlocked:
            finish(id,'waiting')
        except (RetryableIntegrationError,RetryableProviderError) as exc:
            if job['attempts']>=3:
                fail(id,'FAILED',type(exc).__name__)
            else:
                with db.connection() as conn:
                    conn.execute("UPDATE jobs SET state='ready',available_at=now()+(%s*interval '1 second'),lease_until=NULL WHERE story_id=%s AND owner=%s",(min(2**job['attempts'],20),id,owner))
                    row=db.get_story(id,conn)
                    db.event(conn,row,'worker.retry',{'error_type':type(exc).__name__,'attempt':job['attempts']+1})
        except db.BudgetExceeded:
            fail(id,'BLOCKED','BudgetExceeded')
        except Exception as exc:
            log('story.failed',story_id=str(id),error_type=type(exc).__name__)
            fail(id,'FAILED',type(exc).__name__)
        finally:
            current[0]=None

def fail(id,status,error_type):
    try:
        db.set_status(id,status,error_type)
        sync_status(id,status)
    except db.Cancelled:
        finish(id,'ready')
        return
    except Exception as exc:
        # Outage: schedule status reconciliation as a durable job, without rerunning LLM.
        with db.connection() as conn:
            row=db.get_story(id,conn)
            db.event(conn,row,'tracker.sync.pending',{'error_type':type(exc).__name__})
            conn.execute('INSERT INTO status_outbox(story_id) VALUES(%s) ON CONFLICT(story_id) DO NOTHING',(id,))
        log('tracker.sync.pending',story_id=str(id),error_type=type(exc).__name__)
    finish(id,'waiting' if status=='BLOCKED' else 'failed')

def reconcile_status():
    with db.connection() as conn:
        pending=conn.execute('SELECT story_id FROM status_outbox WHERE next_attempt<=now() LIMIT 1').fetchone()
    if not pending: return
    id=pending['story_id']
    with db.story_lock(id,blocking=False) as acquired:
        if not acquired: return
        try:
            status=db.get_story(id)['status']
            if status!='CANCELLED':
                try:
                    tracker.honor_remote_stop(id)
                except (db.Cancelled,RemoteBlocked):
                    with db.connection() as conn:
                        conn.execute('DELETE FROM status_outbox WHERE story_id=%s',(id,))
                    return
            tracker.set_status(id,status)
            with db.connection() as conn:
                conn.execute('DELETE FROM status_outbox WHERE story_id=%s',(id,))
        except Exception:
            with db.connection() as conn:
                conn.execute("UPDATE status_outbox SET attempts=attempts+1,next_attempt=now()+interval '30 seconds' WHERE story_id=%s",(id,))


def watch_remote_stops():
    # Waiting interrupts have no queued work: keep authoritative tracker stops observable.
    while not stopping.is_set():
        try:
            with db.connection() as conn:
                rows=conn.execute("""SELECT s.id FROM stories s JOIN jobs j ON j.story_id=s.id
                  WHERE s.status NOT IN ('NEW','CANCELLED','DONE')
                  AND j.state IN ('waiting','done','failed') ORDER BY s.updated_at LIMIT 100""").fetchall()
            for row in rows:
                if stopping.is_set(): break
                with db.story_lock(row['id'],blocking=False) as acquired:
                    if not acquired: continue
                    try:
                        tracker.honor_remote_stop(row['id'])
                    except (db.Cancelled,RemoteBlocked):
                        log('tracker.stop.applied',story_id=str(row['id']))
        except Exception as exc:
            log('tracker.watch.failed',error_type=type(exc).__name__)
        stopping.wait(10)


def main():
    signal.signal(signal.SIGTERM,lambda *_:stopping.set())
    signal.signal(signal.SIGINT,lambda *_:stopping.set())
    threading.Thread(target=heartbeat,daemon=True).start()
    threading.Thread(target=watch_remote_stops,daemon=True).start()
    log('worker.started',worker_id=owner)
    while not stopping.is_set():
        try:
            reconcile_status()
            job=claim()
            if job: execute(job)
            else: stopping.wait(0.5)
        except Exception as exc:
            log('worker.loop.failed',error_type=type(exc).__name__)
            stopping.wait(2)
    with db.connection() as conn:
        conn.execute('DELETE FROM worker_heartbeats WHERE id=%s',(owner,))

if __name__=='__main__': main()
