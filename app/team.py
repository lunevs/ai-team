"""Read-only team projection: validated agent results and tracker leaf tasks."""
from app.schemas import Role

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
    for call in calls:
        if (call['revision'] == story['revision'] and call['state'] == 'reserved'
                and worker_active and not stopped and call['role'] in PLANNING
                and not cards[call['role']]['completed']):
            cards[call['role']]['current'] = PLANNING[call['role']]
    prefix = f"r{story['revision']}:task:"
    by_key = {link['key']: link for link in links}
    for epic in (story.get('plan') or {}).get('epics', []):
        for task in epic['tasks']:
            card = cards[task['role']]
            link = by_key.get(prefix + task['key'])
            remote = packages.get(link['external_id']) if link else None
            status = (remote or {}).get('_embedded', {}).get('status', {})
            closed = status.get('isClosed', False)
            item = {'title': task['title'], 'url': link['url'] if link else None,
                    'status': status.get('name', 'Статус недоступен' if link else 'Запланировано')}
            card['tasks'].append(item)
            if closed:
                card['completed'] += 1
                card['history'].append(item | {'revision': story['revision'],
                    'completed_at': remote.get('updatedAt'), 'source': 'tracker'})
            elif remote is not None or link is None:
                card['remaining'] += 1
                if status.get('isInProgress') and not stopped:
                    card['current'] = task['title']
    for card in cards.values():
        if stopped:
            card['current'] = {'BLOCKED': 'Работа заблокирована', 'FAILED': 'Требуется внимание',
                               'CANCELLED': 'История отменена'}[story['status']]
        elif card['current'] == 'Нет активной задачи' and card['remaining']:
            card['current'] = 'Ожидает одобрения плана' if story['status'] == 'PLAN_REVIEW' else 'Задачи в очереди'
    return {'agents': list(cards.values()), 'tracker_available': tracker_available,
            'scope': 'story', 'revision': story['revision']}
