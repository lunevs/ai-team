from contextlib import asynccontextmanager
import uuid
from fastapi import FastAPI, Depends, Header, HTTPException, Response
from fastapi.responses import JSONResponse
from app.auth import current_user
from app.schemas import StoryInput, Approval, Resume
from app import db
from app.observability import log

@asynccontextmanager
async def lifespan(app):
    yield

app=FastAPI(title='Bank AI Delivery Team',version='0.7.0',lifespan=lifespan)

@app.middleware('http')
async def trace_story_mutation(request,call_next):
    import asyncio,hmac
    from app.config import settings
    from app.observability import story_trace
    from opentelemetry.trace import Status,StatusCode
    parts=request.url.path.strip('/').split('/')
    if request.method=='POST' and len(parts)>=2 and parts[0]=='stories' and settings.api_token and hmac.compare_digest(request.headers.get('Authorization',''),'Bearer '+settings.api_token):
        try:id=uuid.UUID(parts[1])
        except ValueError:return await call_next(request)
        story=await asyncio.to_thread(db.get_story,id)
        if story:
            with story_trace(story,'api') as span:
                response=await call_next(request)
                if response.status_code>=400:
                    span.set_status(Status(StatusCode.ERROR));span.set_attribute('error_type','HTTPRejected')
                return response
    return await call_next(request)

from app.metrics import authenticate as metrics_auth

@app.get('/metrics',include_in_schema=False)
def telemetry_metrics(user=Depends(metrics_auth)):
    from app.metrics import snapshot,render
    return Response(render(snapshot()),media_type='text/plain; version=0.0.4')

@app.exception_handler(db.Conflict)
def conflict_handler(request,exc):
    return JSONResponse(status_code=409,content={'detail':str(exc)})

@app.exception_handler(Exception)
def unexpected_error(request,exc):
    log('api.error',error_type=type(exc).__name__)
    return JSONResponse(status_code=503,content={'detail':'Сервис временно недоступен; повторите запрос с тем же Idempotency-Key'})

@app.get('/health')
def health():
    return {'status':'ok'}

@app.get('/ready')
def ready():
    with db.connection() as conn:
        conn.execute('SELECT 1 FROM stories LIMIT 1')
        worker=conn.execute("SELECT 1 FROM worker_heartbeats WHERE last_seen>now()-interval '30 seconds' LIMIT 1").fetchone()
    if not worker: raise HTTPException(503,'Worker недоступен')
    return {'status':'ready'}

def require_story(id):
    row=db.get_story(id)
    if row is None: raise HTTPException(404,'История не найдена')
    return row

def public_story(row):
    from app.products import get as get_product, public as public_product
    from app.analytics import get_run
    from app.execution import get_run as get_execution
    with db.connection() as conn:
        links=conn.execute('SELECT key,external_id,url FROM external_ids WHERE story_id=%s ORDER BY key',(row['id'],)).fetchall()
    main=next((x['external_id'] for x in links if x['key']=='main'),None)
    return {k:row[k] for k in ['id','text','delivery_mode','status','revision','pm','plan','plan_digest','approved','questions','trace_id','error_type','created_at','updated_at']} | {'product':public_product(get_product(row['id'],row['revision'])),'story_id':row['id'],'openproject_work_package_id':main,'links':links,'usage':db.usage(row['id']),'analytics':get_run(row['id'],row['revision']),'execution':get_execution(row['id'],row['revision'])}

@app.post('/stories',status_code=201)
def create(data:StoryInput,response:Response,idempotency_key:str=Header(min_length=8,max_length=128),user=Depends(current_user)):
    row,inserted=db.create_story(data,idempotency_key)
    # Durable acceptance first; tracker outages do not lose the story.
    try:
        from app.integrations import ensure_main
        with db.story_lock(row['id']):
            ensure_main(row['id'])
    except Exception as exc:
        log('tracker.pending',story_id=str(row['id']),error_type=type(exc).__name__)
        response.status_code=202
    else:
        if not inserted: response.status_code=200
    return public_story(require_story(row['id']))

@app.get('/stories')
def list_stories(user=Depends(current_user)):
    with db.connection() as conn:
        return conn.execute('SELECT id,text,status,revision,created_at FROM stories ORDER BY created_at DESC LIMIT 100').fetchall()

@app.get('/stories/{id}')
def get(id:uuid.UUID,user=Depends(current_user)):
    return public_story(require_story(id))

@app.get('/stories/{id}/events')
def events(id:uuid.UUID,after:int=0,user=Depends(current_user)):
    require_story(id)
    with db.connection() as conn:
        return conn.execute('SELECT id,type,payload,trace_id,created_at FROM events WHERE story_id=%s AND id>%s ORDER BY id LIMIT 200',(id,after)).fetchall()

@app.post('/stories/{id}/approve-plan')
def approve(id:uuid.UUID,data:Approval,user=Depends(current_user)):
    initial=require_story(id)
    from app.analytics import get_run
    if get_run(id): raise db.Conflict('Используйте отдельное одобрение аналитической спецификации')
    if not initial['approved']:
        from app.integrations import tracker
        from app.config import settings
        main=db.external(id,'main')
        if main:
            remote=tracker.request('/api/v3/work_packages/'+str(main['external_id']),role='pm')
            if remote['_links']['status']['href']!=f"/api/v3/statuses/{settings.status_ids.get('PLAN_REVIEW')}":
                raise db.Conflict('Статус плана в OpenProject изменился')
    with db.connection() as conn:
        row=conn.execute('SELECT * FROM stories WHERE id=%s FOR UPDATE',(id,)).fetchone()
        if row['revision']!=data.revision or row['plan_digest']!=data.plan_digest:
            raise db.Conflict('Ревизия плана изменилась')
        if row['approved'] and row['status'] in ['PLAN_REVIEW','IN_PROGRESS']:
            return {'status':'approved','revision':row['revision']}
        if row['status']!='PLAN_REVIEW': raise db.Conflict('План сейчас нельзя одобрить')
        conn.execute('INSERT INTO approvals(story_id,revision,plan_digest,actor) VALUES(%s,%s,%s,%s)',(id,data.revision,data.plan_digest,user))
        conn.execute('UPDATE stories SET approved=true,updated_at=now() WHERE id=%s',(id,))
        db.event(conn,row,'plan.approved',{'revision':data.revision,'actor':user},f'{id}:{data.revision}:approved')
        db.enqueue(conn,id)
    return {'status':'approved','revision':data.revision}

@app.post('/stories/{id}/cancel')
def cancel(id:uuid.UUID,user=Depends(current_user)):
    require_story(id)
    with db.connection() as conn:
        row=conn.execute('SELECT * FROM stories WHERE id=%s FOR UPDATE',(id,)).fetchone()
        if row['status']!='CANCELLED':
            conn.execute("UPDATE stories SET status='CANCELLED',updated_at=now() WHERE id=%s",(id,))
            db.event(conn,row,'story.cancelled',{'actor':user},f'{id}:cancelled')
            db.enqueue(conn,id)
    return {'status':'CANCELLED'}

@app.post('/stories/{id}/resume')
def resume(id:uuid.UUID,data:Resume,user=Depends(current_user)):
    require_story(id)
    with db.connection() as conn:
        row=conn.execute('SELECT * FROM stories WHERE id=%s FOR UPDATE',(id,)).fetchone()
        if row['status'] not in ['BLOCKED','FAILED']:
            raise db.Conflict('Возобновление доступно только для BLOCKED/FAILED')
        if data.answers.strip():
            conn.execute("UPDATE stories SET revision=revision+1,answers=%s,pm=NULL,plan=NULL,plan_digest=NULL,approved=false,questions='[]',status='NEW',error_type=NULL,updated_at=now() WHERE id=%s",(data.answers,id))
        else:
            if row['questions']: raise db.Conflict('Нужны ответы на вопросы')
            conn.execute("UPDATE stories SET status='NEW',error_type=NULL,updated_at=now() WHERE id=%s",(id,))
        db.event(conn,row,'story.resumed',{'actor':user,'new_revision':bool(data.answers.strip())})
        db.enqueue(conn,id)
    return public_story(require_story(id))

@app.get('/stories/{id}/team')
def team(id:uuid.UUID,user=Depends(current_user)):
    from app.team import project_team
    from app.integrations import tracker
    from app.config import settings
    story=require_story(id)
    with db.connection() as conn:
        story['product']=conn.execute('SELECT status FROM product_releases WHERE story_id=%s AND revision=%s',(id,story['revision'])).fetchone()
        story['execution']=conn.execute('SELECT status FROM execution_runs WHERE story_id=%s AND revision=%s',(id,story['revision'])).fetchone()
        results=conn.execute('SELECT role,revision FROM agent_results WHERE story_id=%s ORDER BY revision DESC',(id,)).fetchall()
        calls=conn.execute('SELECT role,revision,state FROM llm_calls WHERE story_id=%s',(id,)).fetchall()
        links=conn.execute('SELECT key,external_id,url FROM external_ids WHERE story_id=%s',(id,)).fetchall()
        job=conn.execute("SELECT 1 FROM jobs WHERE story_id=%s AND state='running' AND lease_until>now()",(id,)).fetchone()
    # Event payload historically omitted revision; event_key identifies it reliably.
    with db.connection() as conn:
        completed=conn.execute("SELECT event_key,created_at FROM events WHERE story_id=%s AND type='agent.completed'",(id,)).fetchall()
    completions={}
    for event in completed:
        parts=(event['event_key'] or '').split(':')
        if len(parts)==4:
            completions[(parts[2],int(parts[1]))]=event['created_at']
    packages={}
    available=True
    try:
        statuses={item['_links']['self']['href']:item for item in tracker.collection('/api/v3/statuses',role='pm')}
        import json
        from urllib.parse import quote
        ids=[str(link['external_id']) for link in links if link['key'].startswith(f"r{story['revision']}:task:")]
        path=f'/api/v3/projects/{settings.project_id}/work_packages?pageSize=100'
        filters=quote(json.dumps([{'id':{'operator':'=','values':ids}}]),safe='') if ids else ''
        selected=tracker.collection(path+'&filters='+filters,role='pm') if ids else []
        for item in selected:
            href=item.get('_links',{}).get('status',{}).get('href')
            status=dict(statuses.get(href,{}))
            status['isInProgress']=href==f"/api/v3/statuses/{settings.status_ids.get('IN_PROGRESS')}"
            item.setdefault('_embedded',{})['status']=status
            packages[item['id']]=item
    except Exception as exc:
        available=False
        log('team.tracker_unavailable',error_type=type(exc).__name__)
    return project_team(story,results,completions,calls,links,packages,available,bool(job))


@app.get('/metadata/synthetic')
def synthetic_metadata(user=Depends(current_user)):
    from app.synthetic import metadata
    with db.connection() as conn:
        return metadata(conn)

@app.post('/stories/{id}/analytics/start',status_code=202)
def start_analytics(id:uuid.UUID,user=Depends(current_user)):
    if require_story(id)['delivery_mode']!='credit_demo':raise db.Conflict('Используйте продуктовый процесс этой истории')
    with db.story_lock(id):
        with db.connection() as conn:
            story=conn.execute('SELECT * FROM stories WHERE id=%s FOR UPDATE',(id,)).fetchone()
            if story['status'] in ['CANCELLED','BLOCKED','FAILED']:
                raise db.Conflict('Сначала устраните блокировку или отмену')
            existing=conn.execute('SELECT status FROM analytics_runs WHERE story_id=%s AND revision=%s',(id,story['revision'])).fetchone()
            if existing:return {'status':existing['status']}
            if not story['approved'] or story['status']!='IN_PROGRESS':
                raise db.Conflict('Сначала одобрите текущий план')
            conn.execute('INSERT INTO analytics_runs(story_id,revision) VALUES(%s,%s)',(id,story['revision']))
            db.event(conn,story,'analytics.requested',{'actor':user},f"{id}:{story['revision']}:analytics-requested")
            db.enqueue(conn,id)
    return {'status':'REQUESTED'}

@app.post('/stories/{id}/analytics/approve')
def approve_analytics(id:uuid.UUID,data:Approval,user=Depends(current_user)):
    from app.analytics import get_run
    from app.integrations import tracker,sync_status
    from app.config import settings
    require_story(id)
    with db.story_lock(id):
        story=require_story(id);run=get_run(id)
        if not run or data.revision!=story['revision'] or data.plan_digest!=run['digest']:
            raise db.Conflict('Ревизия или digest аналитики изменились')
        if run['status']=='APPROVED' and story['status']=='IN_PROGRESS':return {'status':'APPROVED'}
        if run['status']!='REVIEW' or story['status']!='PLAN_REVIEW':raise db.Conflict('Спецификацию сейчас нельзя одобрить')
        tracker.honor_remote_stop(id)
        main=db.external(id,'main')
        remote=tracker.request('/api/v3/work_packages/'+str(main['external_id']),role='pm')
        if remote['_links']['status']['href']!=f"/api/v3/statuses/{settings.status_ids['PLAN_REVIEW']}":
            raise db.Conflict('Статус в трекере изменился')
        with db.connection() as conn:
            story=conn.execute('SELECT * FROM stories WHERE id=%s FOR UPDATE',(id,)).fetchone()
            if story['status']!='PLAN_REVIEW' or story['revision']!=data.revision:raise db.Conflict('История изменилась')
            conn.execute("UPDATE analytics_runs SET status='APPROVED',approved_by=%s,approved_at=now() WHERE story_id=%s AND revision=%s",(user,id,data.revision))
            db.event(conn,story,'analytics.approved',{'actor':user,'digest':data.plan_digest,'sql_executed':False},f'{id}:{data.revision}:analytics-approved')
            conn.execute("UPDATE stories SET status='IN_PROGRESS',updated_at=now() WHERE id=%s",(id,))
            conn.execute('INSERT INTO status_outbox(story_id) VALUES(%s) ON CONFLICT DO NOTHING',(id,))
            db.enqueue(conn,id)
    return {'status':'APPROVED'}

@app.post('/stories/{id}/analytics/revise')
def revise_analytics(id:uuid.UUID,data:Resume,user=Depends(current_user)):
    if not data.answers.strip():raise HTTPException(422,'Опишите изменение спецификации')
    require_story(id)
    with db.story_lock(id):
        with db.connection() as conn:
            story=conn.execute('SELECT * FROM stories WHERE id=%s FOR UPDATE',(id,)).fetchone()
            run=conn.execute('SELECT status FROM analytics_runs WHERE story_id=%s AND revision=%s',(id,story['revision'])).fetchone()
            if not run or run['status'] not in ['REVIEW','APPROVED'] or story['status'] not in ['PLAN_REVIEW','IN_PROGRESS']:
                raise db.Conflict('Спецификацию сейчас нельзя изменить')
            execution=conn.execute('SELECT status FROM execution_runs WHERE story_id=%s AND revision=%s',(id,story['revision'])).fetchone()
            if execution:raise db.Conflict('Исполнение уже началось. Отмените историю и создайте новую с изменёнными критериями.')
            conn.execute("UPDATE stories SET revision=revision+1,answers=%s,pm=NULL,plan=NULL,plan_digest=NULL,approved=false,questions='[]',status='NEW',error_type=NULL,updated_at=now() WHERE id=%s",(data.answers,id))
            db.event(conn,story,'analytics.revision_requested',{'actor':user,'new_revision':story['revision']+1})
            db.enqueue(conn,id)
    return public_story(require_story(id))


@app.post('/stories/{id}/execution/start',status_code=202)
def start_execution(id:uuid.UUID,user=Depends(current_user)):
    if require_story(id)['delivery_mode']!='credit_demo':raise db.Conflict('Используйте продуктовый процесс этой истории')
    with db.story_lock(id):
        with db.connection() as conn:
            story=conn.execute('SELECT * FROM stories WHERE id=%s FOR UPDATE',(id,)).fetchone()
            if story['status'] in ['CANCELLED','BLOCKED','FAILED','DONE']:raise db.Conflict('История не готова к исполнению')
            existing=conn.execute('SELECT id,status FROM execution_runs WHERE story_id=%s AND revision=%s',(id,story['revision'])).fetchone()
            if existing:return existing
            spec=conn.execute('SELECT * FROM analytics_runs WHERE story_id=%s AND revision=%s',(id,story['revision'])).fetchone()
            if not spec or spec['status']!='APPROVED':raise db.Conflict('Сначала одобрите аналитическую спецификацию')
            run_id=uuid.uuid4()
            conn.execute('INSERT INTO execution_runs(id,story_id,revision,analytics_digest) VALUES(%s,%s,%s,%s)',(run_id,id,story['revision'],spec['digest']))
            db.event(conn,story,'execution.requested',{'run_id':str(run_id),'actor':user},f'{run_id}:requested')
            db.enqueue(conn,id)
    return {'id':run_id,'status':'REQUESTED'}

@app.post('/stories/{id}/execution/retry',status_code=202)
def retry_execution(id:uuid.UUID,user=Depends(current_user)):
    require_story(id)
    with db.story_lock(id):
        with db.connection() as conn:
            story=conn.execute('SELECT * FROM stories WHERE id=%s FOR UPDATE',(id,)).fetchone()
            record=conn.execute('SELECT * FROM execution_runs WHERE story_id=%s AND revision=%s',(id,story['revision'])).fetchone()
            if not record or record['status'] not in ['WAITING_GITHUB','READY_TO_PUBLISH','PUBLISHED','BUSINESS_ACCEPTED','FINALIZING_RELEASE'] or story['status']=='CANCELLED':
                raise db.Conflict('Повтор публикации сейчас недоступен')
            status='ACCEPTANCE' if record['status'] in ['BUSINESS_ACCEPTED','FINALIZING_RELEASE'] else 'IN_PROGRESS'
            conn.execute('UPDATE stories SET status=%s,error_type=NULL WHERE id=%s',(status,id))
            db.enqueue(conn,id)
    return {'status':'QUEUED'}

@app.get('/stories/{id}/execution/artifacts/{name}')
def download_execution_artifact(id:uuid.UUID,name:str,user=Depends(current_user)):
    from app.execution import get_run
    from app.delivery import ARTIFACT_FILES
    from app.storage import read_artifact,StorageError
    require_story(id);record=get_run(id)
    if name not in ARTIFACT_FILES or not record or name not in (record['artifacts'] or {}):raise HTTPException(404,'Артефакт не найден')
    try:content=read_artifact(record['id'],name,record['artifacts'][name])
    except StorageError:raise HTTPException(503,'Файл недоступен или не прошёл проверку целостности') from None
    return Response(content,headers={'Content-Disposition':'attachment; filename="'+name+'"'},media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' if name.endswith('.xlsx') else 'application/octet-stream')

@app.post('/stories/{id}/execution/gate')
def execution_gate(id:uuid.UUID,user=Depends(current_user)):
    from app.execution import get_run
    from app.delivery import broker
    require_story(id);record=get_run(id)
    if not record or not (record['git'] or {}).get('pulls'):raise db.Conflict('PR ещё не опубликованы')
    return broker('/gate',{'pulls':record['git']['pulls']})

@app.post('/stories/{id}/execution/accept',status_code=202)
def accept_execution(id:uuid.UUID,data:Approval,user=Depends(current_user)):
    from app.execution import get_run
    from app.delivery import broker
    from app.integrations import tracker
    require_story(id)
    with db.story_lock(id):
        story=db.active(id);record=get_run(id)
        if not record or record['status']!='ACCEPTANCE' or story['status']!='ACCEPTANCE':raise db.Conflict('Результат не готов к приёмке')
        if story['revision']!=data.revision or record['analytics_digest']!=data.plan_digest:raise db.Conflict('Ревизия изменилась')
        tracker.honor_remote_stop(id)
        gate=broker('/gate',{'pulls':record['git']['pulls']})
        if not gate['eligible']:raise HTTPException(409,gate)
        with db.connection() as conn:
            conn.execute("UPDATE execution_runs SET status='BUSINESS_ACCEPTED',business_accepted_by=%s,business_accepted_at=now(),updated_at=now() WHERE id=%s",(user,record['id']))
            db.event(conn,story,'execution.business_accepted',{'actor':user,'gate':gate},f"{record['id']}:business-accepted")
            db.enqueue(conn,id)
    return {'status':'BUSINESS_ACCEPTED'}

@app.post('/stories/{id}/execution/finalize-release',status_code=202)
def finalize_execution_release(id:uuid.UUID,user=Depends(current_user)):
    require_story(id)
    with db.story_lock(id):
        story=db.active(id)
        with db.connection() as conn:
            record=conn.execute('SELECT * FROM execution_runs WHERE story_id=%s AND revision=%s',(id,story['revision'])).fetchone()
            if not record or record['status'] not in ['RELEASE_REVIEW','FINALIZING_RELEASE']:raise db.Conflict('Релиз пока недоступен')
            conn.execute("UPDATE execution_runs SET status='FINALIZING_RELEASE' WHERE id=%s",(record['id'],))
            db.event(conn,story,'execution.release_finalization_requested',{'actor':user})
            db.enqueue(conn,id)
    return {'status':'FINALIZING_RELEASE'}

from app.product_api import router as product_router
app.include_router(product_router)
