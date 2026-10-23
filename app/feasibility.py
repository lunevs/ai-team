"""Durable, fail-closed check of proposed plans against the trusted profile."""
import hashlib
import json
import re
from pydantic import Field
from psycopg.types.json import Jsonb
from app.schemas import StrictModel
from app.capabilities import registry
from app import db


class CriterionCheck(StrictModel):
    criterion_index: int = Field(ge=0, le=11)
    capability_ids: list[str] = Field(min_length=1, max_length=20)
    task_keys: list[str] = Field(min_length=1, max_length=20)
    verification: str = Field(min_length=10, max_length=1500)


class PlanAssessment(StrictModel):
    criteria: list[CriterionCheck] = Field(min_length=1, max_length=12)
    unknown_requirements: list[str] = Field(default_factory=list, max_length=12)
    technical_decisions: list[str] = Field(default_factory=list, max_length=12)


def plan_digest(pm, plan):
    return hashlib.sha256(json.dumps({'pm':pm,'plan':plan},sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def explicit_capabilities(text):
    """Conservative guard for explicit requests; model cannot omit obvious blockers."""
    checks={'airflow':r'\bair\s?flow\b', 'scheduled_jobs':r'ежеднев|ежесуточ|каждый день|раз в сутки|по расписанию',
            'external_http':r'https?://|скачив|загруз\w*[^.\n]{0,80}(?:сайт|портал|интернет)',
            'external_bi':r'\b(?:superset|metabase|power\s?bi|tableau)\b',
            'xlsx':r'\b(?:xlsx|excel)\b', 'pptx':r'\b(?:pptx|powerpoint)\b'}
    found=set()
    for key,pattern in checks.items():
        for match in re.finditer(pattern,text,re.I):
            # Do not turn explicit exclusions into mandatory capabilities.
            if not re.search(r'(?:без|не нужен|не нужна|не требуется)\s*$',text[max(0,match.start()-30):match.start()],re.I):
                found.add(key)
    return found


def evaluate(story, pm, plan, assessment):
    profile=registry(); capabilities=profile['capabilities']
    tasks={t['key']:t for e in plan['epics'] for t in e['tasks']}
    expected=set(range(len(pm['acceptance_criteria'])))
    seen=[c.criterion_index for c in assessment.criteria]
    issues=[]
    if set(seen)!=expected or len(seen)!=len(set(seen)):
        issues.append('Не каждый критерий заказчика сопоставлен с задачей и способом проверки ровно один раз.')
    rows=[];required=set()
    for row in assessment.criteria:
        required.update(row.capability_ids)
        if not set(row.task_keys)<=set(tasks):issues.append('Проверка ссылается на задачу, которой нет в плане.')
        if row.criterion_index not in expected:continue
        rows.append({'criterion_index':row.criterion_index,'criterion':pm['acceptance_criteria'][row.criterion_index],
                     'task_keys':row.task_keys,'roles':sorted({tasks[k]['role'] for k in row.task_keys if k in tasks}),
                     'capability_ids':row.capability_ids,'verification':row.verification})
    required.update(explicit_capabilities(story['text']+'\n'+story.get('answers','')+'\n'+'\n'.join(pm['acceptance_criteria'])))
    if len(plan['epics'])>profile['limits']['epics']:required.add('multi_epic')
    gaps=[]
    for key in sorted(required):
        cap=capabilities.get(key)
        if cap is None or not cap['supported']:
            gaps.append({'capability':key,'title':cap['name'] if cap else 'Уточнить техническую возможность: '+key,
                         'reason':cap['detail'] if cap else 'Такой возможности нет в проверенном реестре.',
                         'owner':'system_analyst'})
    issues.extend(assessment.unknown_requirements)
    status='NEEDS_PLATFORM' if gaps else 'NEEDS_ANALYSIS' if issues else 'READY'
    return {'status':status,'registry_digest':profile['digest'],'registry_version':profile['version'],
            'plan_digest':plan_digest(pm,plan),'criteria':rows,'gaps':gaps,'analysis_issues':issues,'technical_decisions':assessment.technical_decisions,
            'scope':'Техническая выполнимость предварительного плана; результат ещё не реализован и не проверен.'}


def get(story_id,revision,conn=None):
    if conn is not None:
        row=conn.execute('SELECT report FROM feasibility_reports WHERE story_id=%s AND revision=%s',(story_id,revision)).fetchone()
        return row['report'] if row else None
    with db.connection() as conn:return get(story_id,revision,conn)


def require_ready(story,conn=None):
    report=get(story['id'],story['revision'],conn)
    if not report:raise db.Conflict('Для этого плана ещё не выполнена проверка возможностей. Требуется повторное планирование.')
    if report['status']!='READY':raise db.Conflict('Сначала команда должна устранить технические препятствия, указанные в проверке возможностей.')
    if report['registry_digest']!=registry()['digest'] or report['plan_digest']!=story['plan_digest']:
        raise db.Conflict('Возможности среды или план изменились; нужна новая проверка выполнимости.')


def save(story,report):
    with db.connection() as conn:
        row=conn.execute('SELECT * FROM stories WHERE id=%s FOR UPDATE',(story['id'],)).fetchone()
        if row['status']=='CANCELLED':raise db.Cancelled()
        if row['revision']!=story['revision']:raise db.Conflict('Ревизия истории изменилась')
        conn.execute('INSERT INTO feasibility_reports(story_id,revision,report) VALUES(%s,%s,%s) ON CONFLICT(story_id,revision) DO UPDATE SET report=excluded.report,updated_at=now()',
                     (story['id'],story['revision'],Jsonb(report)))
        db.event(conn,row,'feasibility.checked',{'status':report['status'],'registry_digest':report['registry_digest']},
                 f"{story['id']}:{story['revision']}:feasibility:{report['registry_digest']}:{report['plan_digest']}")


def publish(story,report):
    from app.integrations import knowledge,tracker,ensure_main
    from app.config import settings
    text='# Проверка возможностей\n\n'+report['scope']+'\n\nСтатус: '+report['status']+'\n\n'
    for row in report['criteria']:
        text+=f"## Критерий {row['criterion_index']+1}: {row['criterion']}\nЗадачи: {', '.join(row['task_keys'])}\nИсполнители: {', '.join(row['roles'])}\nПроверка: {row['verification']}\n\n"
    for gap in report['gaps']:text+='## Требуется подготовить: '+gap['title']+'\n'+gap['reason']+'\n\n'
    if report.get('technical_decisions'):text+='## Технические решения команды\n'+'\n'.join('- '+x for x in report['technical_decisions'])+'\n\n'
    if report['analysis_issues']:text+='## Требует разбора аналитика\n'+'\n'.join('- '+x for x in report['analysis_issues'])
    key=f"r{story['revision']}:feasibility:{report['registry_digest'][:12]}:{report['plan_digest'][:12]}"
    page=knowledge.ensure_page(story['id'],key,'Возможности, препятствия и проверка критериев',text)
    parent=ensure_main(story['id']);links=[]
    gaps=report['gaps']+([{'capability':'analysis','title':'Проверить полноту и технические предпосылки плана','reason':'\n'.join(report['analysis_issues'])}] if report['analysis_issues'] else [])
    for gap in gaps:
        gap_key=hashlib.sha256(gap['capability'].encode()).hexdigest()[:16]
        task=tracker.ensure(story['id'],f"r{story['revision']}:feasibility:gap:{gap_key}",'Подготовить возможности: '+gap['title'],
            gap['reason']+'\n\nЭто техническая работа команды, а не вопрос заказчику.\nДокументация: '+page,
            1,parent=parent,assignee=settings.agent_ids.get('system_analyst'))
        links.append({'capability':gap['capability'],'task_id':task,'url':settings.op_public_url+'/work_packages/'+str(task)})
    return report|{'documentation_url':page,'tasks':links}


def assess(story,pm,plan):
    from app.providers import generate
    digest=plan_digest(pm,plan);profile=registry()
    previous=get(story['id'],story['revision'])
    if previous and previous['plan_digest']==digest and previous['registry_digest']==profile['digest']:
        report=previous
    else:
        result=generate(story['id'],'system_analyst',PlanAssessment,
            {'story':story['text'],'answers':story['answers'],'product':pm,'plan':plan,'capabilities':profile,
             'instruction':'Проверь техническую выполнимость всех требований, включая ответы заказчика. Для каждого acceptance_criteria верни ровно одну строку с его индексом (от 0), существующими ключами задач, нужными capability_ids из реестра и конкретным способом независимой проверки. capability_ids содержит только положительно требуемые возможности. Критерий «без БД/сети/расписания/Airflow» НЕ требует product_postgres/external_http/scheduled_jobs/airflow: для выполнения такого запрета достаточно python_service или python_job. Не добавляй отсутствующую возможность только потому, что она перечислена в реестре. Не скрывай неподдерживаемые возможности и не заменяй требование заглушкой. unknown_requirements — только действительно неизвестные внешние условия, без которых исполнение невозможно; для каждого объясни, почему его нельзя решить в имеющейся задаче. Выбор формата health/404, имён файлов/таблиц, stdlib HTTP-сервера и других деталей реализации НЕ является препятствием: аналитик принимает решение, записывает в technical_decisions, разработчик реализует. Доверенный base_image и правила упаковки уже заданы в реестре; их не нужно согласовывать. Задача define_contract или её аналог может уточнить технический контракт без блокировки плана. Это работа команды, не вопросы заказчику. Статус поддержки определяет платформа, не модель. Небольшой профиль не подходит для большого проекта через одну генерацию.'},
            result_role='feasibility_'+profile['digest'][:12]+'_'+digest[:12])
        report=evaluate(story,pm,plan,result)
        save(story,report)
    # Retry external publication after a crash without losing the assessment.
    report=publish(story,report);save(story,report)
    return report


def precheck(story):
    """Explicit missing prerequisites are team work, before any customer questions."""
    profile=registry()
    required=explicit_capabilities(story['text']+'\n'+story.get('answers',''))
    gaps=[{'capability':key,'title':profile['capabilities'][key]['name'],
           'reason':profile['capabilities'][key]['detail'],'owner':'system_analyst'}
          for key in sorted(required) if not profile['capabilities'][key]['supported']]
    if not gaps:return None
    digest=hashlib.sha256((story['text']+'\n'+story.get('answers','')).encode()).hexdigest()
    previous=get(story['id'],story['revision'])
    report=previous if previous and previous['registry_digest']==profile['digest'] and previous['plan_digest']==digest else {
        'status':'NEEDS_PLATFORM','source':'explicit_requirement_precheck','registry_digest':profile['digest'],
        'registry_version':profile['version'],'plan_digest':digest,'criteria':[],'gaps':gaps,
        'analysis_issues':[],'technical_decisions':[],
        'scope':'Явно запрошены недоступные возможности. Полная матрица критериев и план появятся после подготовки среды; заказчику не задаются технические вопросы.'}
    save(story,report);report=publish(story,report);save(story,report)
    return report
