"""Fixed-repository GitHub adapter. Only this service receives GitHub credentials."""
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import uuid
import httpx
from fastapi import FastAPI,Header,HTTPException
from pydantic import BaseModel,ConfigDict,Field

REPO='/repos/lunevs/ai-team'
FILES={'query.sql','qa.json','review.json','report.xlsx','financial.json','dashboard.py','style.md','architecture.md','acceptance.md'}
app=FastAPI(title='GitHub broker',version='0.4.0')

class TaskBundle(BaseModel):
    model_config=ConfigDict(extra='forbid')
    task_id:int=Field(ge=1)
    epic_id:int=Field(ge=1)
    role:str=Field(pattern=r'^[a-z_]+$')
    files:dict[str,str] # base64, so binary XLSX can be committed without shell commands.

class Publish(BaseModel):
    model_config=ConfigDict(extra='forbid')
    run_id:uuid.UUID
    story_id:uuid.UUID
    revision:int=Field(ge=1)
    documentation_url:str=Field(max_length=1000)
    tasks:list[TaskBundle]=Field(min_length=1,max_length=30)

class Pulls(BaseModel):
    model_config=ConfigDict(extra='forbid')
    pulls:list[dict]=Field(min_length=1,max_length=30)


def auth(value):
    token=os.getenv('GIT_BROKER_API_TOKEN','')
    if not token or not hmac.compare_digest(value or '','Bearer '+token):raise HTTPException(401,'Unauthorized')


def github(path,method='GET',data=None,missing=False):
    if not path.startswith(REPO+'/'):raise ValueError('Repository outside policy')
    token=os.getenv('GITHUB_RUNTIME_TOKEN','')
    if not token:raise HTTPException(503,'GitHub token is not configured')
    try:
        with httpx.Client(timeout=25,follow_redirects=False) as client:
            response=client.request(method,'https://api.github.com'+path,headers={'Authorization':'Bearer '+token,
                'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28'},json=data)
    except httpx.TransportError:raise HTTPException(503,'GitHub connection unavailable') from None
    if response.status_code==404 and missing:return None
    if response.status_code>=400:raise HTTPException(503,'GitHub HTTP '+str(response.status_code))
    return response.json()


def ensure_ref(branch,base):
    if not re.fullmatch(r'(feature|release)/[1-9][0-9]*',branch):raise ValueError('Protected ref')
    existing=github(REPO+'/git/ref/heads/'+branch,missing=True)
    if existing:return existing['object']['sha']
    try:
        return github(REPO+'/git/refs','POST',{'ref':'refs/heads/'+branch,'sha':base})['object']['sha']
    except HTTPException:
        existing=github(REPO+'/git/ref/heads/'+branch,missing=True)
        if existing:return existing['object']['sha']
        raise


def ensure_pr(head,base,title,body):
    pulls=github(REPO+'/pulls?state=all&head=lunevs:'+head+'&base='+base)
    if pulls:
        pr=pulls[0]
        if pr['state']=='closed' and not pr.get('merged_at'):raise HTTPException(409,'Task PR closed without merge')
        return pr
    # Unknown POST outcome is reconciled by head/base on retry, never repeated blindly.
    return github(REPO+'/pulls','POST',{'head':head,'base':base,'title':title[:200],'body':body,'draft':False})


def publish_task(data,task,base_sha):
    if not task.files or not set(task.files)<=FILES:raise HTTPException(422,'File path outside allowlist')
    blobs={}
    total=0
    for name,encoded in sorted(task.files.items()):
        try:content=base64.b64decode(encoded,validate=True)
        except ValueError:raise HTTPException(422,'Invalid base64') from None
        total+=len(content)
        if total>2_000_000:raise HTTPException(422,'Bundle too large')
        blobs[name]=content
    digest=hashlib.sha256(b''.join(k.encode()+b'\0'+v for k,v in sorted(blobs.items()))).hexdigest()
    marker=f'AI run={data.run_id} task={task.task_id} digest={digest}'
    release='release/'+str(task.epic_id);branch='feature/'+str(task.task_id)
    release_sha=ensure_ref(release,base_sha)
    tip=github(REPO+'/git/ref/heads/'+branch,missing=True)
    sha=None
    if tip:
        commit=github(REPO+'/git/commits/'+tip['object']['sha'])
        if marker not in commit['message']:raise HTTPException(409,'Branch changed outside this verified run')
        sha=tip['object']['sha']
    else:
        tree=[]
        workspace=Path(os.getenv('GIT_WORKSPACE_ROOT','/data/git'))/data.run_id.hex/str(task.task_id)
        workspace.mkdir(parents=True,exist_ok=True)
        for name,content in blobs.items():
            (workspace/name).write_bytes(content)
            blob=github(REPO+'/git/blobs','POST',{'encoding':'base64','content':base64.b64encode(content).decode()})
            tree.append({'path':f'deliveries/{data.story_id}/r{data.revision}/task-{task.task_id}/{name}',
                         'mode':'100644','type':'blob','sha':blob['sha']})
        parent=github(REPO+'/git/commits/'+release_sha)
        created_tree=github(REPO+'/git/trees','POST',{'base_tree':parent['tree']['sha'],'tree':tree})
        commit=github(REPO+'/git/commits','POST',{'message':marker,'tree':created_tree['sha'],'parents':[release_sha]})
        sha=commit['sha']
        # Create the feature ref directly at its immutable commit. Never force-push or update main.
        if ensure_ref(branch,sha)!=sha:raise HTTPException(409,'Feature ref raced with another writer')
    pr=ensure_pr(branch,release,f'Задача #{task.task_id}: {task.role}',
        f'Результат исполнения истории {data.story_id}, ревизия {data.revision}.\n\n'
        f'Задача: http://localhost:8080/work_packages/{task.task_id}\nДокументация: {data.documentation_url}\n\n'
        'SQL проверен sandbox и независимым QA. LLM-review аналитика приложено как артефакт и не заменяет независимое GitHub-review. '
        'Автор PR не может одобрить собственный PR. Автослияние отключено.\n\n'+marker)
    return {'task_id':task.task_id,'epic_id':task.epic_id,'branch':branch,'release_branch':release,
            'commit':sha,'pull_request_url':pr['html_url'],'number':pr['number'],'author':pr['user']['login']}

@app.get('/health')
def health():return {'status':'ok'}

@app.get('/ready')
def ready(authorization:str=Header(default='')):
    auth(authorization)
    if not os.getenv('GITHUB_RUNTIME_TOKEN'):return {'configured':False}
    result=github(REPO+'/git/ref/heads/'+os.getenv('GITHUB_BASE_REF','release/188'))
    return {'configured':True,'base_sha':result['object']['sha']}

@app.post('/publish')
def publish(data:Publish,authorization:str=Header(default='')):
    auth(authorization)
    if len({t.task_id for t in data.tasks})!=len(data.tasks):raise HTTPException(422,'Duplicate task')
    base=github(REPO+'/git/ref/heads/'+os.getenv('GITHUB_BASE_REF','release/188'))['object']['sha']
    # Stable refs and markers reconcile partial publication after process/network failures.
    return {'pulls':[publish_task(data,task,base) for task in data.tasks],'independent_review_required':True}


def evaluate_pull(pr,reviews,checks,statuses,expected_commit):
    reasons=[]
    if pr['head']['sha']!=expected_commit:reasons.append('PR changed after QA')
    if not pr.get('merged'):reasons.append('PR not merged')
    # The latest non-dismissed submitted review for each reviewer is authoritative.
    latest={}
    for review in sorted(reviews,key=lambda x:(x.get('submitted_at') or '',x.get('id',0))):
        if review.get('state') in ['APPROVED','CHANGES_REQUESTED','DISMISSED']:
            latest[review['user']['login']]=review['state']
    others={who:state for who,state in latest.items() if who!=pr['user']['login']}
    if 'APPROVED' not in others.values() or 'CHANGES_REQUESTED' in others.values():reasons.append('Independent approval required')
    latest_checks={}
    for check in checks:
        if check['name'] not in latest_checks or check.get('id',0)>latest_checks[check['name']].get('id',0):latest_checks[check['name']]=check
    required=[v for v in os.getenv('GITHUB_REQUIRED_CHECKS','application,validate').split(',') if v]
    if any(name not in latest_checks or latest_checks[name]['status']!='completed' or latest_checks[name].get('conclusion')!='success' for name in required):reasons.append('Required CI checks not successful')
    if not latest_checks:reasons.append('No CI checks')
    elif any(c['status']!='completed' or c.get('conclusion') not in ['success','neutral','skipped'] for c in latest_checks.values()):reasons.append('CI not successful')
    if statuses.get('total_count',0) and statuses.get('state')!='success':reasons.append('Commit statuses not successful')
    return {'eligible':not reasons,'reasons':reasons}

@app.post('/gate')
def gate(data:Pulls,authorization:str=Header(default='')):
    auth(authorization);results=[]
    for item in data.pulls:
        number=int(item['number']);expected=str(item['commit'])
        if not re.fullmatch(r'[0-9a-f]{40}',expected):raise HTTPException(422,'Invalid commit')
        pr=github(REPO+'/pulls/'+str(number))
        reviews=github(REPO+f'/pulls/{number}/reviews?per_page=100')
        checks=github(REPO+f'/commits/{expected}/check-runs?per_page=100')['check_runs']
        statuses=github(REPO+f'/commits/{expected}/status')
        results.append({'number':number,**evaluate_pull(pr,reviews,checks,statuses,expected)})
    return {'eligible':all(r['eligible'] for r in results),'pulls':results}
