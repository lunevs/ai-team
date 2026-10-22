"""Durable bounded developer/QA loop. No model code runs in the worker process."""
import os
import uuid
import hashlib
import httpx
from psycopg.types.json import Jsonb
from app import db
from app.providers import generate
from app.schemas import SQLArtifact,SQLReview
from app.synthetic import metadata
from app.qa import check_rows
from app.integrations import tracker,knowledge,sync_status,ensure_main,RetryableIntegrationError


def get_run(story_id,revision=None):
    with db.connection() as conn:
        if revision is None:revision=db.get_story(story_id,conn)['revision']
        return conn.execute('SELECT * FROM execution_runs WHERE story_id=%s AND revision=%s',(story_id,revision)).fetchone()


def update(id,**values):
    from psycopg import sql
    allowed={'status','attempt','sql_text','rows','qa','review','git','artifacts'}
    if not values or not set(values)<=allowed:raise ValueError('Unknown execution field')
    with db.connection() as conn:
        assignments=sql.SQL(',').join(sql.SQL('{}=%s').format(sql.Identifier(k)) for k in values)
        conn.execute(sql.SQL('UPDATE execution_runs SET {},updated_at=now() WHERE id=%s').format(assignments),
                     tuple(Jsonb(v) if isinstance(v,(dict,list)) else v for v in values.values())+(id,))


def event(story,type_,payload,key):
    with db.connection() as conn:db.event(conn,story,type_,payload,key)


def sandbox_call(run,attempt,query):
    try:
        with httpx.Client(timeout=15) as client:
            r=client.post(os.getenv('SANDBOX_URL','http://sandbox:8000')+'/execute',headers={'Authorization':'Bearer '+os.getenv('SANDBOX_API_TOKEN','')},
                json={'run_id':str(run['id']),'attempt':attempt,'select_sql':query})
    except httpx.TransportError:raise RetryableIntegrationError('Sandbox unavailable') from None
    if r.status_code==422:return {'rows':[],'execution_error':r.json().get('detail','SQL rejected')}
    if r.status_code>=400:raise RetryableIntegrationError('Sandbox HTTP '+str(r.status_code))
    return r.json()


def publish_defect(story,run,attempt,qa):
    main=ensure_main(story['id'])
    description='QA обнаружил расхождения.\n\n'+str(qa['failures'])+'\n\nTrace ID: '+story['trace_id']
    defect=tracker.ensure(story['id'],f"r{story['revision']}:execution-defect:{attempt}",
        f'Дефект SQL: попытка {attempt}',description,7,parent=main,assignee=__import__('app.config',fromlist=['settings']).settings.agent_ids.get('data_developer'))
    event(story,'execution.defect',{'attempt':attempt,'work_package_id':defect},f"{run['id']}:defect:{attempt}")


def run(story_id):
    story=db.active(story_id);tracker.honor_remote_stop(story_id)
    record=get_run(story_id)
    if record['status'] in ['ACCEPTANCE','RELEASE_REVIEW','DONE','ESCALATED']:return
    if record['status']=='BUSINESS_ACCEPTED':
        from app.delivery import complete_acceptance
        complete_acceptance(story,record);return
    if record['status']=='FINALIZING_RELEASE':
        from app.delivery import finalize_delivery
        finalize_delivery(story,record);return
    if record['status'] in ['READY_TO_PUBLISH','WAITING_GITHUB','PUBLISHED']:
        finish_delivery(story,record);return
    with db.connection() as conn:
        analytics=conn.execute('SELECT * FROM analytics_runs WHERE story_id=%s AND revision=%s',(story_id,story['revision'])).fetchone()
        info=metadata(conn)
    if not analytics or analytics['status']!='APPROVED' or analytics['digest']!=record['analytics_digest']:
        raise db.Conflict('Analytical approval changed')
    feedback=[]
    for attempt in range(1,4):
        story=db.active(story_id);tracker.honor_remote_stop(story_id)
        with db.connection() as conn:
            prior=conn.execute('SELECT * FROM execution_attempts WHERE run_id=%s AND attempt=%s',(record['id'],attempt)).fetchone()
        if prior:
            artifact_query=prior['sql_text'];qa=prior['qa'];result=prior['result']
        else:
            update(record['id'],status='DEVELOPING',attempt=attempt)
            artifact=generate(story_id,'data_developer',SQLArtifact,{
                'story':story['text'],'attempt':attempt,'metadata':info,'passport':analytics['financial_spec']['passport'],
                'source_mapping':analytics['source_spec']['mapping'],'feedback':feedback,
                'instruction':'Write one PostgreSQL SELECT with CTEs. Only synthetic.dim_customer/dim_product/fact_loan/dim_calendar/fact_loan_daily. No DDL/DML, VALUES, recursion, locking, file/network/system functions. Sources must be schema-qualified. Use dim_calendar.month and is_month_end (not date functions). Complete grid 6×2×2. month,product_id,region,balance,dpd30_balance,dpd90_balance,npl90_ratio,interest_income. Snapshot and income must aggregate independently before joining. No observations => NULL, observed zero => 0. NULL ratio at zero denominator. round numeric money to 2 and ratio to 6. Functions allowed: sum,count,min,max,round,avg,abs; coalesce and nullif. Output concise SQL under 6000 chars.'},
                result_role=f'data_developer_{record["id"].hex}_a{attempt}')
            artifact_query=artifact.select_sql
            db.active(story_id);tracker.honor_remote_stop(story_id)
            update(record['id'],status='QA',sql_text=artifact_query)
            result=sandbox_call(record,attempt,artifact_query)
            qa=check_rows(result['rows']) if 'execution_error' not in result else {'passed':False,'failures':[{'check':'SQL_execution','detail':result['execution_error']}],'expected_rows':24,'actual_rows':0}
            with db.connection() as conn:
                conn.execute('INSERT INTO execution_attempts(run_id,attempt,sql_text,result,qa) VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                    (record['id'],attempt,artifact_query,Jsonb(result),Jsonb(qa)))
            event(story,'execution.qa',{'attempt':attempt,'passed':qa['passed']},f'{record["id"]}:qa:{attempt}')
        if not qa['passed']:
            publish_defect(story,record,attempt,qa);feedback=qa['failures']
            if attempt==3:
                update(record['id'],status='ESCALATED',qa=qa,attempt=attempt)
                sync_status(story_id,'BLOCKED');return
            continue
        update(record['id'],status='ARCHITECT_REVIEW',rows=result['rows'],qa=qa,sql_text=artifact_query,attempt=attempt)
        review=generate(story_id,'system_analyst',SQLReview,{
            'sql':artifact_query,'passport':analytics['financial_spec']['passport'],'qa':qa,
            'instruction':'Review the actual SQL for contract and security. QA is deterministic, not model-authored. Check joins, no double counting, month-end and monthly income, complete grid, missing versus zero, DPD >= thresholds, NULL ratios. Approve if correct. Return only concrete findings.'},
            result_role=f'system_analyst_review_{record["id"].hex}_a{attempt}')
        if not review.approved:
            qa={'passed':False,'failures':[{'check':'architecture','detail':v} for v in review.findings]}
            publish_defect(story,record,attempt,qa);feedback=qa['failures']
            if attempt==3:
                update(record['id'],status='ESCALATED',qa=qa,review=review.model_dump())
                sync_status(story_id,'BLOCKED');return
            continue
        update(record['id'],status='READY_TO_PUBLISH',review=review.model_dump())
        record=get_run(story_id);finish_delivery(story,record);return


def finish_delivery(story,record):
    # Implemented by delivery/Git adapters; no silent fake publication.
    from app.delivery import prepare_and_publish
    prepare_and_publish(story,record)
