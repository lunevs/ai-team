"""Deterministic completion of a dynamic product plan."""
from app.schemas import SAResult,PlannedTask,Role


def prepare_product_plan(plan):
    tasks=[t for e in plan.epics for t in e.tasks]
    if any(t.kind!='work' or t.key=='delivery_package' for t in tasks):
        raise ValueError('Packaging task is platform-owned')
    if not any(t.role==Role.APPLICATION_DEVELOPER for t in tasks):
        raise ValueError('Product plan requires an application developer')
    locations={t.key:i for i,e in enumerate(plan.epics) for t in e.tasks}
    if any(locations[d]>locations[t.key] for t in tasks for d in t.depends_on):
        raise ValueError('An epic cannot depend on a later epic')
    raw=plan.model_dump(mode='json')
    raw['development_mode']='workspace'
    raw['epics'][-1]['tasks'].append(PlannedTask(key='delivery_package',title='Упаковать и передать проверенный продукт',
        role=Role.APPLICATION_DEVELOPER,kind='package',acceptance_criteria=['Полный пакет проходит независимые проверки и устанавливается по install.md'],
        depends_on=[t.key for t in tasks]).model_dump(mode='json'))
    return SAResult.model_validate(raw)
