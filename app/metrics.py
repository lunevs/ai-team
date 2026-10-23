"""Read-only, bounded-cardinality Prometheus gauges from the committed ledger."""
from collections import Counter
import hmac
import math
import os
from fastapi import Header,HTTPException
from app import db
from app.observability import ROLES,STATUSES

BACKENDS={'mock','openrouter','mixed','pending','private'}

def authenticate(authorization:str=Header(default='')):
    expected=os.getenv('TELEMETRY_METRICS_TOKEN','')
    if not expected or not hmac.compare_digest(authorization,'Bearer '+expected):raise HTTPException(401,'Metrics authorization required')

def snapshot():
    with db.connection() as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        conn.execute("SET LOCAL statement_timeout='2s'")
        stories=conn.execute('''WITH backends AS (
          SELECT story_id,CASE WHEN count(DISTINCT backend)=1 THEN min(backend) ELSE 'mixed' END AS backend
          FROM llm_calls GROUP BY story_id)
          SELECT s.status,coalesce(b.backend,'pending') AS backend,count(*) AS count
          FROM stories s LEFT JOIN backends b ON b.story_id=s.id GROUP BY s.status,b.backend''').fetchall()
        calls=conn.execute('''SELECT role,backend,state,count(*) AS calls,
          coalesce(sum(actual_tokens),0) AS actual_tokens,
          coalesce(sum(reserved_tokens) FILTER(WHERE actual_tokens IS NULL),0) AS reserved_tokens,
          coalesce(sum(actual_cost),0) AS actual_cost,
          coalesce(sum(reserved_cost) FILTER(WHERE actual_cost IS NULL),0) AS reserved_cost,
          count(*) FILTER(WHERE actual_cost IS NULL) AS estimated_calls,
          avg(latency_ms)/1000.0 AS mean_latency,
          percentile_cont(0.95) WITHIN GROUP(ORDER BY latency_ms)/1000.0 AS p95_latency
          FROM llm_calls GROUP BY role,backend,state''').fetchall()
        budgets=conn.execute('''WITH recent AS (
          SELECT id,max_calls,max_tokens,max_cost_usd,updated_at FROM stories
          WHERE status NOT IN ('CANCELLED','DONE') ORDER BY updated_at DESC,id LIMIT 100)
          SELECT s.id,s.max_calls,s.max_tokens,s.max_cost_usd,count(c.id) AS calls,
          coalesce(sum(coalesce(c.actual_tokens,c.reserved_tokens)),0) AS tokens,
          coalesce(sum(coalesce(c.actual_cost,c.reserved_cost)),0) AS cost,
          CASE WHEN count(c.id)=0 THEN 'pending' WHEN count(DISTINCT c.backend)=1 THEN min(c.backend) ELSE 'mixed' END AS backend
          FROM recent s LEFT JOIN llm_calls c ON c.story_id=s.id
          GROUP BY s.id,s.max_calls,s.max_tokens,s.max_cost_usd,s.updated_at ORDER BY s.updated_at DESC,s.id''').fetchall()
        qa=conn.execute("SELECT coalesce(qa->>'passed','unknown') AS result,count(*) AS count FROM execution_attempts GROUP BY qa->>'passed'").fetchall()
        events=conn.execute("SELECT type,count(*) AS count FROM events WHERE type IN ('execution.defect','worker.retry') GROUP BY type").fetchall()
    return {'stories':stories,'calls':calls,'budgets':budgets,'qa':qa,'events':events}

def render(data):
    lines=[]
    descriptions={
      'ai_team_stories':'Current story counts in the durable ledger, including synthetic tests.',
      'ai_team_llm_calls':'Ledger call reservations by role, backend and state; survives process restart.',
      'ai_team_llm_tokens':'Actual tokens and conservative reserved tokens without actual usage.',
      'ai_team_llm_cost_usd':'Actual USD and conservative reserved USD without actual cost.',
      'ai_team_usage_estimated_calls':'Calls whose actual cost is unavailable.',
      'ai_team_llm_latency_seconds':'Ledger mean/p95 call duration; lifetime snapshot, not a selected-window histogram.',
      'ai_team_story_budget_ratio':'Conservative usage/limit for up to 100 recently updated nonterminal stories.',
      'ai_team_qa_attempts':'Persisted deterministic QA attempts; no replay increments.',
      'ai_team_qa_defects':'Committed defect publication events.',
      'ai_team_worker_retries':'Committed retries due to integration/provider errors; excludes crash lease recovery.'}
    for name,description in descriptions.items():lines.extend([f'# HELP {name} {description}',f'# TYPE {name} gauge'])
    def emit(name,value,**labels):
        if value is None:return
        value=float(value)
        if not math.isfinite(value) or value<0:return
        encoded=','.join(key+'="'+str(val).replace('\\','\\\\').replace('"','\\"').replace('\n','\\n')+'"' for key,val in sorted(labels.items()))
        lines.append(name+('{'+encoded+'}' if encoded else '')+' '+format(value,'.15g'))
    for item in data['stories']:
        if item['status'] in STATUSES and item['backend'] in BACKENDS:
            emit('ai_team_stories',item['count'],status=item['status'],backend=item['backend'])
    for item in data['calls']:
        if item['role'] not in ROLES or item['backend'] not in BACKENDS or item['state'] not in {'reserved','completed','failed'}:continue
        labels={k:item[k] for k in ['role','backend','state']}
        emit('ai_team_llm_calls',item['calls'],**labels)
        for accounting in ['actual','reserved']:
            emit('ai_team_llm_tokens',item[accounting+'_tokens'],accounting=accounting,**labels)
            emit('ai_team_llm_cost_usd',item[accounting+'_cost'],accounting=accounting,**labels)
        emit('ai_team_usage_estimated_calls',item['estimated_calls'],**labels)
        emit('ai_team_llm_latency_seconds',item['mean_latency'],quantile='mean',**labels)
        emit('ai_team_llm_latency_seconds',item['p95_latency'],quantile='0.95',**labels)
    for item in data['budgets'][:100]:
        from uuid import UUID
        try:story=str(UUID(str(item['id'])))
        except ValueError:continue
        if item['backend'] not in BACKENDS:continue
        for dimension,key,limit in [('calls','calls','max_calls'),('tokens','tokens','max_tokens'),('cost','cost','max_cost_usd')]:
            if item[limit] is not None and item[limit]>0:emit('ai_team_story_budget_ratio',float(item[key])/float(item[limit]),story_id=story,backend=item['backend'],dimension=dimension)
    counts=Counter({r['result']:r['count'] for r in data['qa']})
    emit('ai_team_qa_attempts',counts['true'],result='passed');emit('ai_team_qa_attempts',counts['false'],result='failed')
    events={r['type']:r['count'] for r in data['events']}
    emit('ai_team_qa_defects',events.get('execution.defect',0));emit('ai_team_worker_retries',events.get('worker.retry',0))
    return '\n'.join(lines)+'\n'
