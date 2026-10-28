import hashlib
import json
from typing import TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Command
from psycopg.types.json import Jsonb
from app import db
from app.capabilities import registry
from app.schemas import PMResult,SAResult
from app.synthetic import METRICS,SEED,VERSION,MODEL_CONTRACT
from app.providers import generate
from app.integrations import ensure_main,sync_status,publish_plan,tracker

class State(TypedDict,total=False):
    story_id:str
    pm:dict
    plan:dict
    documentation_url:str
    approved:bool
    feasibility_blocked:bool


def build_graph(saver):
    def preflight_node(state):
        # Capability gaps constrain execution, never the customer's right to see a plan.
        return {'feasibility_blocked':False}

    def pm_node(state):
        id=state['story_id']
        ensure_main(id)
        sync_status(id,'ANALYSIS')
        story=db.active(id)
        context={'story':story['text'],'answers':story['answers'],'delivery_mode':story['delivery_mode'],'clarification_policy':'Учитывай все уточнения заказчика. Более позднее явное изменение имеет приоритет над ранним и исходным запросом; не возвращай отменённые требования. Не изменяй остальные требования без указания заказчика.'}
        if story['delivery_mode']=='product':
            context|={'capabilities':registry(),'technical_policy':'Не добавляй новые бизнес-требования и не спрашивай заказчика о выборе имён полей /health, ответа 404 и внутренних средств реализации: это решения команды. Отсутствие технической возможности — работа команды по расширению платформы. Не задавай заказчику вопросы о доступности инструментов и не исключай требование из критериев из-за отсутствия инструмента.', 'instruction':'Определи цель, критерии, приоритеты и формат результата самостоятельного продукта. Всегда заполни goal (не менее 5 символов), acceptance_criteria и priorities (непустые списки), questions (список). Если нужны уточнения, всё равно верни предварительную цель, критерии и приоритеты; вопросы помести только в questions. Не подставляй кредитное демо. Только публичные/синтетические данные, отдельный продуктовый контур, возможна общая инфраструктура продуктов. Уточни отсутствующие бизнес-требования до реализации; не загружай данные, если это не поручено. Профили: python-locked-v1 — один Python service/job без внешней сети; airflow-postgres-v1 — PostgreSQL, Airflow LocalExecutor, Python web app и шлюз к согласованным точным HTTPS URL. Не заменяй запрошенную BI-систему встроенным экраном без согласования. Не добавляй неподдерживаемые инструменты и описания ограничений в acceptance_criteria: критерии содержат только требования заказчика. Не добавляй презентации, синтетическую замену реальных данных, разрезы сегментов или финансовые правила, если они не указаны. Ограничения показываются отдельно проверкой возможностей.'}
        else:
            context|={'demo_defaults':METRICS,'model_contract':MODEL_CONTRACT,'dataset':{'version':VERSION,'seed':SEED,'months':'2026-01..2026-06','synthetic_only':True},'instruction':'Определи цель, критерии и приоритеты. Используй переданный демонстрационный паспорт; при противоречии истории задай вопрос.'}
        result=generate(id,'pm',PMResult,context)
        with db.connection() as conn:
            row=conn.execute('SELECT * FROM stories WHERE id=%s FOR UPDATE',(id,)).fetchone()
            if row['status']=='CANCELLED': raise db.Cancelled()
            conn.execute('UPDATE stories SET pm=%s,questions=%s WHERE id=%s',(Jsonb(result.model_dump(mode='json')),Jsonb(result.questions),id))
        return {'pm':result.model_dump(mode='json')}

    def blocked_node(state):
        sync_status(state['story_id'],'BLOCKED')
        return {}

    def sa_node(state):
        story=db.active(state['story_id'])
        tracker.honor_remote_stop(story['id'])
        if story['delivery_mode']=='product':
            result=generate(story['id'],'system_analyst',SAResult,{'story':story['text'],'answers':story['answers'],'product':state['pm'],'delivery_mode':'product','capabilities':registry(),
                'instruction':'Подготовь ПОЛНЫЙ предварительный план даже при вопросах или неподдерживаемых участках: сохрани запрошенный объём, явно опиши эти участки и исследовательские задачи, не заявляй их готовность и не подменяй технологии. PM представляет план заказчику; ты назначаешь существующих участников команды исполнителями. Архитектура самостоятельного Python-продукта. Декомпозируй результат на необходимые эпики и задачи по фактическому объёму, без фиксированного числа ролей или задач. Назначь исполнителей и зависимости. Дай задачи разработки кода, подготовки данных/правил, независимых тестов и ревью там, где они нужны. Обязательно заполни product_definition: runtime_profile, sources (id/url/format), kind, needs_database и эксплуатацию. Без внешней сети/расписания используй python-locked-v1. Для Airflow, расписания, внешних источников, XML/ZIP/XLSX — airflow-postgres-v1, kind=service, needs_database=true. Публичные источники описывай точными HTTPS URL (без redirects/credentials/private hosts); URL должны соответствовать поручению, при реальной бизнес-неопределённости нужны уточнения, не подменяй источник демонстрационным. План и источники будут согласованы заказчиком. DAG в dags/, бизнес-модули data/, SQL в migrations/. source_client и инфраструктуру поставит платформа. Раздели исследование source-инструментом, парсер/схему/идемпотентную загрузку, расписание, приложение и независимые проверки. Не создавай kind=package или ключ delivery_package: платформенная задача упаковки добавится автоматически. В разных эпиках используй разные ключи задач, зависимости между эпиками только от более раннего к позднему. Уникальные ключи, ацикличные зависимости. Дашборд предоставляет машиночитаемый /result с charts: [{series: [{values: [числа]}]}], пользовательский интерфейс на /. Файлы результата в /data, независимые проверки JSON/CSV/XLSX/PPTX; отмечать происхождение и полноту данных. Исходники и тесты в отдельном продуктовом пакете с Docker/Compose/install.md; runtime независим от команды. Общая продуктовая инфраструктура разрешена, данные и роли отделены от служебной БД команды. Системный аналитик назначает исполнителей. До 1200 символов архитектуры; краткие критерии.'})
            from app.planning import prepare_product_plan
            return {'plan':prepare_product_plan(result).model_dump(mode='json')}
        result=generate(story['id'],'system_analyst',SAResult,{'story':story['text'],'answers':story['answers'],'product':state['pm'],'instruction':'Подготовь архитектуру и эпики с задачами, критериями, зависимостями и исполнителями. Не исполняй код. Ключи задач уникальны, зависимости ацикличны. Ограничь ответ: 1–2 эпика, ровно 4 задачи, максимум 2 коротких критерия на задачу; архитектура до 1200 символов. Не добавляй SQL или подробные скрипты.'})
        return {'plan':result.model_dump(mode='json')}

    def publish_node(state):
        id=state['story_id']
        story=db.active(id)
        digest=hashlib.sha256(json.dumps({'pm':state['pm'],'plan':state['plan']},sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        with db.connection() as conn:
            row=conn.execute('SELECT * FROM stories WHERE id=%s FOR UPDATE',(id,)).fetchone()
            if row['status']=='CANCELLED': raise db.Cancelled()
            conn.execute('UPDATE stories SET plan=%s,plan_digest=%s,updated_at=now() WHERE id=%s',(Jsonb(state['plan']),digest,id))
        feasibility=None
        if story['delivery_mode']=='product':
            from app.feasibility import assess
            feasibility=assess(story,state['pm'],state['plan'])
        page=publish_plan(id,PMResult.model_validate(state['pm']),SAResult.model_validate(state['plan']),feasibility=feasibility)
        sync_status(id,'BLOCKED' if story['questions'] or (feasibility and feasibility['status']!='READY') else 'PLAN_REVIEW')
        return {'documentation_url':page}

    def approval_node(state):
        story=db.active(state['story_id'])
        tracker.honor_remote_stop(story['id'])
        if not story['approved']:
            interrupt({'action':'approve-plan','revision':story['revision'],'plan_digest':story['plan_digest']})
        story=db.active(story['id'])
        if not story['approved']: raise db.Conflict('Approval is not persisted')
        with db.connection() as conn:
            approval=conn.execute('SELECT 1 FROM approvals WHERE story_id=%s AND revision=%s AND plan_digest=%s',(story['id'],story['revision'],story['plan_digest'])).fetchone()
        if not approval: raise db.Conflict('Approval does not match current plan')
        return {'approved':True}

    def accepted_node(state):
        sync_status(state['story_id'],'IN_PROGRESS')
        story=db.active(state['story_id'])
        with db.connection() as conn:
            db.event(conn,story,'stage1.completed',{'next_stage':'analytics','sql_executed':False},f"{story['id']}:{story['revision']}:stage1")
        return {}

    graph=StateGraph(State)
    for name,node in [('preflight',preflight_node),('pm',pm_node),('blocked',blocked_node),('system_analyst',sa_node),('publish',publish_node),('approval',approval_node),('accepted',accepted_node)]:
        graph.add_node(name,node)
    graph.add_edge(START,'preflight')
    graph.add_conditional_edges('preflight',lambda state:END if state.get('feasibility_blocked') else 'pm')
    graph.add_conditional_edges('pm',lambda state:'system_analyst' if db.get_story(state['story_id'])['delivery_mode']=='product' else ('blocked' if state['pm']['questions'] else 'system_analyst'))
    graph.add_edge('blocked',END)
    graph.add_edge('system_analyst','publish')
    graph.add_conditional_edges('publish',lambda state:'approval' if db.get_story(state['story_id'])['status']=='PLAN_REVIEW' else END)
    graph.add_edge('approval','accepted')
    graph.add_edge('accepted',END)
    return graph.compile(checkpointer=saver)


def run_story(story_id,saver):
    story=db.get_story(story_id)
    if story['status']=='CANCELLED':
        tracker.set_status(story_id,'CANCELLED')
        return
    tracker.honor_remote_stop(story_id)
    from app.execution import get_run as get_execution,run as run_execution
    if get_execution(story_id):
        run_execution(story_id)
        return
    from app.analytics import get_run,run as run_analytics
    if get_run(story_id):
        run_analytics(story_id,saver)
        return
    if story['delivery_mode']=='product' and story['approved']:
        from app.products import run
        run(story_id)
        return
    graph=build_graph(saver)
    config={'configurable':{'thread_id':f'{story_id}:{story["revision"]}'},'recursion_limit':30}
    snapshot=graph.get_state(config)
    if snapshot.values:
        if any(task.interrupts for task in snapshot.tasks):
            if not story['approved']: return
            graph.invoke(Command(resume=True),config)
        elif snapshot.next:
            graph.invoke(None,config)
        elif story['status']=='NEW':
            # Retry after a persisted terminal BLOCKED checkpoint with no new answers.
            graph.invoke({'story_id':str(story_id)},config)
    else:
        graph.invoke({'story_id':str(story_id)},config)
    current=db.get_story(story_id)
    if current['delivery_mode']=='product' and current['approved']:
        from app.products import run
        run(story_id)
