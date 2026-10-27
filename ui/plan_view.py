"""Customer-facing projection of a proposal; execution gates remain on the server."""
ROLES={'pm':'Продуктовый менеджер','system_analyst':'Системный аналитик / архитектор','financial_analyst':'Финансовый аналитик','data_developer':'Разработчик данных','application_developer':'Разработчик приложений','designer':'Дизайнер','qa':'Тестировщик'}


def task_states(story):
    feasibility=story.get('feasibility') or {}
    gaps={g['capability'] for g in feasibility.get('gaps',[])}
    blocked={key for c in feasibility.get('criteria',[]) if set(c['capability_ids'])&gaps for key in c['task_keys']}
    tasks=[t for e in (story.get('plan') or {}).get('epics',[]) for t in e['tasks']]
    waiting=set()
    for _ in tasks:
        for task in tasks:
            if task['key'] not in blocked and set(task.get('depends_on',[]))&(blocked|waiting):waiting.add(task['key'])
    return {t['key']:('Сейчас команда не может выполнить' if t['key'] in blocked else 'Ждёт решения по зависимым задачам' if t['key'] in waiting else 'Предлагается к выполнению' if feasibility.get('status')=='READY' else 'Предлагается; выполнимость всего плана пока не подтверждена') for t in tasks}
