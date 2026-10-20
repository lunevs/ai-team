import pytest
from pydantic import ValidationError
from app.schemas import StoryInput,SAResult
from app.auth import LocalTokenAuth
from fastapi import HTTPException


def test_confidential_input_cannot_be_accepted_by_schema():
    with pytest.raises(ValidationError):
        StoryInput(text='Рассчитать реальные данные банковских клиентов',synthetic_only=False)


def test_plan_rejects_cycles_and_unknown_dependencies():
    base={'architecture':'PostgreSQL и изолированная схема для тестовых расчётов','epics':[{'key':'e','title':'Первый эпик','tasks':[{'key':'a','title':'Первая задача','role':'qa','acceptance_criteria':['Проверка'],'depends_on':['a']}]}]}
    with pytest.raises(ValidationError): SAResult.model_validate(base)
    base['epics'][0]['tasks'][0]['depends_on']=['missing']
    with pytest.raises(ValidationError): SAResult.model_validate(base)


def test_unknown_role_cannot_request_new_privileges():
    with pytest.raises(ValidationError):
        SAResult.model_validate({'architecture':'PostgreSQL и изолированная схема для тестовых расчётов','epics':[{'key':'e','title':'Первый эпик','tasks':[{'key':'a','title':'Первая задача','role':'administrator','acceptance_criteria':['Проверка']}]}]})


def test_auth_denies_when_token_is_missing(monkeypatch):
    from app import auth
    from app.config import Settings
    monkeypatch.setattr(auth,'settings',Settings(api_token=''))
    with pytest.raises(HTTPException): LocalTokenAuth().authenticate('Bearer ')
