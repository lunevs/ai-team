import uuid
import pytest
from fastapi import HTTPException
from app.metrics import render,authenticate

def test_metrics_accounting_and_bounded_dimensions(monkeypatch):
    budgets=[{'id':str(uuid.uuid4()),'backend':'mock','calls':4,'tokens':400,'cost':.1,'max_calls':4,'max_tokens':1000,'max_cost_usd':1} for _ in range(120)]
    data={'stories':[{'status':'IN_PROGRESS','backend':'mock','count':1}],
          'calls':[dict(role='pm',backend='mock',state='completed',calls=2,actual_tokens=100,reserved_tokens=80,
                        actual_cost=.01,reserved_cost=.02,estimated_calls=1,mean_latency=1.5,p95_latency=2)],
          'budgets':budgets,'qa':[{'result':'true','count':2},{'result':'false','count':1}],
          'events':[{'type':'execution.defect','count':1}]}
    result=render(data)
    assert 'accounting="actual"' in result and 'accounting="reserved"' in result
    assert len([line for line in result.splitlines() if line.startswith('ai_team_story_budget_ratio{')])==300
    assert 'ai_team_qa_attempts{result="failed"} 1' in result
    assert 'ai_team_llm_cost_usd{accounting="actual",backend="mock",role="pm",state="completed"} 0.01' in result
    assert 'ai_team_llm_cost_usd{accounting="reserved",backend="mock",role="pm",state="completed"} 0.02' in result
    data['calls'][0]['role']='CANARY_SECRET'
    assert 'CANARY_SECRET' not in render(data)
    monkeypatch.setenv('TELEMETRY_METRICS_TOKEN','metrics-only')
    authenticate('Bearer metrics-only')
    with pytest.raises(HTTPException):authenticate('Bearer application-token')
    monkeypatch.delenv('TELEMETRY_METRICS_TOKEN')
    with pytest.raises(HTTPException):authenticate('Bearer ')
