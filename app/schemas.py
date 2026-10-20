from enum import StrEnum
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

class Role(StrEnum):
    PM = 'pm'
    SYSTEM_ANALYST = 'system_analyst'
    FINANCIAL_ANALYST = 'financial_analyst'
    DATA_DEVELOPER = 'data_developer'
    APPLICATION_DEVELOPER = 'application_developer'
    DESIGNER = 'designer'
    QA = 'qa'

class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')

class StoryInput(StrictModel):
    text: str = Field(min_length=20, max_length=8000)
    synthetic_only: Literal[True]
    max_calls: int = Field(default=8, ge=1, le=8)
    max_tokens: int = Field(default=60000, ge=100, le=60000)
    max_cost_usd: float = Field(default=1.0, gt=0, le=1.0, allow_inf_nan=False)

class Approval(StrictModel):
    revision: int = Field(ge=1)
    plan_digest: str = Field(pattern=r'^[0-9a-f]{64}$')

class Resume(StrictModel):
    answers: str = Field(default='', max_length=8000)

class PMResult(StrictModel):
    goal: str = Field(min_length=5, max_length=2000)
    acceptance_criteria: list[str] = Field(min_length=1, max_length=12)
    priorities: list[str] = Field(min_length=1, max_length=12)
    questions: list[str] = Field(default_factory=list, max_length=8)

class PlannedTask(StrictModel):
    key: str = Field(pattern=r'^[a-z][a-z0-9_]{0,30}$')
    title: str = Field(min_length=5, max_length=200)
    role: Role
    acceptance_criteria: list[str] = Field(min_length=1, max_length=10)
    depends_on: list[str] = Field(default_factory=list, max_length=20)

class Epic(StrictModel):
    key: str = Field(pattern=r'^[a-z][a-z0-9_]{0,30}$')
    title: str = Field(min_length=5, max_length=200)
    tasks: list[PlannedTask] = Field(min_length=1, max_length=10)

class SAResult(StrictModel):
    architecture: str = Field(min_length=20, max_length=8000)
    epics: list[Epic] = Field(min_length=1, max_length=3)

    @model_validator(mode='after')
    def validate_graph(self):
        tasks = [t for e in self.epics for t in e.tasks]
        keys = [t.key for t in tasks]
        if len(set(keys)) != len(keys) or len({e.key for e in self.epics}) != len(self.epics):
            raise ValueError('Duplicate keys')
        by_key = {t.key: t for t in tasks}
        visiting, visited = set(), set()
        def visit(key):
            if key not in by_key or key in visiting:
                raise ValueError('Unknown dependency or dependency cycle')
            if key in visited:
                return
            visiting.add(key)
            for dep in by_key[key].depends_on:
                visit(dep)
            visiting.remove(key)
            visited.add(key)
        for key in keys:
            visit(key)
        return self

# Контракты следующих этапов; наличие схемы не означает исполнение роли.
class FinancialResult(StrictModel):
    metrics: list[dict]
    assumptions: list[str]
    economic_checks: list[str]
    artifact_urls: list[str]

class DevelopmentResult(StrictModel):
    task_id: int
    branch: str
    commit: str
    pull_request_url: str
    test_results: list[dict]
    documentation_url: str

class DataDeveloperResult(DevelopmentResult):
    warehouse_design: str
    synthetic_data_strategy: str

class ApplicationDeveloperResult(DevelopmentResult):
    interfaces: list[str]

class DesignerResult(StrictModel):
    style_guide_url: str
    templates: list[str]
    visual_checks: list[str]

class QAResult(StrictModel):
    passed: bool
    checks: list[dict]
    defects: list[int]
    limitations: list[str]
