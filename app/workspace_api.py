"""Task source snapshots and host-only check leases."""
import hashlib
import uuid
from typing import Literal
from fastapi import APIRouter,Depends,HTTPException,Response
from pydantic import Field,field_validator
from psycopg.types.json import Jsonb
from app import db,workspaces
from app.auth import current_user
from app.schemas import StrictModel
from app.product_api import operator

router=APIRouter()

class SourceObservation(StrictModel):
    source_id:str=Field(pattern=r'^[a-z][a-z0-9_-]{0,39}$')
    url:str=Field(max_length=2000)
    format:Literal['csv','json','xml','zip','xlsx']
    bytes:int=Field(ge=0,le=8_000_000)
    sha256:str=Field(pattern=r'^[a-f0-9]{64}$')
    content_type:str=Field(default='',max_length=200)
    preview:str=Field(default='',max_length=4000)

class WorkspaceReceipt(StrictModel):
    operation_id:uuid.UUID
    snapshot_sha256:str=Field(pattern=r'^[a-f0-9]{64}$')
    passed:bool
    tests_run:int=Field(default=0,ge=0)
    tests_present:bool=False
    test_output:str=Field(default='',max_length=12000)
    source_observation:dict=Field(default_factory=dict,max_length=8)
    error_type:Literal['','PolicyRejected','BuildFailed','TestsFailed','OperatorFailed']=''

    @field_validator('source_observation')
    @classmethod
    def bounded_source_observation(cls,value):
        return SourceObservation.model_validate(value).model_dump() if value else {}


@router.get('/stories/{id}/workspace')
def state(id:uuid.UUID,user=Depends(current_user)):
    story=db.get_story(id)
    if not story:raise HTTPException(404,'Story not found')
    return workspaces.public(workspaces.get(id,story['revision']))


@router.post('/workspace-operations/claim')
def claim(user=Depends(operator)):
    with db.connection() as conn:
        row=conn.execute('''SELECT t.*,w.story_id,w.revision,w.definition FROM workspace_tasks t
          JOIN product_workspaces w ON w.id=t.workspace_id JOIN stories s ON s.id=w.story_id
          WHERE t.state='CHECK_PENDING' AND s.approved AND s.revision=w.revision
          AND s.status NOT IN ('BLOCKED','FAILED','CANCELLED') AND (t.lease_until IS NULL OR t.lease_until<now())
          ORDER BY t.updated_at FOR UPDATE OF t SKIP LOCKED LIMIT 1''').fetchone()
        if not row:return {'operation':None}
        workspace=workspaces.get(row['story_id'],row['revision'],conn)
        content,manifest=workspaces.task_snapshot(workspace,row);digest=hashlib.sha256(content).hexdigest();operation=uuid.uuid4()
        conn.execute("UPDATE workspace_tasks SET operation_id=%s,snapshot_digest=%s,lease_until=now()+interval '10 minutes' WHERE id=%s",(operation,digest,row['id']))
        return {'operation':'workspace-check','id':row['id'],'workspace_id':row['workspace_id'],'task_id':row['task_id'],
                'operation_id':operation,'snapshot_sha256':digest,'source_digest':manifest['source_digest']}


@router.get('/workspace-operations/{id}/snapshot')
def snapshot(id:uuid.UUID,user=Depends(current_user)):
    with db.connection() as conn:
        row=conn.execute('SELECT * FROM workspace_tasks WHERE id=%s',(id,)).fetchone()
        if not row:raise HTTPException(404,'Task not found')
        workspace=conn.execute('SELECT * FROM product_workspaces WHERE id=%s',(row['workspace_id'],)).fetchone()
        content,_=workspaces.task_snapshot(workspace,row)
    return Response(content,media_type='application/zip')


def active(conn,id,data):
    row=conn.execute('''SELECT t.*,w.story_id,w.revision FROM workspace_tasks t JOIN product_workspaces w ON w.id=t.workspace_id
      WHERE t.id=%s FOR UPDATE OF t''',(id,)).fetchone()
    if not row:raise HTTPException(404,'Task not found')
    story=db.get_story(row['story_id'],conn)
    if not story['approved'] or story['revision']!=row['revision'] or story['status'] in ['CANCELLED','FAILED','BLOCKED']:
        raise db.Conflict('Workspace operation stopped')
    if row['operation_id']!=data.operation_id or row['snapshot_digest']!=data.snapshot_sha256:raise db.Conflict('Workspace operation changed')
    return row,story


@router.post('/workspace-operations/{id}/validate')
def validate(id:uuid.UUID,data:WorkspaceReceipt,user=Depends(operator)):
    with db.connection() as conn:
        row,_=active(conn,id,data)
        if row['state']!='CHECK_PENDING' or not row['lease_until'] or not conn.execute('SELECT %s>now() AS active',(row['lease_until'],)).fetchone()['active']:
            raise db.Conflict('Workspace lease expired')
    return {'active':True}


@router.post('/workspace-operations/{id}/report')
def report(id:uuid.UUID,data:WorkspaceReceipt,user=Depends(operator)):
    receipt=data.model_dump(mode='json')
    with db.connection() as conn:
        row,story=active(conn,id,data)
        if row['state']!='CHECK_PENDING':
            if row['checks']==receipt:return {'state':row['state'],'replayed':True}
            raise db.Conflict('Workspace receipt changed')
        if not row['lease_until'] or not conn.execute('SELECT %s>now() AS active',(row['lease_until'],)).fetchone()['active']:raise db.Conflict('Workspace lease expired')
        if data.tests_present!=(data.tests_run>0):raise db.Conflict('Invalid test accounting')
        if len(str(data.source_observation))>9000:raise db.Conflict('Source observation too large')
        research=(row['intent'] or '').startswith('research:')
        if data.passed and research and data.source_observation.get('source_id')!=row['intent'].split(':',1)[1]:raise db.Conflict('Source receipt does not match research request')
        if not research and data.source_observation:raise db.Conflict('Unexpected source receipt')
        if research and data.passed:
            definition=conn.execute('SELECT definition FROM product_workspaces WHERE id=%s',(row['workspace_id'],)).fetchone()['definition']
            source=next(s for s in definition.get('sources',[]) if s['id']==data.source_observation['source_id'])
            if any(data.source_observation.get(k)!=source[k] for k in ['url','format']):raise db.Conflict('Source URL or format changed')
            conn.execute('INSERT INTO source_inspections(id,workspace_id,task_run_id,source_id,receipt) VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                (data.operation_id,row['workspace_id'],row['id'],source['id'],Jsonb(data.source_observation)))
        if not data.passed:state='CHECK_FAILED'
        elif row['intent']=='continue' or (row['intent'] or '').startswith('research:'):state='RUNNING'
        else:state='CHECKED' if data.passed else 'CHECK_FAILED'
        observations=(row['observations']+[{'actual_checks':receipt}])[-12:]
        conn.execute('UPDATE workspace_tasks SET state=%s,checks=%s,observations=%s,lease_until=NULL,updated_at=now() WHERE id=%s',
            (state,Jsonb(receipt),Jsonb(observations),id))
        db.event(conn,story,'workspace.checked',{'task_id':row['task_id'],'passed':data.passed,'tests_run':data.tests_run},str(data.operation_id))
        db.enqueue(conn,story['id'])
    return {'state':state}


@router.get('/stories/{story_id}/repairs/{attempt_id}/sources')
def repair_sources(story_id:uuid.UUID,attempt_id:uuid.UUID,user=Depends(current_user)):
    with db.connection() as c:
        row=c.execute('SELECT source_files,source_digest,scope,attempt FROM repair_history WHERE id=%s AND story_id=%s',(attempt_id,story_id)).fetchone()
        if not row:raise HTTPException(404,'Repair attempt not found')
        return row
