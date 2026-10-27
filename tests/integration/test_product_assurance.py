"""Real PostgreSQL protocol tests; no model/GitHub approvals are represented."""
import copy
import uuid
import pytest
from psycopg.types.json import Jsonb
from fastapi import HTTPException
from app import db,products
from app.schemas import StoryInput,Approval
from app.product_api import claim,report,accept,download_output,OperatorReport
from app.product_assurance import make_contract
def contract():
    check={'id':'total','criterion':0,'description':'Контроль суммы по требованиям','basis':'Заранее рассчитано 10 + 20 = 30 рублей','source':'result','path':'','pointer':'/total','operator':'number','expected':30,'tolerance':'0'}
    plan={'checks':[check],'not_applicable_reason':''}
    return make_contract({'package':{'sha256':'a'*64},'specification':{'output_type':'scalar'}},{'pm':{'acceptance_criteria':['Итог 30 рублей']},'revision':1,'plan_digest':'c'*64},plan,copy.deepcopy(plan))
from scripts.product_verifier import evaluate

pytestmark=pytest.mark.integration


def test_missing_forged_and_stale_proofs_cannot_reach_acceptance(monkeypatch):
    story,_=db.create_story(StoryInput(text='Изолированная техническая проверка доказательств; не банковская приёмка',delivery_mode='product',synthetic_only=True),'product-assurance-'+str(uuid.uuid4()))
    sid=story['id'];rid=uuid.uuid4();c=contract();package={'sha256':'a'*64,'size':0,'backend':'local'}
    try:
        with db.connection() as conn:
            conn.execute("UPDATE stories SET approved=true,status='IN_PROGRESS',plan_digest='cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc',pm=%s WHERE id=%s",(Jsonb({'acceptance_criteria':c['criteria']}),sid))
            conn.execute("INSERT INTO product_releases(id,story_id,revision,product_key,version,status,package,assurance_version,verification,specification) VALUES(%s,%s,1,%s,'1.0.1','CHECK_PENDING',%s,1,%s,%s)",
                         (rid,sid,'p-'+uuid.UUID(str(sid)).hex[:12],Jsonb(package),Jsonb(c),Jsonb({'output_type':'scalar'})))
        operation=claim('fixture-operator');assert operation['id']==rid and operation['verification']==c
        data=OperatorReport(operation_id=operation['operation_id'],package_sha256='a'*64,passed=True,tests_passed=True,health_passed=True,image_id='sha256:'+'b'*64)
        assert report(rid,data,'fixture-operator')['status']=='ESCALATED'
        assert report(rid,data,'fixture-operator')['status']=='ESCALATED'
        altered=data.model_copy(update={'passed':False})
        with pytest.raises(db.Conflict):report(rid,altered,'fixture-operator')
        with db.connection() as conn:
            conn.execute("UPDATE product_releases SET status='ACCEPTANCE' WHERE id=%s",(rid,))
            conn.execute("UPDATE stories SET status='IN_PROGRESS' WHERE id=%s",(sid,))
        with pytest.raises(db.Conflict):accept(sid,Approval(revision=1,plan_digest='cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc'),'fixture-customer')
        with pytest.raises(HTTPException):download_output(rid,uuid.uuid4(),'fixture-customer')
    finally:
        with db.connection() as conn:
            conn.execute('DELETE FROM product_outputs WHERE release_id=%s',(rid,));conn.execute('DELETE FROM product_evidence WHERE release_id=%s',(rid,))
            conn.execute('DELETE FROM product_releases WHERE story_id=%s',(sid,));conn.execute('DELETE FROM events WHERE story_id=%s',(sid,))
            conn.execute('DELETE FROM status_outbox WHERE story_id=%s',(sid,));conn.execute('DELETE FROM jobs WHERE story_id=%s',(sid,));conn.execute('DELETE FROM stories WHERE id=%s',(sid,))


def test_successful_exact_version_proofs_publish_original_file_and_require_git_gate(monkeypatch):
    import base64
    from app.product_assurance import digest
    from app import delivery
    story,_=db.create_story(StoryInput(text='Изолированная fixture выдачи CSV с независимым числовым контролем',delivery_mode='product',synthetic_only=True),'output-proof-'+str(uuid.uuid4()))
    sid=story['id'];rid=uuid.uuid4();c=contract();c['output_type']='file'
    for role in ('qa','financial_analyst'):
        c[role]['checks'].append(c[role]['checks'][0]|{'id':'rows','source':'csv_file','path':'report.csv','pointer':'','operator':'min_count','expected':1})
    c['digest']=digest({k:v for k,v in c.items() if k!='digest'})
    raw=b'total\n30\n';collected={'result:':{'data':base64.b64encode(b'{"total":30}').decode()},'csv_file:report.csv':{'data':base64.b64encode(raw).decode()}}
    reviews={'contract_digest':c['digest'],**{r:{'approved':True} for r in ('qa','architecture','financial')}}
    image='sha256:'+'b'*64
    try:
        with db.connection() as conn:
            conn.execute("UPDATE stories SET approved=true,status='IN_PROGRESS',plan_digest=%s,pm=%s WHERE id=%s",('c'*64,Jsonb({'acceptance_criteria':c['criteria']}),sid))
            conn.execute("INSERT INTO product_releases(id,story_id,revision,product_key,version,status,package,assurance_version,verification,specification,review,git) VALUES(%s,%s,1,%s,'1.0.1','CHECK_PENDING',%s,1,%s,%s,%s,%s)",
                (rid,sid,'p-'+uuid.UUID(str(sid)).hex[:12],Jsonb({'sha256':'a'*64}),Jsonb(c),Jsonb({'output_type':'file'}),Jsonb(reviews),Jsonb({'pulls':[]})))
        check=claim('fixture-operator')
        assert report(rid,OperatorReport(operation_id=check['operation_id'],package_sha256='a'*64,passed=True,tests_passed=True,health_passed=True,image_id=image,evidence=evaluate(c,'check',collected)),'fixture-operator')['status']=='CHECKED'
        with db.connection() as conn:conn.execute("UPDATE product_releases SET status='DEPLOY_READY' WHERE id=%s",(rid,))
        deploy=claim('fixture-operator')
        assert report(rid,OperatorReport(operation_id=deploy['operation_id'],package_sha256='a'*64,passed=True,health_passed=True,image_id=image,evidence=evaluate(c,'deploy',collected)),'fixture-operator')['status']=='ACCEPTANCE'
        public=products.public(products.get(sid));assert public['ready_for_acceptance']
        output=public['outputs'][0]
        assert download_output(rid,output['id'],'fixture-customer').body==raw
        with pytest.raises(HTTPException):download_output(uuid.uuid4(),output['id'],'fixture-customer')
        monkeypatch.setattr(delivery,'broker',lambda *a,**kw:{'eligible':False})
        with pytest.raises(db.Conflict):accept(sid,Approval(revision=1,plan_digest='c'*64),'fixture-customer')
        with db.connection() as conn:conn.execute('UPDATE product_outputs SET content=%s WHERE id=%s',(b'corrupted',output['id']))
        assert not products.public(products.get(sid))['ready_for_acceptance']
        with pytest.raises(HTTPException):download_output(rid,output['id'],'fixture-customer')
        with pytest.raises(db.Conflict):accept(sid,Approval(revision=1,plan_digest='c'*64),'fixture-customer')
        with db.connection() as conn:conn.execute('UPDATE product_outputs SET content=%s WHERE id=%s',(raw,output['id']))
        # Explicit test double: not a GitHub review or permission to merge a real PR.
        monkeypatch.setattr(delivery,'broker',lambda *a,**kw:{'eligible':True})
        assert accept(sid,Approval(revision=1,plan_digest='c'*64),'fixture-customer')['accepted']
        with db.connection() as conn:conn.execute('UPDATE product_releases SET package=%s,accepted=false WHERE id=%s',(Jsonb({'sha256':'d'*64}),rid))
        with pytest.raises(db.Conflict):accept(sid,Approval(revision=1,plan_digest='c'*64),'fixture-customer')
    finally:
        with db.connection() as conn:
            conn.execute('DELETE FROM product_outputs WHERE release_id=%s',(rid,));conn.execute('DELETE FROM product_evidence WHERE release_id=%s',(rid,))
            conn.execute('DELETE FROM product_releases WHERE story_id=%s',(sid,));conn.execute('DELETE FROM events WHERE story_id=%s',(sid,))
            conn.execute('DELETE FROM status_outbox WHERE story_id=%s',(sid,));conn.execute('DELETE FROM jobs WHERE story_id=%s',(sid,));conn.execute('DELETE FROM stories WHERE id=%s',(sid,))


def test_dismissed_product_finding_requires_new_review_round_without_reusing_approval(monkeypatch):
    from app import rework,workspaces
    story,_=db.create_story(StoryInput(text='Изолированный тест повторного ревью после ошибочного замечания',synthetic_only=True,delivery_mode='product'),'product-rereview-'+str(uuid.uuid4()))
    sid=story['id'];rid=uuid.uuid4();c=contract()
    try:
        with db.connection() as conn:
            conn.execute("INSERT INTO product_releases(id,story_id,revision,product_key,version,status,assurance_version,verification,review) VALUES(%s,%s,1,%s,'1.0.1','ESCALATED',1,%s,%s)",
                (rid,sid,'p-'+uuid.UUID(str(sid)).hex[:12],Jsonb(c),Jsonb({'qa':{'approved':False,'findings':['Отсутствует Dockerfile']}})))
        monkeypatch.setattr(workspaces,'get',lambda *a:{'files':{'app.py':'pass'}})
        monkeypatch.setattr(rework,'assess',lambda *a,**kw:{'escalation':None,'defects':[],'next_action':'Повторно проверить полный пакет; Dockerfile представлен платформой'})
        rework.product_failure(story,products.get(sid),[],{})
        saved=products.get(sid)
        assert saved['status']=='CHECKED' and saved['review_round']==1
        assert saved['verification']==c and not saved['review']['qa']['approved'] and not saved['review']['effective_approved']
    finally:
        with db.connection() as conn:
            conn.execute('DELETE FROM product_releases WHERE story_id=%s',(sid,));conn.execute('DELETE FROM events WHERE story_id=%s',(sid,));conn.execute('DELETE FROM jobs WHERE story_id=%s',(sid,));conn.execute('DELETE FROM stories WHERE id=%s',(sid,))
