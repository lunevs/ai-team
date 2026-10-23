"""Validated LLM schemas for the trusted portable-product policy."""
from pydantic import Field, model_validator
from typing import Literal
from app.schemas import StrictModel
from app.product_policy import *

class Contribution(StrictModel):
    specification: str=Field(min_length=20,max_length=5000)
    files:dict[str,str]=Field(default_factory=dict,max_length=8)

    @model_validator(mode='after')
    def files_safe(self):
        validate_sources(self.files)
        return self

class ProductSolution(StrictModel):
    title:str=Field(min_length=5,max_length=160)
    kind:Literal['service','job']
    output_type:Literal['application','dashboard','file','presentation','scalar']
    needs_database:bool
    architecture:str=Field(min_length=30,max_length=6000)
    operating_notes:str=Field(min_length=30,max_length=5000)
    files:dict[str,str]=Field(min_length=2,max_length=24)

    @model_validator(mode='after')
    def source_contract(self):
        validate_sources(self.files)
        if 'app.py' not in self.files or not any(k.startswith('tests/test_') and k.endswith('.py') for k in self.files):
            raise ValueError('app.py and unittest tests are required')
        return self

class ProductReview(StrictModel):
    approved:bool
    findings:list[str]=Field(default_factory=list,max_length=12)

