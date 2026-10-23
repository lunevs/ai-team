"""Human-readable explanations shared by customer and team views."""
def waiting_reason(story):
    if story.get('error_type') == 'BudgetExceeded':
        return 'Ждёт увеличения лимита LLM-вызовов, токенов или стоимости'
    if story.get('status') == 'BLOCKED' and (story.get('feasibility') or {}).get('status') in ['NEEDS_PLATFORM','NEEDS_ANALYSIS']:
        return 'Команде нужно подготовить недостающие возможности' if story['feasibility']['status']=='NEEDS_PLATFORM' else 'Требуется проверка технических предпосылок плана'
    if story.get('status') == 'BLOCKED':
        return 'Ждёт ответов заказчика на уточняющие вопросы' if story.get('questions') else 'Ждёт решения по блокировке; см. события истории'
    if story.get('status') == 'FAILED':
        return 'Ждёт устранения ошибки и возобновления истории'
    return None
