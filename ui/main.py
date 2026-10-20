"""Streamlit обращается только к FastAPI; credentials не отправляются в браузер."""
import os
import uuid
import html
import httpx
import streamlit as st

st.set_page_config(page_title='AI Delivery Team',page_icon='🏦',layout='wide')
st.title('Bank AI Delivery Team')
st.caption('Синтетические данные · Планирование и одобрение · Этап 1')
API=os.getenv('API_URL','http://api:8000')
TOKEN=os.getenv('APP_API_TOKEN','')

def request(path,method='GET',data=None,key=None):
    headers={'Authorization':'Bearer '+TOKEN}
    if key:headers['Idempotency-Key']=key
    try:
        with httpx.Client(timeout=75) as client:
            response=client.request(method,API+path,headers=headers,json=data)
        if response.status_code>=400:
            detail=response.json().get('detail','Ошибка запроса')
            st.error(str(detail))
            return None
        return response.json()
    except (httpx.TransportError,ValueError):
        st.error('API временно недоступен. Повторите запрос: ключ сохранён.')
        return None

with st.sidebar:
    st.header('Новая история')
    with st.form('new-story'):
        text=st.text_area('User story',height=180,placeholder='Создать витрину анализа кредитного портфеля по месяцам…')
        synthetic=st.checkbox('Используются только синтетические или публичные данные')
        submitted=st.form_submit_button('Создать историю',type='primary')
    if submitted:
        if not synthetic or len(text.strip())<20:
            st.warning('Опишите задачу и подтвердите синтетические данные.')
        else:
            if st.session_state.get('submitted_text')!=text:
                st.session_state['request_key']=str(uuid.uuid4())
                st.session_state['submitted_text']=text
            result=request('/stories','POST',{'text':text,'synthetic_only':True},st.session_state['request_key'])
            if result:
                st.session_state['story_id']=result['story_id']
                st.success('История сохранена')
    stories=request('/stories') or []
    if stories:
        ids=[s['id'] for s in stories]
        labels={s['id']:f"{s['status']} · {s['text'][:65]}" for s in stories}
        selected=st.selectbox('Истории',ids,index=ids.index(st.session_state['story_id']) if st.session_state.get('story_id') in ids else 0,format_func=labels.get)
        st.session_state['story_id']=selected
    st.button('Обновить')

id=st.session_state.get('story_id')
if not id:
    st.info('Создайте первую историю. PM сформулирует цель, системный аналитик подготовит план и задачи.')
    st.stop()
story=request('/stories/'+id)
if story is None:st.stop()
if 'mock' in story['usage'].get('backends',[]):
    st.warning('Тестовая история: использовался mock LLM. Это не результат реального запроса к модели.')
elif story['usage'].get('backends'):
    st.caption('LLM backend: '+', '.join(story['usage']['backends']))
a,b,c=st.columns(3)
a.metric('Статус',story['status'])
b.metric('LLM-вызовы',story['usage']['calls'])
c.metric('Стоимость, оценка USD',f"{story['usage']['estimated_cost_usd']:.6f}")
st.caption(f"Ревизия {story['revision']} · Story ID {id} · Trace ID {story['trace_id']}")
st.write(story['text'])
if story['error_type']:st.warning('Требуется внимание: '+story['error_type'])
if story['questions']:
    st.subheader('Вопросы для уточнения')
    for question in story['questions']:st.write('• '+question)
if story['pm']:
    st.subheader('Цель и критерии приёмки')
    st.write(story['pm']['goal'])
    for criterion in story['pm']['acceptance_criteria']:st.write('• '+criterion)
if story['plan']:
    st.subheader('Архитектура и план')
    st.write(story['plan']['architecture'])
    for epic in story['plan']['epics']:
        with st.expander(epic['title'],expanded=True):
            for task in epic['tasks']:
                st.write(f"**{task['title']}** · {task['role']}")
                st.write('Критерии: '+'; '.join(task['acceptance_criteria']))
                if task['depends_on']:st.caption('Зависит от: '+', '.join(task['depends_on']))
if story['status']=='PLAN_REVIEW':
    st.info('Одобрение относится к показанной ревизии. На этапе 1 SQL ещё не выполняется.')
    if st.button('Одобрить план',type='primary'):
        if request('/stories/'+id+'/approve-plan','POST',{'revision':story['revision'],'plan_digest':story['plan_digest']}):st.rerun()
if story['status'] in ['BLOCKED','FAILED']:
    with st.form('resume'):
        answers=st.text_area('Ответы и уточнения (создадут новую ревизию плана)')
        if st.form_submit_button('Продолжить'):
            if request('/stories/'+id+'/resume','POST',{'answers':answers}):st.rerun()
if story['status']!='CANCELLED':
    if st.button('Отменить историю'):
        if request('/stories/'+id+'/cancel','POST',{}):st.rerun()
with st.expander('Задачи и документация'):
    for link in story['links']:st.link_button(link['key'],link['url'])
with st.expander('События'):
    for event in request('/stories/'+id+'/events') or []:
        st.write(f"{event['created_at']} · {event['type']}")
        st.json(event['payload'])
if story['status']=='IN_PROGRESS':st.success('План одобрен, событие сохранено. Продолжение исполнения — на следующем этапе.')
