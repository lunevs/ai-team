import hashlib
import json
from typing import TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Command
from psycopg.types.json import Jsonb
from app import db
from app.schemas import PMResult,SAResult
from app.providers import generate
from app.integrations import ensure_main,sync_status,publish_plan,tracker

class State(TypedDict,total=False):
    story_id:str
    pm:dict
    plan:dict
    documentation_url:str
    approved:bool


def build_graph(saver):
    def pm_node(state):
        id=state['story_id']
        ensure_main(id)
        sync_status(id,'ANALYSIS')
        story=db.active(id)
        result=generate(id,'pm',PMResult,{'story':story['text'],'answers':story['answers'],'instruction':'Определи цель, критерии и приоритеты. Задавай вопросы только при невозможности подготовить однозначный план.'})
        with db.connection() as conn:
            row=conn.execute('SELECT * FROM stories WHERE id=%s FOR UPDATE',(id,)).fetchone()
            if row['status']=='CANCELLED': raise db.Cancelled()
            conn.execute('UPDATE stories SET pm=%s,questions=%s WHERE id=%s',(Jsonb(result.model_dump()),Jsonb(result.questions),id))
        return {'pm':result.model_dump()}

    def blocked_node(state):
        sync_status(state['story_id'],'BLOCKED')
        return {}

    def sa_node(state):
        story=db.active(state['story_id'])
        tracker.honor_remote_stop(story['id'])
        result=generate(story['id'],'system_analyst',SAResult,{'story':story['text'],'answers':story['answers'],'product':state['pm'],'instruction':'Подготовь архитектуру и эпики с задачами, критериями, зависимостями и исполнителями. Не исполняй код. Ключи задач уникальны, зависимости ацикличны. Достаточно 1–2 эпиков и 4–7 задач.'})
        return {'plan':result.model_dump()}

    def publish_node(state):
        id=state['story_id']
        page=publish_plan(id,PMResult.model_validate(state['pm']),SAResult.model_validate(state['plan']))
        digest=hashlib.sha256(json.dumps({'pm':state['pm'],'plan':state['plan']},sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        with db.connection() as conn:
            row=conn.execute('SELECT * FROM stories WHERE id=%s FOR UPDATE',(id,)).fetchone()
            if row['status']=='CANCELLED': raise db.Cancelled()
            conn.execute('UPDATE stories SET plan=%s,plan_digest=%s,updated_at=now() WHERE id=%s',(Jsonb(state['plan']),digest,id))
        sync_status(id,'PLAN_REVIEW')
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
    for name,node in [('pm',pm_node),('blocked',blocked_node),('system_analyst',sa_node),('publish',publish_node),('approval',approval_node),('accepted',accepted_node)]:
        graph.add_node(name,node)
    graph.add_edge(START,'pm')
    graph.add_conditional_edges('pm',lambda state:'blocked' if state['pm']['questions'] else 'system_analyst')
    graph.add_edge('blocked',END)
    graph.add_edge('system_analyst','publish')
    graph.add_edge('publish','approval')
    graph.add_edge('approval','accepted')
    graph.add_edge('accepted',END)
    return graph.compile(checkpointer=saver)


def run_story(story_id,saver):
    story=db.get_story(story_id)
    if story['status']=='CANCELLED':
        tracker.set_status(story_id,'CANCELLED')
        return
    tracker.honor_remote_stop(story_id)
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
