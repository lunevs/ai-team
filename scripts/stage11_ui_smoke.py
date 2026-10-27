"""Streamlit rendering smoke with explicit UI-only fixture, no story mutations."""
import copy,json,os
import httpx
from streamlit.testing.v1 import AppTest
from app.product_assurance import make_contract
from scripts.product_verifier import evaluate
import base64


def main():
    original=httpx.Client
    root=os.getenv('API_URL','http://api:8000');sid=os.environ['STAGE11_UI_STORY_ID']
    with original(timeout=20,headers={'Authorization':'Bearer '+os.environ['APP_API_TOKEN']}) as client:
        r=client.get(root+'/stories/'+sid);r.raise_for_status();story=r.json()
        r=client.get(root+'/stories/'+sid+'/team');r.raise_for_status();team=r.json()
    definition={'package':{'sha256':'a'*64},'specification':{'output_type':'file'}}
    c={'id':'total','criterion':0,'description':'Независимый контроль суммы 30 RUB','basis':'Заранее вычислено: 10 + 20 = 30 RUB','source':'result','path':'','pointer':'/total','operator':'number','expected':30,'tolerance':'0'}
    plan={'checks':[c,c|{'id':'rows','source':'csv_file','path':'report.csv','pointer':'','operator':'min_count','expected':1}],'not_applicable_reason':''}
    contract=make_contract(definition,{'pm':{'acceptance_criteria':['UI fixture: итоговый отчёт 30 RUB']},'revision':1,'plan_digest':'c'*64},plan,plan)
    values={'result:':{'data':base64.b64encode(b'{"total":30}').decode()},'csv_file:report.csv':{'data':base64.b64encode(b'total\n30\n').decode()}}
    receipt=evaluate(contract,'deploy',values)
    product={'id':'00000000-0000-0000-0000-000000000011','story_id':sid,'product_key':'p-f11e00000003','version':'1.0.1','status':'ACCEPTANCE','accepted':False,'verification':contract,
             'ready_for_acceptance':True,'qa':{'evidence':receipt,'passed':True},'review':{},'git':{},'deployment':{'evidence':receipt},'outputs':[{'id':'00000000-0000-0000-0000-000000000012','path':'report.csv'}]}
    story.update(status='ACCEPTANCE',product=product,workspace=None,repairs=None,questions=[],error_type=None)
    class FixtureClient:
        def __init__(self,*a,**kw):pass
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def get(self,url,**kw):return self.request('GET',url,**kw)
        def request(self,method,url,**kw):
            if '/outputs/' in url:return httpx.Response(200,content=b'total\n30\n')
            if url.endswith('/team'):value=team
            elif url.endswith('/events'):value=[]
            elif url.endswith('/products'):value=[]
            elif url.endswith('/stories/'+sid):value=story
            else:value=[]
            return httpx.Response(200,json=value)
    httpx.Client=FixtureClient
    try:
        def render():
            a=AppTest.from_file('/workspace/ui/main.py');a.session_state['story_id']=sid;a.session_state['selected_story']=sid;a.run(timeout=30)
            assert not a.exception,a.exception
            return a
        a=render()
        assert any('UI fixture: итоговый отчёт 30 RUB' in e.value for e in a.markdown)
        assert len(a.get('download_button'))==1
        assert any('Принять самостоятельный продукт' in b.label for b in a.button)
        product['ready_for_acceptance']=False;product['deployment']['evidence']=evaluate(contract,'deploy',values|{'result:':{'data':base64.b64encode(b'{"total":31}').decode()}})
        a=render()
        assert not any('Принять самостоятельный продукт' in b.label for b in a.button)
        assert any('актуальные доказательства' in w.value for w in a.warning)
        return {'status':'passed','scope':'Streamlit AppTest with explicit UI fixture; source story read only','checks':['criteria_rendered','original_csv_download_button','acceptance_button_when_ready','stale_or_failed_evidence_hides_acceptance','customer_failure_explanation']}
    finally:httpx.Client=original

if __name__=='__main__':print('STAGE11_UI_RESULT='+json.dumps(main(),ensure_ascii=False))
