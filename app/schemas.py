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

class PublicSource(StrictModel):
    id: str = Field(min_length=1, max_length=40)
    url: str = Field(min_length=10, max_length=2000)
    format: Literal["csv","json","xml","zip","xlsx"]

    @model_validator(mode="after")
    def public_source(self):
        from app.source_policy import source_definition
        source_definition(self.model_dump())
        return self

class ProductDefinition(StrictModel):
    runtime_profile: Literal["python-locked-v1","airflow-postgres-v1"] = "python-locked-v1"
    sources: list[PublicSource] = Field(default_factory=list, max_length=12)
    title: str = Field(min_length=5, max_length=160)
    kind: Literal['service','job']
    output_type: Literal['application','dashboard','file','presentation','scalar']
    needs_database: bool
    architecture: str = Field(min_length=30, max_length=6000)
    operating_notes: str = Field(min_length=30, max_length=5000)

    @model_validator(mode="after")
    def profile_contract(self):
        from app.source_policy import sources_policy
        sources_policy([s.model_dump() for s in self.sources])
        if self.runtime_profile == "python-locked-v1" and self.sources:
            raise ValueError("External sources require airflow-postgres-v1")
        if self.runtime_profile == "airflow-postgres-v1" and (not self.needs_database or self.kind != "service"):
            raise ValueError("Airflow profile requires a service and product database")
        return self

class StoryInput(StrictModel):
    text: str = Field(min_length=20, max_length=8000)
    delivery_mode: Literal['credit_demo','product'] = 'credit_demo'
    synthetic_only: Literal[True]
    max_calls: int | None = Field(default=None, ge=1)
    max_tokens: int | None = Field(default=None, ge=100)
    max_cost_usd: float | None = Field(default=None, gt=0, allow_inf_nan=False)

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
    depends_on: list[str] = Field(default_factory=list, max_length=500)
    kind: Literal['work','package'] = 'work'

class Epic(StrictModel):
    key: str = Field(pattern=r'^[a-z][a-z0-9_]{0,30}$')
    title: str = Field(min_length=5, max_length=200)
    tasks: list[PlannedTask] = Field(min_length=1, max_length=51)

class SAResult(StrictModel):
    architecture: str = Field(min_length=20, max_length=8000)
    epics: list[Epic] = Field(min_length=1, max_length=12)
    product_definition: ProductDefinition | None = None
    development_mode: Literal['legacy','workspace'] = 'legacy'

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

class SourceMapping(StrictModel):
    target: Literal['balance','dpd30_balance','dpd90_balance','npl90_ratio','interest_income']
    source: str = Field(min_length=5,max_length=500)
    rule: str = Field(min_length=5,max_length=1000)

class SourceSpecification(StrictModel):
    grain: str = Field(min_length=10,max_length=1000)
    mapping: list[SourceMapping] = Field(min_length=5,max_length=5)
    data_quality_rules: list[str] = Field(min_length=2,max_length=10)
    questions: list[str] = Field(default_factory=list,max_length=8)

    @model_validator(mode='after')
    def unique_targets(self):
        if len({m.target for m in self.mapping})!=5:raise ValueError('Every target metric must be mapped once')
        return self

class MetricPassport(StrictModel):
    currency: Literal['RUB'] = 'RUB'
    money_scale: Literal[2] = 2
    dpd30_threshold: Literal[30] = 30
    dpd90_threshold: Literal[90] = 90
    npl_definition: Literal['dpd >= 90'] = 'dpd >= 90'
    snapshot: Literal['last_calendar_day'] = 'last_calendar_day'
    missing_snapshot: Literal['NULL; no carry forward'] = 'NULL; no carry forward'
    zero_denominator: Literal['NULL'] = 'NULL'
    interest_income: Literal['sum of daily income within calendar month'] = 'sum of daily income within calendar month'
    rounding: Literal['ROUND_HALF_UP'] = 'ROUND_HALF_UP'
    ratio_scale: Literal[6] = 6

class AnalyticsResult(StrictModel):
    passport: MetricPassport
    business_rules: list[str] = Field(min_length=3,max_length=12)
    economic_checks: list[str] = Field(min_length=2,max_length=10)
    user_stories: list[str] = Field(min_length=1,max_length=6)
    assumptions: list[str] = Field(min_length=1,max_length=8)
    questions: list[str] = Field(default_factory=list,max_length=8)

class SQLArtifact(StrictModel):
    select_sql:str=Field(min_length=20,max_length=25000)
    explanation:str=Field(min_length=10,max_length=2000)

class SQLReview(StrictModel):
    approved:bool
    findings:list[str]=Field(default_factory=list,max_length=10)
