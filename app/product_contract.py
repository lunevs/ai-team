"""Validated LLM schemas for the trusted portable-product policy."""
from pydantic import Field, model_validator
from typing import Literal
from app.schemas import StrictModel,ProductDefinition
from app.product_policy import *

class Contribution(StrictModel):
    specification: str=Field(min_length=20,max_length=5000)
    files:dict[str,str]=Field(default_factory=dict,max_length=8)

    @model_validator(mode='after')
    def files_safe(self):
        validate_sources(self.files)
        return self

class ProductSolution(ProductDefinition):
    files:dict[str,str]=Field(min_length=2,max_length=256)

    @model_validator(mode='after')
    def source_contract(self):
        validate_sources(self.files,self.runtime_profile)
        if 'app.py' not in self.files or not any(k.startswith('tests/test_') and k.endswith('.py') for k in self.files):
            raise ValueError('app.py and unittest tests are required')
        if self.runtime_profile=='airflow-postgres-v1' and not any(k.startswith('dags/') and k.endswith('.py') for k in self.files):
            raise ValueError('Airflow profile requires a DAG')
        return self

class ProductReview(StrictModel):
    approved:bool
    findings:list[str]=Field(default_factory=list,max_length=12)


class AcceptanceCheck(StrictModel):
    id:str
    criterion:int
    description:str
    basis:str
    source:Literal['result','json_file','csv_file','xlsx_file','pptx_file']='result'
    path:str=''
    pointer:str=''
    operator:Literal['equals','number','min_count','nonempty']
    expected:str|int|float|bool|list|dict|None=None
    tolerance:str='0'

class AcceptancePlan(StrictModel):
    checks:list[AcceptanceCheck]=Field(min_length=1,max_length=30)
    not_applicable_reason:str=Field(default='',max_length=1500)
