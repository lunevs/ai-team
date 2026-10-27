"""UI-only fixture: never approves or changes the source story."""
import json,os,sys
import httpx
from streamlit.testing.v1 import AppTest

def main():
    original=httpx.Client;sid=os.environ['PLAN_FIRST_UI_STORY_ID']
    with original(timeout=20,headers={'Authorization':'Bearer '+os.environ['APP_API_TOKEN']}) as client:
        response=client.get(os.getenv('API_URL','http://api:8000')+'/stories/'+sid);response.raise_for_status();story=response.json()
    story.update(approved=False,status='PLAN_REVIEW',questions=[],plan_digest='c'*64,pm={'goal':'Явная техническая fixture согласования','acceptance_criteria':['Тестовый результат']},plan={'architecture':'Техническая fixture, не банковская реализация','epics':[{'title':'Подготовка результата','tasks':[{'key':'data','title':'Подготовить данные','role':'data_developer','acceptance_criteria':['Проверить данные'],'depends_on':[]},{'key':'chart','title':'Показать результат в BI','role':'application_developer','acceptance_criteria':['Проверить график'],'depends_on':['data']}]}]},feasibility={'status':'READY','gaps':[],'analysis_issues':[],'criteria':[]},workspace=None,product=None,repairs=None)
    calls=[]
    class Client:
        def __init__(self,*a,**kw):pass
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def request(self,method,url,**kw):
            calls.append(url)
            if url.endswith('/team'):value={'tracker_available':True,'agents':[{'name':'Разработчик приложений','current':'Готовится выполнить задачу','completed':0,'remaining':1,'history':[],'tasks':[]}]}
            elif url.endswith('/stories/'+sid):value=story
            else:value=[]
            return httpx.Response(200,json=value)
    httpx.Client=Client
    original_path=list(sys.path)
    sys.path[:]=[p for p in sys.path if p not in {'','/workspace'}]
    for name in ('ui.plan_view','ui'):sys.modules.pop(name,None)
    try:
        def render():
            a=AppTest.from_file('/workspace/ui/main.py');a.session_state['story_id']=sid;a.run(timeout=30)
            assert not a.exception,a.exception
            return a
        a=render();assert not a.metric and not any(url.endswith('/team') for url in calls)
        assert any('План от продуктового менеджера' in e.value for e in a.subheader)
        assert not any(e.value=='Команда' for e in a.subheader)
        assert any('Ок, начать' in b.label for b in a.button)
        story['status']='BLOCKED';story['feasibility'].update(status='NEEDS_PLATFORM',gaps=[{'capability':'external_bi','title':'Отдельная BI-система','reason':'Ещё не поддержана'}],criteria=[{'task_keys':['chart'],'capability_ids':['external_bi']}])
        a=render();assert a.error and not any('Ок, начать' in b.label for b in a.button)
        assert any('Сейчас команда не может' in e.value for e in a.caption)
        story.update(approved=True,status='IN_PROGRESS');story['feasibility'].update(status='READY',gaps=[],criteria=[])
        a=render();assert any(e.value=='Команда' for e in a.subheader) and any(url.endswith('/team') for url in calls)
        return {'status':'passed','scope':'Explicit Streamlit fixture; no approval or source story mutation','checks':['plan_and_roles_before_approval','no_technical_metrics','no_agent_cards_before_approval','unsupported_work_explicit','agent_cards_after_approval']}
    finally:
        httpx.Client=original
        sys.path[:]=original_path
if __name__=='__main__':print('PLAN_FIRST_UI_RESULT='+json.dumps(main(),ensure_ascii=False))
