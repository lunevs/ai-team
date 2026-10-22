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
            {'story':story['text'],'plan':story['plan'],'metadata':info,
             'instruction':'Опиши source-to-target mapping пяти метрик и гранулярность dm_credit_portfolio_monthly. Используй только перечисленные источники. Неполный month-end срез пометь missing_snapshot_loans; не переноси прошлый день. Это проект спецификации, не SQL. Если история противоречит фиксированному demo-паспорту или требует иных источников, обязательно задай вопросы.'},
             result_role='system_analyst_spec')
        with db.connection() as conn:
            conn.execute("UPDATE analytics_runs SET status='RUNNING',source_spec=%s,dataset_checksum=%s WHERE story_id=%s AND revision=%s",
                (Jsonb(result.model_dump(mode='json')),info['dataset']['checksum'],story['id'],story['revision']))
        return state

    def financial(state):
        story=db.active(state['story_id']);tracker.honor_remote_stop(story['id'])
        run=get_run(story['id']);expected=reference()
        result=generate(story['id'],'financial_analyst',AnalyticsResult,
            {'story':story['text'],'source_spec':run['source_spec'],'metadata':source_context(),
             'control_examples':[r for r in expected if r['month'] in ['2026-01','2026-06']],
             'instruction':'Подготовь паспорт, бизнес-правила, пользовательские истории и проверки экономического смысла. Не утверждай, что модель исполнила SQL или проверила результат реализации. Данные — синтетический демонстрационный набор. Расхождения с историей/планом обозначь вопросами; не меняй критерии молча.'},
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
        questions=run['source_spec'].get('questions',[])+run['financial_spec'].get('questions',[])
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
    if record['status'] in ['REVIEW','BLOCKED','APPROVED']:return
    graph=build_graph(saver)
    config={'configurable':{'thread_id':f"{story_id}:{record['revision']}:analytics"},'recursion_limit':15}
    snapshot=graph.get_state(config)
    if snapshot.values:
        if snapshot.next:graph.invoke(None,config)
    else:graph.invoke({'story_id':str(story_id)},config)
