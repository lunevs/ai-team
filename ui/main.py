"""Streamlit обращается только к FastAPI; credentials не отправляются в браузер."""
import os
import uuid
import html
import httpx
import streamlit as st

st.set_page_config(page_title='AI Delivery Team',page_icon='🏦',layout='wide')
st.title('Bank AI Delivery Team')
st.caption('Синтетические данные · Планирование и аналитическая спецификация · Этапы 1–2')
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
if story['status']=='PLAN_REVIEW' and not story.get('analytics'):
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
if story['status']=='IN_PROGRESS' and not story.get('analytics'):st.success('План одобрен. Можно перейти к подготовке аналитической спецификации.')

st.subheader('Аналитическая спецификация · этап 2')
analytics=story.get('analytics')
if not analytics:
    st.caption('Синтетические источники → System Analyst → Financial Analyst → отдельное подтверждение. SQL агентом не выполняется.')
    if story['status']=='IN_PROGRESS' and story['approved']:
        if st.button('Подготовить аналитическую спецификацию',type='primary'):
            if request('/stories/'+id+'/analytics/start','POST',{}):st.rerun()
    else:
        st.caption('Доступно после одобрения плана.')
else:
    st.write('Статус спецификации: **'+analytics['status']+'**')
    if analytics['status'] in ['REQUESTED','RUNNING'] and story['status'] in ['BLOCKED','FAILED','CANCELLED']:
        st.warning('Подготовка спецификации приостановлена: '+story['status'])
    elif analytics['status'] in ['REQUESTED','RUNNING']:
        st.info('Аналитики готовят спецификацию. Обновите историю для просмотра результата; карточки команды обновляются автоматически.')
    if analytics.get('source_spec'):
        with st.expander('Source-to-target mapping и качество данных',expanded=True):
            st.write(analytics['source_spec']['grain'])
            st.dataframe(analytics['source_spec']['mapping'],column_order=['target','source','rule'],hide_index=True,use_container_width=True)
            for rule in analytics['source_spec']['data_quality_rules']:st.write('• '+rule)
    if analytics.get('financial_spec'):
        spec=analytics['financial_spec']
        with st.expander('Паспорт метрик и экономический смысл',expanded=True):
            st.json(spec['passport'])
            for label,key in [('Бизнес-правила','business_rules'),('Экономические проверки','economic_checks'),('Пользовательские истории','user_stories'),('Допущения','assumptions')]:
                st.markdown('**'+label+'**')
                for item in spec[key]:st.write('• '+item)
    if analytics.get('reference_rows'):
        with st.expander('Независимые ожидаемые значения'):
            st.caption('Python Decimal-эталон по синтетическим источникам. Это не результат SQL-витрины.')
            st.dataframe(analytics['reference_rows'],hide_index=True,use_container_width=True)
        st.caption('Checksum источников: '+analytics['dataset_checksum'])
    if analytics.get('documentation_url'):
        st.link_button('Спецификация в BookStack',analytics['documentation_url'])
    if analytics['status']=='REVIEW' and story['status']=='PLAN_REVIEW':
        st.info('Подтвердите паспорт и mapping этой ревизии. Изменение критериев требует новой ревизии через уточнения.')
        if st.button('Одобрить аналитическую спецификацию',type='primary'):
            if request('/stories/'+id+'/analytics/approve','POST',{'revision':story['revision'],'plan_digest':analytics['digest']}):st.rerun()
    if analytics['status']=='APPROVED':
        st.success('Спецификация одобрена. Исполнение SQL и QA реализации — этап 3.')
    elif analytics['status']=='REVIEW':
        with st.form('analytics-change'):
            changes=st.text_area('Уточнения, меняющие спецификацию или критерии')
            if st.form_submit_button('Вернуть на уточнение'):
                if changes.strip():
                    result=request('/stories/'+id+'/analytics/revise','POST',{'answers':changes})
                    if result:st.rerun()
                else:st.warning('Опишите требуемое изменение.')

@st.fragment(run_every='10s')
def team_panel(story_id):
    st.subheader('Команда')
    st.caption('По выбранной истории · обновление каждые 10 секунд')
    data=request('/stories/'+story_id+'/team')
    if data is None:
        return
    if not data['tracker_available']:
        st.warning('Трекер недоступен: счётчики задач неполные. Результаты агентов сохранены.')
    with st.container(horizontal=True, wrap=True, gap='small'):
        for agent in data['agents']:
            with st.container(border=True, width=250):
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

team_panel(id)
