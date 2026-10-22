"""Stage 2: fixed metadata tools, structured analysts and explicit approval."""
import hashlib
import json
from langgraph.graph import StateGraph, START, END
from psycopg.types.json import Jsonb
from app import db
from app.schemas import SourceSpecification, AnalyticsResult
from app.providers import generate
from app.synthetic import metadata,reference,dataset,checksum,VERSION
from app.integrations import tracker,knowledge,sync_status,ensure_main


def get_run(story_id,revision=None):
    with db.connection() as conn:
        if revision is None:
            row=db.get_story(story_id,conn)
            revision=row['revision']
        return conn.execute('SELECT * FROM analytics_runs WHERE story_id=%s AND revision=%s',(story_id,revision)).fetchone()


def source_context():
    with db.connection() as conn:
        info=metadata(conn)
    if not info['dataset'] or info['dataset']['checksum']!=checksum(dataset()):
        raise RuntimeError('Synthetic dataset checksum mismatch')
    return info


def build_graph(saver):
    def system(state):
        story=db.active(state['story_id']);tracker.honor_remote_stop(story['id'])
        info=source_context()
        result=generate(story['id'],'system_analyst',SourceSpecification,
            {'story':story['text'],'answers':story['answers'],'architecture':(story['plan'] or {}).get('architecture','')[:1000],'metadata':info,
             'instruction':'Опиши source-to-target mapping пяти метрик и гранулярность dm_credit_portfolio_monthly. Используй только перечисленные источники. Ответ краткий: grain до 250 символов, правило mapping до 250 символов, 2 коротких data_quality_rules. Дневной остаток NOT NULL, фиктивные случаи NULL в источнике не обсуждай. Неполный month-end срез пометь missing_snapshot_loans; не переноси прошлый день. Это проект спецификации, не SQL. Согласование выполняется отдельной кнопкой UI. Просьбы подтвердить уже заданные правила не являются questions. questions содержит только недостающие факты, которых нет в story/answers/metadata; при полном контракте questions=[]. Семантика model_contract является заданным контрактом fixture, не придумывай альтернатив и не запрашивай её заново. Если история противоречит фиксированному demo-паспорту или требует иных источников, обязательно задай вопросы.'},
             result_role='system_analyst_spec')
        with db.connection() as conn:
            conn.execute("UPDATE analytics_runs SET status='RUNNING',source_spec=%s,dataset_checksum=%s WHERE story_id=%s AND revision=%s",
                (Jsonb(result.model_dump(mode='json')),info['dataset']['checksum'],story['id'],story['revision']))
        return state

    def financial(state):
        story=db.active(state['story_id']);tracker.honor_remote_stop(story['id'])
        run=get_run(story['id']);expected=reference()
        info=source_context()
        financial_metadata={k:v for k,v in info.items() if k!='columns'}
        controls=[r for r in expected if (r['month'],r['product_id'],r['region']) in [('2026-01',1,'North'),('2026-01',2,'South'),('2026-06',1,'South')]]
        result=generate(story['id'],'financial_analyst',AnalyticsResult,
            {'story':story['text'],'answers':story['answers'],'source_spec':{k:v for k,v in run['source_spec'].items() if k!='data_quality_rules'},'metadata':financial_metadata,
             'control_examples':controls,
             'instruction':'Подготовь компактный паспорт, 3 кратких бизнес-правила, 1 пользовательскую историю и 2 проверки экономического смысла. Ответ до 2000 символов. Не утверждай, что модель исполнила SQL или проверила результат реализации. Данные — синтетический демонстрационный набор. Согласование выполняется отдельной кнопкой UI, не добавляй просьбы подтвердить заданные правила в questions. source_row_count/snapshot_loan_count отличают наблюдаемый нулевой остаток от отсутствующего среза. Семантика model_contract и control_examples уже зафиксирована; технические форматы следующих этапов не требуют вопросов. Расхождения с историей/планом обозначь вопросами; не меняй критерии молча.'},
            result_role='financial_analyst_spec')
        with db.connection() as conn:
            conn.execute('UPDATE analytics_runs SET financial_spec=%s,reference_rows=%s WHERE story_id=%s AND revision=%s',
                (Jsonb(result.model_dump(mode='json')),Jsonb(expected),story['id'],story['revision']))
        return state

    def publish(state):
        story=db.active(state['story_id']);tracker.honor_remote_stop(story['id'])
        run=get_run(story['id'])
        body={k:run[k] for k in ['source_spec','financial_spec','dataset_checksum','reference_rows']}
        digest=hashlib.sha256(json.dumps(body,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        text='# Аналитическая спецификация (проект)\n\nНабор: '+VERSION+'\n\nТребует отдельного подтверждения заказчика. SQL агентом не исполнялся.\n\n```json\n'+json.dumps(body,ensure_ascii=False,indent=2)+'\n```'
        page=knowledge.ensure_page(story['id'],f"r{story['revision']}:analytics",'Паспорт метрик и source-to-target mapping',text)
        # Every current task links to the shared specification, including retries.
        with db.connection() as conn:
            tasks=conn.execute('SELECT external_id FROM external_ids WHERE story_id=%s AND key LIKE %s',
                (story['id'],f"r{story['revision']}:task:%")).fetchall()
        ids=[ensure_main(story['id'])]+[x['external_id'] for x in tasks]
        for task_id in ids:
            db.active(story['id']);tracker.honor_remote_stop(story['id'])
            remote=tracker.request(f'/api/v3/work_packages/{task_id}')
            description=remote.get('description',{}).get('raw','')
            if page not in description:
                tracker.request(f'/api/v3/work_packages/{task_id}','PATCH',{'lockVersion':remote['lockVersion'],
                    'description':{'format':'markdown','raw':description+'\n\nАналитическая спецификация: '+page}})
        questions=list(dict.fromkeys(run['source_spec'].get('questions',[])+run['financial_spec'].get('questions',[])))
        sync_status(story['id'],'BLOCKED' if questions else 'PLAN_REVIEW')
        with db.connection() as conn:
            current=conn.execute('SELECT * FROM stories WHERE id=%s FOR UPDATE',(story['id'],)).fetchone()
            if current['status']=='CANCELLED':raise db.Cancelled()
            conn.execute('UPDATE analytics_runs SET status=%s,digest=%s,documentation_url=%s WHERE story_id=%s AND revision=%s',
                ('BLOCKED' if questions else 'REVIEW',digest,page,story['id'],story['revision']))
            conn.execute('UPDATE stories SET questions=%s WHERE id=%s',(Jsonb(questions),story['id']))
            db.event(conn,story,'analytics.published',{'revision':story['revision'],'digest':digest,'sql_executed':False},f"{story['id']}:{story['revision']}:analytics")
        return state

    graph=StateGraph(dict)
    for name,node in [('source_spec',system),('financial_spec',financial),('publish_spec',publish)]:graph.add_node(name,node)
    graph.add_edge(START,'source_spec');graph.add_edge('source_spec','financial_spec')
    graph.add_edge('financial_spec','publish_spec');graph.add_edge('publish_spec',END)
    return graph.compile(checkpointer=saver)


def run(story_id,saver):
    record=get_run(story_id)
    if record['status']=='APPROVED':
        sync_approval_documentation(story_id,record)
        sync_status(story_id,'IN_PROGRESS')
        return
    if record['status'] in ['REVIEW','BLOCKED']:return
    graph=build_graph(saver)
    config={'configurable':{'thread_id':f"{story_id}:{record['revision']}:analytics"},'recursion_limit':15}
    snapshot=graph.get_state(config)
    if snapshot.values:
        if snapshot.next:graph.invoke(None,config)
    else:graph.invoke({'story_id':str(story_id)},config)


def sync_approval_documentation(story_id,record):
    db.active(story_id);tracker.honor_remote_stop(story_id)
    link=db.external(story_id,f"doc:r{record['revision']}:analytics")
    if not link:raise RuntimeError('Missing specification page')
    marker=f"<!-- analytics-approved:{story_id}:{record['revision']}:{record['digest']} -->"
    page=knowledge.request('/pages/'+str(link['external_id']))
    text=page.get('markdown','')
    if marker in text:return
    text=text.replace('# Аналитическая спецификация (проект)','# Аналитическая спецификация (одобрена)',1)
    text=text.replace('Требует отдельного подтверждения заказчика.','Подтверждена заказчиком для указанной ревизии.',1)
    text+='\n\n## Согласование\n'+marker+f"\nРевизия: {record['revision']}\nDigest: {record['digest']}\nДата: {record['approved_at']}\nSQL агентом не исполнялся.\n"
    db.active(story_id)
    knowledge.request('/pages/'+str(link['external_id']),'PUT',{'name':page['name'],'markdown':text})
