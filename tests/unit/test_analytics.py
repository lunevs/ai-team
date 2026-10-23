import pytest
from pydantic import ValidationError
from app.schemas import MetricPassport, SourceSpecification
from app.providers import MockProvider
import json


def test_demo_passport_cannot_silently_change_approved_rules():
    assert MetricPassport().dpd30_threshold==30
    with pytest.raises(ValidationError):MetricPassport(dpd30_threshold=31)
    with pytest.raises(ValidationError):MetricPassport(snapshot='last_available_day')


def test_mapping_requires_all_metrics_once():
    spec,_=MockProvider().generate_structured('system_analyst',json.dumps({}),SourceSpecification)
    assert len({m.target for m in spec.mapping})==5
    raw=spec.model_dump();raw['mapping'][0]=raw['mapping'][1]
    with pytest.raises(ValidationError):SourceSpecification.model_validate(raw)


@pytest.mark.parametrize('content',['{"private-marker":',None])
def test_invalid_provider_output_retains_usage_without_logging_content(monkeypatch,capsys,content):
    from types import SimpleNamespace
    from app import providers
    from app.schemas import AnalyticsResult
    monkeypatch.setattr(providers,'settings',SimpleNamespace(llm_key='test',model='test',structured_mode='json_object',completion_tokens=4096))
    class Client:
        def __init__(self,*args,**kwargs):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def post(self,*args,**kwargs):
            assert kwargs['json']['max_tokens']==8192
            assert kwargs['json']['reasoning']=={'enabled':False}
            return SimpleNamespace(status_code=200,json=lambda:{'choices':[{'message':{'content':content},'finish_reason':'length'}],'usage':{'total_tokens':12,'cost':0.01}})
    monkeypatch.setattr(providers.httpx,'Client',Client)
    with pytest.raises(providers.InvalidModelOutput) as error:
        providers.OpenRouterProvider().generate_structured('financial_analyst','synthetic',AnalyticsResult)
    assert error.value.usage=={'total_tokens':12,'cost':0.01}
    assert 'private-marker' not in capsys.readouterr().out


def test_invalid_output_repair_is_bounded_and_keeps_budget_path(monkeypatch):
    from app import providers
    from app.schemas import PMResult
    calls=[]
    expected=PMResult(goal='Build warehouse',acceptance_criteria=['Document requirements'],priorities=['Clarify scope'])
    def generate(*args):
        calls.append(args)
        if len(calls)==1:
            raise providers.InvalidModelOutput({'total_tokens':12},[{'field':'priorities','type':'missing'}])
        return expected
    monkeypatch.setattr(providers,'_generate',generate)
    assert providers.generate('story','pm',PMResult,{'story':'request'})==expected
    assert len(calls)==2
    assert calls[1][3]['output_correction']['issues']==[{'field':'priorities','type':'missing'}]
    calls.clear()
    def invalid(*args):
        calls.append(args)
        raise providers.InvalidModelOutput()
    monkeypatch.setattr(providers,'_generate',invalid)
    with pytest.raises(providers.InvalidModelOutput):
        providers.generate('story','pm',PMResult,{'story':'request'})
    assert len(calls)==2
