"""Streamlit обращается только к FastAPI; credentials не отправляются в браузер."""
import os
import uuid
import html
from datetime import datetime, timezone
import httpx
import streamlit as st

st.set_page_config(page_title='AI Delivery Team',page_icon='🏦',layout='wide')
st.title('Bank AI Delivery Team')
st.caption('AI-команда · Самостоятельные продукты и банковская аналитика · Публичные и синтетические данные')
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

def select_product_story(story_id):
    st.session_state['selected_story']=story_id
    st.session_state['story_id']=story_id

with st.sidebar:
    st.header('Новая история')
    mode=st.selectbox('Тип результата',['product','credit_demo'],format_func=lambda x:'Самостоятельный продукт' if x=='product' else 'Кредитное демо')
    with st.form('new-story'):
        text=st.text_area('User story',height=180,placeholder='Создать витрину анализа кредитного портфеля по месяцам…')
        synthetic=st.checkbox('Используются только синтетические или публичные данные')
        submitted=st.form_submit_button('Создать историю',type='primary')
    if submitted:
        if not synthetic or len(text.strip())<20:
            st.warning('Опишите задачу и подтвердите синтетические данные.')
        else:
            if st.session_state.get('submitted_text')!=(mode,text):
                st.session_state['request_key']=str(uuid.uuid4())
                st.session_state['submitted_text']=(mode,text)
            result=request('/stories','POST',{'text':text,'synthetic_only':True,'delivery_mode':mode},st.session_state['request_key'])
            if result:
                st.session_state['story_id']=result['story_id']
                st.session_state['selected_story']=result['story_id']
                st.success('История сохранена')
    stories=request('/stories') or []
    if stories:
        ids=[s['id'] for s in stories]
        labels={s['id']:f"{s['status']} · {s['text'][:65]}" for s in stories}
        if st.session_state.get('selected_story') not in ids:
            st.session_state['selected_story']=st.session_state.get('story_id') if st.session_state.get('story_id') in ids else ids[0]
        selected=st.selectbox('Истории',ids,key='selected_story',format_func=labels.get)
        st.session_state['story_id']=selected
    with st.expander('Реестр продуктов'):
        registered=request('/products') or []
        if not registered:st.caption('Продуктов пока нет. Создайте user story в режиме самостоятельного продукта.')
        for product in registered:
            st.button(product['product_key']+' · '+product['version']+' · '+product['status'],
                key='registry-'+product['id'],on_click=select_product_story,args=(product['story_id'],))
    st.button('Обновить')

id=st.session_state.get('story_id')
if not id:
    st.info('Создайте первую историю. PM сформулирует цель, системный аналитик подготовит план и задачи.')
    st.stop()
@st.fragment(run_every='5s')
def interaction_panel(id):
    st.caption('Автообновление каждые 5 секунд · Проверено: '+datetime.now(timezone.utc).strftime('%H:%M:%S UTC'))
    story=request('/stories/'+id)
    if story is None:st.stop()
    grafana=os.getenv('GRAFANA_PUBLIC_URL','').rstrip('/')
    if grafana=='http://127.0.0.1:3001':
        st.link_button('Наблюдаемость истории · Grafana',grafana+'/d/ai-team-operations?var-story_id='+id)
    if 'mock' in story['usage'].get('backends',[]):
        st.warning('Тестовая история: использовался mock LLM. Это не результат реального запроса к модели.')
    elif story['usage'].get('backends'):
        st.caption('LLM backend: '+', '.join(story['usage']['backends']))
    a,b,c=st.columns(3)
    display_status=story['status'] if story['status'] in ['BLOCKED','FAILED','CANCELLED'] else ((story.get('product') or {}).get('status') or story['status'])
    status_names={'CHECK_PENDING':'Ждёт тестов контейнера','DEPLOY_READY':'Ждёт развёртывания','WAITING_GITHUB':'Ждёт публикации PR','PUBLISHED':'Ждёт ревью и слияния PR','PLAN_REVIEW':'Ждёт одобрения плана','BLOCKED':'Ждёт решения' if story.get('error_type') else 'Ждёт ваших ответов','FAILED':'Ошибка','NEW':'Готовится к работе','IN_PROGRESS':'В работе','ACCEPTANCE':'Ждёт приёмки','DONE':'Завершено','CANCELLED':'Отменено'}
    a.markdown('**Статус**')
    a.write(status_names.get(display_status,display_status))
    b.metric('LLM-вызовы',story['usage']['calls'])
    c.metric('Стоимость, оценка USD',f"{story['usage']['estimated_cost_usd']:.6f}")
    st.caption(f"Ревизия {story['revision']} · Story ID {id} · Trace ID {story['trace_id']}")
    st.write(story['text'])
    reason=story.get('waiting_reason')
    if reason:st.warning(reason)
    elif story['error_type']:st.warning('Требуется внимание: '+story['error_type'])
    limits=[str(story.get(key)) if story.get(key) is not None else 'без ограничения' for key in ['max_calls','max_tokens','max_cost_usd']]
    st.caption(f"Лимиты: вызовы — {limits[0]}; токены — {limits[1]}; USD — {limits[2]}. Использовано токенов: {story['usage']['tokens']}.")
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
    if story['status'] in ['BLOCKED','FAILED'] and story.get('error_type')!='BudgetExceeded':
        with st.form('resume'):
            answers=st.text_area('Ответы и уточнения (создадут новую ревизию плана)',key=f'answers-{id}-{story["revision"]}')
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
    if story.get('delivery_mode')=='product':
        st.subheader('Самостоятельный продукт')
        product=story.get('product')
        if not product:
            st.info('После одобрения плана команда подготовит исходники, тесты и самостоятельный пакет поставки.')
        else:
            st.write('**'+product['product_key']+' · '+product['version']+'** — '+product['status'])
            if product.get('documentation_url'):
                st.link_button('Архитектура и эксплуатация · BookStack',product['documentation_url'])
            if product.get('package'):
                st.caption('SHA256 пакета: '+product['package']['sha256'])
                if st.button('Подготовить пакет к скачиванию'):
                    try:
                        with httpx.Client(timeout=30) as client:
                            response=client.get(API+'/products/'+product['id']+'/package',headers={'Authorization':'Bearer '+TOKEN})
                        if response.status_code==200:
                            st.download_button('Скачать исходники, Docker и install.md',response.content,
                                file_name=product['product_key']+'-'+product['version']+'.zip',mime='application/zip')
                        else:st.error('Пакет временно недоступен')
                    except httpx.TransportError:st.error('API временно недоступен')
            if product['status']=='CHECK_PENDING':
                st.info('Пакет ждёт host-оператора: сборка и изолированные тесты. Оператор запускается отдельно от контейнеров команды.')
            if product.get('qa'):
                with st.expander('Фактическая проверка контейнера'):st.json(product['qa'])
            if product.get('review'):
                with st.expander('Независимые заключения QA и архитектора'):st.json(product['review'])
            for pr in (product.get('git') or {}).get('pulls',[]):
                st.link_button('PR задачи #'+str(pr['task_id']),pr['pull_request_url'])
            release=(product.get('git') or {}).get('release')
            if release:st.link_button('Релиз в main',release['pull_request_url'])
            deployment=product.get('deployment') or {}
            if deployment.get('url'):
                st.link_button('Открыть развёрнутый продукт',deployment['url']+'/result')
            if deployment.get('result'):
                st.write('Результат продукта');st.json(deployment['result'])
            if product['status']=='ACCEPTANCE' and not product['accepted']:
                st.caption('Приёмка подтверждает бизнес-результат. Релиз в main требует отдельного review/CI/слияния.')
                if st.button('Принять самостоятельный продукт',type='primary'):
                    if request('/stories/'+id+'/product/accept','POST',{'revision':story['revision'],'plan_digest':story['plan_digest']}):st.rerun()
            if product['status'] in ['ESCALATED','DEPLOY_FAILED']:
                st.warning('Нужен разбор замечаний. Тесты или развёртывание не подтверждены; готовность не заявлена.')
            if product['status']=='DEPLOY_FAILED':
                if st.button('Повторить запуск после исправления настроек'):
                    if request('/stories/'+id+'/product/retry-deployment','POST',{'revision':story['revision'],'plan_digest':story['plan_digest']}):st.rerun()
            if product['status'] in ['WAITING_GITHUB','PUBLISHED','RELEASE_REVIEW'] or product['accepted'] and product['status']=='ACCEPTANCE':
                st.caption('Ожидаются GitHub-доступ либо независимые review, CI и слияние PR. Автослияния нет.')
                if st.button('Проверить продвижение продукта'):
                    if request('/stories/'+id+'/product/refresh','POST',{}):st.rerun()
    else:
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

        st.subheader('Расчёт и результат · этап 3')
        execution=story.get('execution')
        if not execution:
            if analytics and analytics['status']=='APPROVED' and story['status']=='IN_PROGRESS':
                if st.button('Запустить реализацию и QA',type='primary'):
                    if request('/stories/'+id+'/execution/start','POST',{}):st.rerun()
            else:st.caption('Доступно после подтверждения аналитической спецификации.')
        else:
            st.write('Статус исполнения: **'+execution['status']+'** · попытка '+str(execution['attempt'])+' из 3')
            if execution.get('qa'):
                with st.expander('Проверки качества',expanded=not execution['qa']['passed']):
                    st.write('QA: '+('пройден' if execution['qa']['passed'] else 'есть замечания'))
                    st.json(execution['qa'])
            if execution.get('review'):
                with st.expander('Ревью системного аналитика / архитектора'):
                    st.json(execution['review'])
                    st.caption('Ревью модели дополняет независимое GitHub review.')
            if execution.get('rows') and (execution.get('qa') or {}).get('passed'):
                import pandas as pd
                from decimal import Decimal,ROUND_HALF_UP
                rows=execution['rows']
                products=st.multiselect('Продукты',sorted({str(r['product_id']) for r in rows}),default=sorted({str(r['product_id']) for r in rows}))
                regions=st.multiselect('Регионы',sorted({r['region'] for r in rows}),default=sorted({r['region'] for r in rows}))
                filtered=[r for r in rows if str(r['product_id']) in products and r['region'] in regions]
                monthly=[]
                for month in sorted({r['month'] for r in filtered}):
                    parts=[r for r in filtered if r['month']==month];item={'month':month}
                    for metric in ['balance','dpd30_balance','dpd90_balance','interest_income']:
                        values=[Decimal(str(r[metric])) for r in parts if r[metric] is not None]
                        item[metric]=sum(values) if values else None
                    b,n=item['balance'],item['dpd90_balance']
                    item['npl90_ratio']=(n/b).quantize(Decimal('.000001'),rounding=ROUND_HALF_UP) if b is not None and n is not None and b else None
                    monthly.append(item)
                if monthly:
                    month=st.select_slider('Месяц показателей',[r['month'] for r in monthly],value=monthly[-1]['month'])
                    current=next(r for r in monthly if r['month']==month)
                    cols=st.columns(3)
                    cols[0].metric('Остаток, RUB','—' if current['balance'] is None else f"{current['balance']:,.2f}")
                    cols[1].metric('NPL90','—' if current['npl90_ratio'] is None else f"{current['npl90_ratio']:.2%}")
                    cols[2].metric('Доход за месяц, RUB','—' if current['interest_income'] is None else f"{current['interest_income']:,.2f}")
                    frame=pd.DataFrame([{k:float(v) if isinstance(v,Decimal) else v for k,v in r.items()} for r in monthly]).set_index('month')
                    st.line_chart(frame[['balance','dpd90_balance']],color=['#376CA6','#183153'])
                    st.bar_chart(frame[['interest_income']],color='#186A3B')
                st.caption('Остатки — на последний календарный день месяца. Доход — сумма за месяц. NPL рассчитан из суммарных остатков. Пропуски сохраняются.')
                with st.expander('Детализация витрины'):
                    st.dataframe(filtered,hide_index=True,use_container_width=True)
                with st.expander('Качество источников: покрытие month-end'):
                    st.caption('Независимый контроль по источникам: кредиты с данными месяца без последнего календарного снимка. Это отдельный DQ-индикатор, не денежная метрика SQL-витрины.')
                    coverage=[{k:r[k] for k in ['month','product_id','region','source_row_count','snapshot_loan_count','missing_snapshot_loans']} for r in (analytics or {}).get('reference_rows',[]) if str(r['product_id']) in products and r['region'] in regions]
                    st.dataframe(coverage,hide_index=True,use_container_width=True)
            for name in ['report.xlsx','query.sql','qa.json']:
                if name in (execution.get('artifacts') or {}):
                    try:
                        response=httpx.get(API+execution['artifacts'][name]['url'],headers={'Authorization':'Bearer '+TOKEN},timeout=15)
                        if response.status_code==200:st.download_button('Скачать '+name,response.content,file_name=name,key='download-'+name)
                    except httpx.TransportError:st.caption('Файл временно недоступен: '+name)
            git=execution.get('git') or {}
            if git.get('documentation_url'):st.link_button('Результат в BookStack',git['documentation_url'])
            for pull in git.get('pulls',[]):st.link_button('PR задачи #'+str(pull['task_id']),pull['pull_request_url'])
            for release in git.get('releases',[]):st.link_button('Релиз эпика #'+str(release['epic_id']),release['pull_request_url'])
            if execution['status']=='RELEASE_REVIEW':
                st.info('Результат принят. Релизные PR ожидают независимого ревью, CI и слияния в main.')
                if st.button('Проверить выпуск релиза'):
                    if request('/stories/'+id+'/execution/finalize-release','POST',{}):st.rerun()
            for tag in git.get('tags',[]):st.caption('Выпущен: '+tag['tag'])
            if execution['status']=='WAITING_GITHUB':
                st.info('Расчёт и отчёт готовы. Для публикации PR нужны сервисный GitHub-токен в локальном .env, перезапуск GitHub broker и слияние PR реализации этапа 3 в базовую release-ветку.')
            if execution['status'] in ['WAITING_GITHUB','READY_TO_PUBLISH','PUBLISHED'] or execution['status'] in ['BUSINESS_ACCEPTED','FINALIZING_RELEASE'] and story['status']=='FAILED':
                if st.button('Повторить публикацию PR'):
                    if request('/stories/'+id+'/execution/retry','POST',{}):st.rerun()
            if execution['status']=='ACCEPTANCE':
                st.info('Результат ожидает приёмки. Сначала завершите независимое GitHub review, CI и слияние task PR.')
                if st.button('Проверить готовность PR'):
                    gate=request('/stories/'+id+'/execution/gate','POST',{})
                    if gate:st.json(gate)
                if st.button('Принять результат',type='primary'):
                    if request('/stories/'+id+'/execution/accept','POST',{'revision':story['revision'],'plan_digest':execution['analytics_digest']}):st.rerun()
            if execution['status']=='ESCALATED':st.warning('Три попытки исчерпаны. Требуется решение системного аналитика и уточнение задачи.')


interaction_panel(id)

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
                if agent.get('next_task') and agent['next_task'] not in title:
                    st.caption('Задача: '+agent['next_task'])
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
