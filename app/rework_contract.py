"""Evidence-based defect review and triage; model decisions cannot bypass source checks."""
import hashlib,json
from typing import Literal
from pydantic import Field,model_validator
from app.schemas import StrictModel

class Finding(StrictModel):
    summary:str=Field(min_length=5,max_length=1000)
    criterion:str=Field(min_length=5,max_length=2000)
    evidence:str=Field(min_length=5,max_length=6000)
    severity:Literal['critical','major','minor']='major'
    verification:str=Field(min_length=5,max_length=2000)

class SourceReview(StrictModel):
    approved:bool
    findings:list[Finding]=Field(default_factory=list,max_length=12)
    @model_validator(mode='after')
    def consistent(self):
        if self.approved==bool(self.findings):raise ValueError('Approval and findings disagree')
        return self

class Disposition(StrictModel):
    index:int=Field(ge=0)
    decision:Literal['confirmed','dismissed','technical','product']
    reason:str=Field(min_length=10,max_length=3000)

class Triage(StrictModel):
    findings:list[Disposition]=Field(max_length=24)
    next_action:str=Field(min_length=10,max_length=4000)


def fingerprint(finding):
    # Evidence/commit changes do not erase a repeated underlying issue.
    value={k:' '.join(str(finding[k]).lower().split()) for k in ['summary','criterion','verification']}
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def validate_triage(findings,decision,actual_failure=False):
    if sorted(d.index for d in decision.findings)!=list(range(len(findings))):raise ValueError('Triage must classify each finding exactly once')
    if actual_failure and not any(d.decision in ['confirmed','technical','product'] for d in decision.findings):
        raise ValueError('Actual failed execution cannot be dismissed without a successful recheck')
    return decision
