"""Read-only team projection: validated agent results and tracker leaf tasks."""
from app.schemas import Role
from app.status import waiting_reason

ROLE_NAMES = {
    'pm': 'Продуктовый менеджер',
    'system_analyst': 'Системный аналитик / архитектор',
    'financial_analyst': 'Финансовый аналитик',
    'data_developer': 'Разработчик данных',
    'application_developer': 'Разработчик приложений',
    'designer': 'Дизайнер',
    'qa': 'QA-инженер',
}
PLANNING = {'pm': 'Определяет цель', 'system_analyst': 'Готовит архитектуру и план'}


def project_team(story, results, completions, calls, links, packages, tracker_available, worker_active):
    """Counts are per selected story; older revisions remain in history only."""
    cards = {role.value: {'role': role.value, 'name': ROLE_NAMES[role.value],
             'current': 'Нет активной задачи', 'completed': 0, 'remaining': 0,
             'history': [], 'tasks': []} for role in Role}
    stopped = story['status'] in ('CANCELLED', 'BLOCKED', 'FAILED')
    for role, title in PLANNING.items():
        card = cards[role]
        for result in results:
            if result['role'] != role:
                continue
            revision = result['revision']
            card['history'].append({'title': title, 'revision': revision,
                'completed_at': completions.get((role, revision)), 'url': None,
                'source': 'agent'})
            if revision == story['revision']:
                card['completed'] += 1
        if not card['completed'] and not stopped:
            card['remaining'] += 1
    for result in results:
        if result['role'] in ['system_analyst_spec','financial_analyst_spec']:
            role=result['role'].replace('_spec','')
            cards[role]['history'].append({'title':'Аналитическая спецификация','revision':result['revision'],
                'completed_at':completions.get((result['role'],result['revision'])),'url':None,'source':'agent'})
            if result['revision']==story['revision']:cards[role]['completed']+=1
        elif result['role'].startswith(('data_developer_','system_analyst_review_')):
            role='data_developer' if result['role'].startswith('data_developer_') else 'system_analyst'
            cards[role]['history'].append({'title':'Подготовлен SQL' if role=='data_developer' else 'Ревью SQL',
                'revision':result['revision'],'completed_at':completions.get((result['role'],result['revision'])),
                'url':None,'source':'agent'})
    product_results={
        'product_financial_analyst':('financial_analyst','Подготовил экономические критерии'),
        'product_designer':('designer','Подготовил стиль продукта'),
        'product_data_developer':('data_developer','Спроектировал данные продукта'),
        'product_solution':('application_developer','Подготовил исходники и тесты'),
        'product_qa':('qa','Проверил исходники и результаты тестов'),
        'product_review':('system_analyst','Проверил архитектуру продукта')}
    for result in results:
        if result['role'] in product_results:
            role,title=product_results[result['role']]
            cards[role]['history'].append({'title':title,'revision':result['revision'],
                'completed_at':completions.get((result['role'],result['revision'])),'url':None,'source':'agent'})
    for call in calls:
        if call['role']=='data_developer' and call['state']=='reserved' and call['revision']==story['revision'] and worker_active and not stopped:
            cards['data_developer']['current']='Разрабатывает SQL-витрину'
        if (call['revision'] == story['revision'] and call['state'] == 'reserved'
                and worker_active and not stopped and call['role'] in PLANNING
                and not any(r['role']==call['role'] and r['revision']==story['revision'] for r in results)):
            cards[call['role']]['current'] = PLANNING[call['role']]
    for call in calls:
        if call['role']=='financial_analyst' and call['state']=='reserved' and call['revision']==story['revision'] and worker_active and not stopped:
            cards['financial_analyst']['current']='Проверяет паспорт метрик'
    for call in calls:
        if call['role']=='system_analyst' and call['state']=='reserved' and call['revision']==story['revision'] and worker_active and not stopped:
            cards['system_analyst']['current']='Готовит спецификацию источников' if any(r['role']=='system_analyst' and r['revision']==story['revision'] for r in results) else PLANNING['system_analyst']
    prefix = f"r{story['revision']}:task:"
    by_key = {link['key']: link for link in links}
    planned = [task for epic in (story.get('plan') or {}).get('epics', []) for task in epic['tasks']]
    task_titles = {task['key']: task['title'] for task in planned}
    closed_keys = set()
    for task in planned:
        link = by_key.get(prefix + task['key'])
        remote = packages.get(link['external_id']) if link else None
        if (remote or {}).get('_embedded', {}).get('status', {}).get('isClosed', False):
            closed_keys.add(task['key'])
    for epic in (story.get('plan') or {}).get('epics', []):
        for task in epic['tasks']:
            card = cards[task['role']]
            link = by_key.get(prefix + task['key'])
            remote = packages.get(link['external_id']) if link else None
            status = (remote or {}).get('_embedded', {}).get('status', {})
            closed = status.get('isClosed', False)
            item = {'title': task['title'], 'url': link['url'] if link else None,
                    'status': status.get('name', 'Статус недоступен' if link else 'Запланировано'),
                    'key': task['key'], 'closed': closed, 'depends_on': task.get('depends_on', [])}
            card['tasks'].append(item)
            if closed:
                card['completed'] += 1
                card['history'].append(item | {'revision': story['revision'],
                    'completed_at': remote.get('updatedAt'), 'source': 'tracker'})
            elif remote is not None or link is None:
                card['remaining'] += 1
                if status.get('isInProgress') and not stopped:
                    card['current'] = 'Выполняет: ' + task['title']
    feasibility=story.get('feasibility') or {}
    gap_names={gap['capability']:gap['title'] for gap in feasibility.get('gaps',[])}
    for task in feasibility.get('tasks',[]):
        card=cards['system_analyst'];remote=packages.get(task['task_id'])
        status=(remote or {}).get('_embedded',{}).get('status',{})
        item={'title':gap_names.get(task['capability'],'Проверить технические предпосылки плана'),
              'url':task['url'],'status':status.get('name','Статус недоступен'),
              'key':'capability:'+task['capability'],'closed':status.get('isClosed',False),'depends_on':[]}
        card['tasks'].append(item)
        if item['closed']:
            card['completed']+=1
            card['history'].append(item|{'revision':story['revision'],'completed_at':remote.get('updatedAt'),'source':'tracker'})
        elif remote is not None:card['remaining']+=1
    if story.get('delivery_mode')=='product' and worker_active and not stopped:
        activity={'financial_analyst':'Определяет экономические критерии','designer':'Готовит стиль продукта',
            'data_developer':'Проектирует данные продукта','application_developer':'Пишет приложение и тесты',
            'qa':'Проверяет продукт и тесты','system_analyst':'Проверяет архитектуру продукта'}
        for call in calls:
            if call['revision']==story['revision'] and call['state']=='reserved' and call['role'] in activity:
                cards[call['role']]['current']=activity[call['role']]
    product_status=(story.get('product') or {}).get('status')
    execution=story.get('execution') or {}
    active_role={'QA':'qa','ARCHITECT_REVIEW':'system_analyst','DEVELOPING':'data_developer','READY_TO_PUBLISH':'application_developer'}.get(execution.get('status'))
    if active_role and worker_active and not stopped:cards[active_role]['current']={'qa':'Проверяет расчёт витрины','system_analyst':'Проверяет SQL и архитектуру','data_developer':'Разрабатывает SQL-витрину','application_developer':'Готовит отчёт и публикацию'}[active_role]
    for card in cards.values():
        pending=next((task for task in card['tasks'] if not task['closed']),None)
        card['next_task']=pending['title'] if pending else None
        if stopped:
            card['current'] = 'История отменена' if story['status']=='CANCELLED' else waiting_reason(story)
        elif execution.get('status') in ['ACCEPTANCE','BUSINESS_ACCEPTED','RELEASE_REVIEW','FINALIZING_RELEASE'] and card['remaining']:
            card['current']='Ожидает ревью и приёмки'
        elif execution.get('status')=='WAITING_GITHUB' and card['remaining']:
            card['current']='Ожидает публикации PR'
        elif card['current'] == 'Нет активной задачи' and card['remaining']:
            card['current'] = ('Ожидает одобрения спецификации' if story.get('approved') else 'Ожидает одобрения плана') if story['status'] == 'PLAN_REVIEW' else queued_activity(card, closed_keys, task_titles, worker_active)
    if story.get('delivery_mode')=='product' and product_status and (not stopped or (story['status']=='BLOCKED' and product_status=='ESCALATED' and not story.get('questions') and not story.get('error_type'))):
        current_results={result['role'] for result in results if result['revision']==story['revision']}
        active_roles={call['role'] for call in calls if call['revision']==story['revision'] and call['state']=='reserved'} if worker_active else set()
        for role,card in cards.items():
            card['current']=product_activity(role,product_status,current_results,role in active_roles,card['current'])
    if story['status']=='BLOCKED' and feasibility.get('status') in ['NEEDS_PLATFORM','NEEDS_ANALYSIS']:
        for role,card in cards.items():
            if role=='system_analyst':
                pending=next((task for task in card['tasks'] if not task['closed']),None)
                card['current']='Нужно подготовить: '+pending['title'] if pending else 'Нужно проверить технические предпосылки плана'
            elif role=='pm':card['current']='Ждёт подготовки среды для планирования'
            else:card['current']='Ждёт выполнимого плана'
    workspace=story.get('workspace') or {}
    if workspace and not stopped:
        rows=workspace['tasks'];by_key={row['task_key']:row for row in rows}
        titles={task['key']:task['title'] for task in planned}
        epics=(story.get('plan') or {}).get('epics',[])
        current_keys={task['key'] for task in epics[workspace['epic_index']]['tasks']} if workspace['epic_index']<len(epics) else set()
        for role,card in cards.items():
            own=[row for row in rows if row['role']==role]
            current=next((row for row in own if row['state'] not in ['PENDING','MERGED']),None)
            if current:
                title=titles.get(current['task_key'],current['task_key'])
                label={'RUNNING':'Работает над задачей' if worker_active else 'Готовится продолжить',
                    'CHECK_PENDING':'Ждёт проверки изменений','CHECKED':'Готовит изменения к согласованию',
                    'WAITING_GITHUB':'Ждёт готовности публикации','REVIEW':'Ждёт согласования изменений','FAILED':'Требуется исправление'}
                card['current']=label.get(current['state'],'Ожидает продолжения')+': '+title
            else:
                pending=next((row for row in own if row['state']=='PENDING'),None)
                if pending:
                    title=titles.get(pending['task_key'],pending['task_key'])
                    spec=next((t for t in planned if t['key']==pending['task_key']),{})
                    waiting=next((d for d in spec.get('depends_on',[]) if d in by_key and by_key[d]['state']!='MERGED'),None)
                    if waiting:card['current']='Ждёт завершения задачи «'+titles.get(waiting,waiting)+'», затем: '+title
                    else:card['current']=('Готовится к задаче: ' if pending['task_key'] in current_keys else 'Ждёт завершения предыдущего этапа: ')+title
                elif own:
                    title=titles.get(own[-1]['task_key'],own[-1]['task_key'])
                    card['current']=('Выполнена задача: ' if own[-1]['task_key'] in closed_keys else 'Задача согласована; ждёт выпуска: ')+title
    return {'agents': list(cards.values()), 'tracker_available': tracker_available,
            'scope': 'story', 'revision': story['revision']}


def queued_activity(card, closed_keys, task_titles, worker_active):
    pending = next((task for task in card['tasks'] if not task['closed']), None)
    if not pending:
        if card['role']=='system_analyst':
            return 'Ждёт формулировки цели продуктовым менеджером'
        return 'Готовится сформулировать цель' if worker_active else 'Ждёт возобновления работы команды'
    title = pending['title']
    dependencies = [task_titles.get(key, key) for key in pending['depends_on'] if key not in closed_keys]
    if dependencies:
        return 'Ждёт завершения: ' + '; '.join(dependencies) + '. Затем: ' + title
    if not worker_active:
        return 'Ждёт возобновления работы команды. Затем: ' + title
    return 'Готовится приступить к задаче: ' + title


def product_activity(role, status, results, active, fallback):
    """Describe each person's evidence-backed progress, not the shared machine stage."""
    activity={'pm':'Уточняет цель и требования','financial_analyst':'Описывает показатели и правила расчёта',
        'designer':'Продумывает оформление результата','data_developer':'Продумывает хранение и подготовку данных',
        'application_developer':'Создаёт приложение','qa':'Проверяет приложение',
        'system_analyst':'Проверяет устройство и надёжность решения'}
    if active:
        return activity.get(role,fallback)
    prepared={'pm':('pm','Цель и требования подготовлены'),
        'financial_analyst':('product_financial_analyst','Описание показателей подготовлено'),
        'designer':('product_designer','Оформление результата подготовлено'),
        'data_developer':('product_data_developer','Схема хранения данных подготовлена'),
        'application_developer':('product_solution','Приложение подготовлено'),
        'qa':('product_qa','Проверка приложения завершена'),
        'system_analyst':('product_review','Проверка устройства решения завершена')}
    key,summary=prepared[role]
    if status=='ESCALATED':
        if role=='application_developer':return 'Приложение требует доработки после проверки'
        if role=='qa':return 'Проверка завершена; ждёт исправлений' if key in results else 'Ждёт исправлений для повторной проверки'
        if role=='system_analyst':return 'Проверка решения завершена; ждёт доработок' if key in results else 'Ждёт разбора замечаний к решению'
    if key in results:
        if role=='application_developer' and status=='CHECK_PENDING':
            return summary+'; ждёт проверки'
        return summary
    if status=='CHECK_PENDING':
        if role=='qa':return 'Ждёт запуска проверки приложения'
        if role=='system_analyst':return 'План подготовлен; ждёт результатов проверки' if 'system_analyst' in results else 'Ждёт результатов проверки приложения'
    if status in {'WAITING_GITHUB','PUBLISHED'}:
        return 'Ждёт согласования изменений перед запуском'
    if status=='DEPLOY_READY':return 'Ждёт запуска готового приложения'
    if status=='ACCEPTANCE':return 'Ждёт вашей оценки результата'
    if status in {'RELEASE_REVIEW','BUSINESS_ACCEPTED','FINALIZING_RELEASE'}:return 'Ждёт окончательного согласования выпуска'
    return fallback
