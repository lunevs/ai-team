"""Independent acceptance contracts and fail-closed, version-bound evidence."""
import hashlib
import json
import math
import re
from decimal import Decimal, InvalidOperation


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def validate_plan(plan,criteria_count):
    if set(plan)!={'checks','not_applicable_reason'}:raise ValueError('Invalid verification plan')
    checks=plan['checks']
    if not isinstance(checks,list) or not 1<=len(checks)<=30:raise ValueError('At least one independent check is required')
    ids=set()
    for c in checks:
        if set(c)!={'id','criterion','description','basis','source','path','pointer','operator','expected','tolerance'}:raise ValueError('Invalid check fields')
        if not re.fullmatch(r'[a-z][a-z0-9_-]{0,39}',c['id']) or c['id'] in ids:raise ValueError('Duplicate or invalid check id')
        ids.add(c['id'])
        if type(c['criterion']) is not int or not 0<=c['criterion']<criteria_count:raise ValueError('Unknown acceptance criterion')
        for key in ('description','basis'):
            if not isinstance(c[key],str) or not 10<=len(c[key])<=1500:raise ValueError('Independent description and basis required')
        if c['source'] not in {'result','json_file','csv_file','xlsx_file','pptx_file'}:raise ValueError('Unsupported source')
        if c['source']=='result':
            if c['path']!='':raise ValueError('Result uses the fixed /result endpoint')
        elif not re.fullmatch(r'[a-zA-Z0-9_-][a-zA-Z0-9_./-]{0,119}',c['path']) or any(x in {'','.','..'} for x in c['path'].split('/')):raise ValueError('Invalid output path')
        elif not c['path'].endswith({'json_file':'.json','csv_file':'.csv','xlsx_file':'.xlsx','pptx_file':'.pptx'}[c['source']]):raise ValueError('Output format mismatch')
        if not isinstance(c['pointer'],str) or len(c['pointer'])>300 or (c['pointer'] and not c['pointer'].startswith('/')):raise ValueError('Use a JSON pointer')
        if c['operator'] not in {'equals','number','min_count','nonempty'}:raise ValueError('Unsupported comparison')
        if not isinstance(c['expected'],(str,int,float,bool,list,dict,type(None))):raise ValueError('Invalid expectation')
        if len(json.dumps(c['expected']))>2000:raise ValueError('Oversized expectation')
        tolerance=Decimal(str(c['tolerance']))
        if not tolerance.is_finite() or tolerance<0:raise ValueError('Invalid tolerance')
        if c['operator'] in {'number','min_count'}:
            number=Decimal(str(c['expected']))
            if not number.is_finite() or isinstance(c['expected'],bool):raise ValueError('Finite numeric expectation required')
            if c['operator']=='min_count' and (number<1 or number!=int(number)):raise ValueError('Positive row count required')
    return plan


def make_contract(record,story,qa,financial):
    criteria=story['pm']['acceptance_criteria']
    validate_plan(qa,len(criteria));validate_plan(financial,len(criteria))
    if {c['criterion'] for c in qa['checks']}!=set(range(len(criteria))):raise ValueError('QA must cover every acceptance criterion')
    if qa['not_applicable_reason']:raise ValueError('QA cannot waive acceptance criteria')
    if financial['not_applicable_reason'] and len(financial['not_applicable_reason'])<20:raise ValueError('Explain why no financial calculations apply')
    body={'version':1,'package_sha256':record['package']['sha256'],'revision':story['revision'],
          'plan_digest':story['plan_digest'],'criteria':criteria,'output_type':record['specification']['output_type'],
          'qa':qa,'financial_analyst':financial}
    return body|{'digest':digest(body)}


def validate_contract(contract):
    body={k:v for k,v in contract.items() if k!='digest'}
    if contract['digest']!=digest(body):raise ValueError('Verification contract changed')
    count=len(contract['criteria'])
    validate_plan(contract['qa'],count);validate_plan(contract['financial_analyst'],count)
    if {c['criterion'] for c in contract['qa']['checks']}!=set(range(count)):raise ValueError('Incomplete criteria coverage')
    return contract


def pointer(value,path):
    for key in path.split('/')[1:] if path else []:
        key=key.replace('~1','/').replace('~0','~')
        value=value[int(key)] if isinstance(value,list) else value[key]
    return value


def meaningful(value):
    if value is None or value=='' or value==[] or value=={}:return False
    if isinstance(value,dict):return any(meaningful(v) for v in value.values())
    if isinstance(value,list):return any(meaningful(v) for v in value)
    return not isinstance(value,float) or math.isfinite(value)


def result_ready(value,output_type):
    if not meaningful(value):return False
    if output_type!='dashboard':return True
    charts=value.get('charts') if isinstance(value,dict) else None
    return bool(isinstance(charts,list) and charts and all(isinstance(c,dict) and isinstance(c.get('series'),list) and c['series'] and all(isinstance(s,dict) and isinstance(s.get('values'),list) and s['values'] and all(isinstance(v,(int,float)) and not isinstance(v,bool) and meaningful(v) for v in s['values']) for s in c['series']) for c in charts))


def compare(actual,check):
    op=check['operator'];expected=check['expected']
    if op=='nonempty':return meaningful(actual)
    if op=='equals':return type(actual) is type(expected) and actual==expected
    if op=='min_count':return isinstance(actual,(list,dict)) and len(actual)>=int(check['expected'])
    if isinstance(actual,bool):return False
    try:
        a,b,t=map(lambda x:Decimal(str(x)),(actual,expected,check['tolerance']))
        return a.is_finite() and b.is_finite() and abs(a-b)<=t
    except (InvalidOperation,ValueError,TypeError):return False


def receipt_valid(contract,receipt,phase):
    try:
        validate_contract(contract)
        if set(receipt)!={'contract_digest','package_sha256','phase','checks','outputs','passed'}:return False
        if receipt['contract_digest']!=contract['digest'] or receipt['package_sha256']!=contract['package_sha256'] or receipt['phase']!=phase:return False
        expected={(role,c['id']) for role in ('qa','financial_analyst') for c in contract[role]['checks']}|{('platform','result')}
        checks=receipt['checks']
        if len(checks)!=len(expected) or {(c['role'],c['id']) for c in checks}!=expected:return False
        definitions={(role,c['id']):c for role in ('qa','financial_analyst') for c in contract[role]['checks']}
        for c in checks:
            key=(c['role'],c['id'])
            if key in definitions:
                definition=definitions[key]
                if c.get('criterion')!=definition['criterion'] or c.get('expected')!=definition['expected'] or not compare(c.get('actual'),definition):return False
            elif not result_ready(c.get('actual'),contract['output_type']):return False
        if contract['output_type'] in {'file','presentation'} and not receipt['outputs']:return False
        return receipt['passed'] is True and all(c['passed'] is True for c in checks)
    except (KeyError,ValueError,TypeError):return False


def ready(record,story=None):
    contract=record.get('verification')
    if record.get('assurance_version')!=1 or not contract:return False
    if contract.get('package_sha256')!=(record.get('package') or {}).get('sha256'):return False
    if story and (contract['revision']!=story['revision'] or contract['plan_digest']!=story['plan_digest'] or contract['criteria']!=story['pm']['acceptance_criteria']):return False
    review=record.get('review') or {}
    if review.get('contract_digest')!=contract['digest'] or not all((review.get(r) or {}).get('approved') is True for r in ('qa','architecture','financial')):return False
    return receipt_valid(contract,(record.get('qa') or {}).get('evidence') or {},'check') and receipt_valid(contract,(record.get('deployment') or {}).get('evidence') or {},'deploy')
