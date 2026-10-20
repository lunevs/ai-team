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
plan_column, team_column = st.columns([3, 2], gap='large')
with plan_column:
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

@st.fragment(run_every='10s')
def team_panel(story_id):
    st.subheader('Команда')
    st.caption('По выбранной истории · обновление каждые 10 секунд')
    data=request('/stories/'+story_id+'/team')
    if data is None:
        return
    if not data['tracker_available']:
        st.warning('Трекер недоступен: счётчики задач неполные. Результаты агентов сохранены.')
    for agent in data['agents']:
        with st.container(border=True):
            st.markdown('**'+agent['name']+'**')
            title=agent['current']
            st.write(title if len(title)<=65 else title[:62]+'…')
            if len(title)>65:
                st.caption(title)
            st.caption(f"Выполнено: {agent['completed']} · Осталось: {agent['remaining']}")
            with st.popover('История задач',use_container_width=True):
                st.markdown('**'+agent['name']+'**')
                if not agent['history']:
                    st.caption('Выполненных задач пока нет')
                for item in agent['history']:
                    st.write(item['title'])
                    st.caption(f"Ревизия {item['revision']} · {item.get('completed_at') or 'Время не зафиксировано'}")
                    if item.get('url'):
                        st.link_button('Открыть задачу',item['url'])
                if agent['tasks']:
                    st.divider()
                    st.markdown('**Назначенные задачи**')
                    for task in agent['tasks']:
                        st.write(task['title'])
                        st.caption(task['status'])
                        if task['url']:
                            st.link_button('Открыть в трекере',task['url'])
    st.caption('Счётчики — текущая ревизия. История результатов планирования включает прошлые ревизии. Закрытые задачи подтверждаются OpenProject.')

with team_column:
    team_panel(id)
