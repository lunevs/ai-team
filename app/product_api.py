"""Authenticated product registry and leased host-operator protocol."""
import base64
import hashlib
import json
import hmac
import os
import uuid
from typing import Literal
from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import Field
from psycopg.types.json import Jsonb
from app import db, products
from app.auth import current_user
from app.schemas import StrictModel, Approval

router=APIRouter()

def operator(authorization:str=Header(default='')):
    token=os.getenv('PRODUCT_OPERATOR_TOKEN','')
    if not token or not hmac.compare_digest(authorization,'Bearer '+token):raise HTTPException(401,'Unauthorized operator')
    return 'product-operator'

class OperatorReport(StrictModel):
    operation_id:uuid.UUID
    package_sha256:str=Field(pattern=r'^[a-f0-9]{64}$')
    passed:bool
    tests_passed:bool=False
    health_passed:bool=False
    image_id:str=Field(default='',max_length=100,pattern=r'^(sha256:[a-f0-9]{64})?$')
    result:dict=Field(default_factory=dict,max_length=8)
    evidence:dict=Field(default_factory=dict,max_length=6)
    error_type:Literal['','EvidenceFailed','BuildFailed','TestsFailed','HealthFailed','DeploymentFailed','PolicyRejected','OperatorFailed']=''
    port:int|None=Field(default=None,ge=1024,le=65535)

@router.get('/products')
def list_products(user=Depends(current_user)):
    with db.connection() as conn:
        return [products.public(r) for r in conn.execute('SELECT * FROM product_releases ORDER BY created_at DESC LIMIT 100').fetchall()]

@router.get('/stories/{id}/product')
def product(id:uuid.UUID,user=Depends(current_user)):
    if not db.get_story(id):raise HTTPException(404,'Story not found')
    return products.public(products.get(id))

@router.get('/products/{id}/package')
def package(id:uuid.UUID,user=Depends(current_user)):
    with db.connection() as conn:record=conn.execute('SELECT * FROM product_releases WHERE id=%s',(id,)).fetchone()
    if not record:raise HTTPException(404,'Product version not found')
    return Response(products.package_bytes(record),media_type='application/zip',headers={'Content-Disposition':f'attachment; filename="{record["product_key"]}-{record["version"]}.zip"'})

@router.post('/stories/{id}/product/refresh')
def refresh(id:uuid.UUID,user=Depends(current_user)):
    story=db.active(id)
    if story['delivery_mode']!='product' or not story['approved']:raise db.Conflict('Approved product plan required')
    with db.connection() as conn:db.enqueue(conn,id)
    return {'status':'queued'}

@router.post('/stories/{id}/product/accept')
def accept(id:uuid.UUID,data:Approval,user=Depends(current_user)):
    with db.story_lock(id):
        story=db.active(id);record=products.get(id)
        if not record or record['status']!='ACCEPTANCE':raise db.Conflict('Deploy and verify product before acceptance')
        if story['revision']!=data.revision or story['plan_digest']!=data.plan_digest:raise db.Conflict('Plan changed')
        from app.product_assurance import ready
        if not ready(record,story) or not products.outputs_ready(record):raise db.Conflict('Проверки результата отсутствуют, устарели или не пройдены')
        from app.delivery import broker
        if not broker('/gate',{'pulls':record['git']['pulls']})['eligible']:raise db.Conflict('Independent Git review/CI/merge gate incomplete')
        products.update(record['id'],accepted=True)
        with db.connection() as conn:
            db.event(conn,story,'product.accepted',{'actor':user,'product_release':str(record['id'])},f"{record['id']}:accepted")
            db.enqueue(conn,id)
    return {'accepted':True,'release_merge_required':True}

@router.post('/stories/{id}/product/retry-deployment')
def retry_deployment(id:uuid.UUID,data:Approval,user=Depends(current_user)):
    from app.delivery import broker
    with db.story_lock(id):
        story=db.active(id);record=products.get(id)
        if not record or record['status']!='DEPLOY_FAILED':raise db.Conflict('Deployment is not awaiting retry')
        if story['revision']!=data.revision or story['plan_digest']!=data.plan_digest:raise db.Conflict('Plan changed')
        if not broker('/gate',{'pulls':record['git']['pulls']})['eligible']:raise db.Conflict('Independent Git review/CI/merge gate incomplete')
        products.update(record['id'],status='DEPLOY_READY')
    return {'status':'DEPLOY_READY'}

@router.post('/product-operations/claim')
def claim(user=Depends(operator)):
    with db.connection() as conn:
        record=conn.execute('''SELECT p.* FROM product_releases p JOIN stories s ON s.id=p.story_id
            WHERE p.status IN ('CHECK_PENDING','DEPLOY_READY') AND s.status NOT IN ('CANCELLED','BLOCKED','FAILED')
            AND s.approved AND s.revision=p.revision AND (p.lease_until IS NULL OR p.lease_until<now())
            ORDER BY p.created_at FOR UPDATE OF p SKIP LOCKED LIMIT 1''').fetchone()
        if not record:return {'operation':None}
        operation='check' if record['status']=='CHECK_PENDING' else 'deploy'
        id=uuid.uuid4()
        conn.execute("UPDATE product_releases SET operation=%s,operation_id=%s,lease_until=now()+interval '10 minutes' WHERE id=%s",(operation,id,record['id']))
        return {'operation':operation,'operation_id':id,'id':record['id'],'story_id':record['story_id'],
            'product_key':record['product_key'],'version':record['version'],'package':record['package'],
            'deployment':record['deployment'],'qa':record['qa'],'assurance_version':record['assurance_version'],'verification':record['verification']}

@router.post('/product-operations/{id}/validate')
def validate_operation(id:uuid.UUID,data:OperatorReport,user=Depends(operator)):
    with db.connection() as conn:
        row=conn.execute('''SELECT p.* FROM product_releases p JOIN stories s ON s.id=p.story_id
            WHERE p.id=%s AND p.operation_id=%s AND p.lease_until>now() AND s.revision=p.revision
            AND s.approved AND s.status NOT IN ('CANCELLED','BLOCKED','FAILED')''',(id,data.operation_id)).fetchone()
        if not row or row['package']['sha256']!=data.package_sha256:raise db.Conflict('Operation stopped or changed')
    if row['operation']=='deploy':
        from app.delivery import broker
        if not broker('/gate',{'pulls':row['git']['pulls']})['eligible']:raise db.Conflict('Git gate changed before deployment')
    return {'active':True}

@router.post('/product-operations/{id}/report')
def report(id:uuid.UUID,data:OperatorReport,user=Depends(operator)):
    with db.connection() as conn:
        record=conn.execute('SELECT * FROM product_releases WHERE id=%s FOR UPDATE',(id,)).fetchone()
        if not record or record['operation_id']!=data.operation_id:raise db.Conflict('Operation lease changed')
        story=db.get_story(record['story_id'],conn)
        if record['package']['sha256']!=data.package_sha256:raise db.Conflict('Package changed')
        from app.product_assurance import digest,receipt_valid
        report_digest=digest(data.model_dump(mode='json'))
        if record['lease_until'] is None:
            previous=conn.execute('SELECT report_digest FROM product_evidence WHERE operation_id=%s',(data.operation_id,)).fetchone()
            if not previous or previous['report_digest']!=report_digest:raise db.Conflict('Completed operation report differs')
            return {'status':record['status']}
        if story['status'] in {'CANCELLED','BLOCKED','FAILED'} or story['revision']!=record['revision']:raise db.Conflict('Story stopped or revised')
        if not conn.execute('SELECT %s>now() AS active',(record['lease_until'],)).fetchone()['active']:raise db.Conflict('Operation lease expired')
        operation=record['operation']
        evidence_ok=record['assurance_version']==0
        receipt=data.evidence.copy()
        outputs=receipt.get('outputs',[])
        if len(json.dumps(receipt))>3_000_000 or not isinstance(outputs,list) or len(outputs)>12:raise db.Conflict('Oversized evidence')
        verified_outputs=[];total=0
        contract=record.get('verification')
        if record['assurance_version']==1:
            evidence_ok=bool(contract and receipt_valid(contract,receipt,operation))
            allowed={c['path'] for role in ('qa','financial_analyst') for c in contract[role]['checks'] if c['source']!='result'} if contract else set()
            seen=set()
            for output in outputs:
                if set(output)!={'path','sha256','data'} or output['path'] not in allowed or output['path'] in seen:raise db.Conflict('Unexpected output')
                seen.add(output['path'])
                try:raw=base64.b64decode(output['data'],validate=True)
                except (ValueError,TypeError):raise db.Conflict('Invalid output encoding')
                total+=len(raw)
                if len(raw)>1048576 or total>2097152 or hashlib.sha256(raw).hexdigest()!=output['sha256']:raise db.Conflict('Output integrity failure')
                verified_outputs.append((output,raw))
            if record['specification']['output_type'] in {'file','presentation'} and not verified_outputs:evidence_ok=False
            receipt['outputs']=[{k:v for k,v in output.items() if k!='data'} for output,_ in verified_outputs]
        conn.execute('INSERT INTO product_evidence(operation_id,release_id,receipt,report_digest) VALUES(%s,%s,%s,%s)',
                     (data.operation_id,id,Jsonb(receipt),report_digest))
        for output,raw in verified_outputs:
            conn.execute('INSERT INTO product_outputs(id,release_id,operation_id,path,sha256,content) VALUES(%s,%s,%s,%s,%s,%s)',
                         (uuid.uuid4(),id,data.operation_id,output['path'],output['sha256'],raw))
        report_data=data.model_dump(mode='json')|{'evidence':receipt}
        if operation=='check':
            passed=evidence_ok and data.passed and data.tests_passed and data.health_passed and bool(data.image_id)
            workspace_mode=(story.get('plan') or {}).get('development_mode')=='workspace'
            status='CHECKED' if passed else 'REPAIR_PENDING' if workspace_mode else 'ESCALATED'
            conn.execute('UPDATE product_releases SET status=%s,qa=%s,lease_until=NULL,updated_at=now() WHERE id=%s',
                (status,Jsonb(report_data),id))
            if passed or workspace_mode:db.enqueue(conn,record['story_id'])
            else:
                conn.execute("UPDATE stories SET status='BLOCKED',error_type='ProductChecksFailed',updated_at=now() WHERE id=%s",(story['id'],))
                conn.execute('INSERT INTO status_outbox(story_id) VALUES(%s) ON CONFLICT DO NOTHING',(story['id'],))
        else:
            passed=evidence_ok and data.passed and data.health_passed and data.image_id==(record['qa'] or {}).get('image_id')
            deployment={'operator':user,'image_id':data.image_id,'package_sha256':data.package_sha256,
                'url':f'http://127.0.0.1:{data.port}' if data.port else None,'result':data.result,
                'evidence':receipt,'operation_id':str(data.operation_id),'error_type':data.error_type}
            if passed and record['assurance_version']==1:
                from app.product_assurance import ready
                passed=ready(record|{'deployment':deployment},story)
            status='ACCEPTANCE' if passed else ('REPAIR_PENDING' if data.error_type=='EvidenceFailed' and (story.get('plan') or {}).get('development_mode')=='workspace' else 'DEPLOY_FAILED')
            conn.execute('UPDATE product_releases SET status=%s,deployment=%s,lease_until=NULL,updated_at=now() WHERE id=%s',
                (status,Jsonb(deployment),id))
            if status=='REPAIR_PENDING':db.enqueue(conn,record['story_id'])
            # Tracker sync is done by the worker via a durable outbox, outside this transaction.
            if passed:
                conn.execute("UPDATE stories SET status='ACCEPTANCE',updated_at=now() WHERE id=%s",(story['id'],))
                conn.execute('INSERT INTO status_outbox(story_id) VALUES(%s) ON CONFLICT DO NOTHING',(story['id'],))
        db.event(conn,story,'product.operation.completed',{'operation':operation,'status':status,'release_id':str(id)},f'{data.operation_id}:report')
        return {'status':status}


@router.get('/products/{id}/outputs/{output_id}')
def download_output(id:uuid.UUID,output_id:uuid.UUID,user=Depends(current_user)):
    with db.connection() as conn:
        row=conn.execute('SELECT path,sha256,content FROM product_outputs WHERE id=%s AND release_id=%s',(output_id,id)).fetchone()
    if not row:raise HTTPException(404,'Output not found')
    content=bytes(row['content'])
    if hashlib.sha256(content).hexdigest()!=row['sha256']:raise HTTPException(409,'Output integrity failure')
    name=row['path'].rsplit('/',1)[-1]
    return Response(content,media_type='application/octet-stream',headers={'Content-Disposition':f'attachment; filename="{name}"','X-Content-Type-Options':'nosniff'})
