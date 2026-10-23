"""Token-protected, memory-only Git gate fixture. Never connects to GitHub."""
import os
from fastapi import FastAPI,Header,HTTPException
app=FastAPI();allowed=set();pulls={};releases={}
def auth(value):
    if value!='Bearer '+os.environ['GIT_BROKER_API_TOKEN']:raise HTTPException(401)
@app.get('/ready')
def ready(authorization:str=Header('')):
    auth(authorization);return {'configured':True,'workspace_base_ready':True,'product_base_ready':True}
@app.post('/allow')
def allow(data:dict,authorization:str=Header('')):
    auth(authorization);allowed.add(data['number']);return {'ok':True}
@app.post('/publish')
def publish(data:dict,authorization:str=Header('')):
    auth(authorization);result=[]
    for t in data['tasks']:
        number=t['task_id'];row={'number':number,'task_id':number,'epic_id':t['epic_id'],'commit':format(number,'040x'),
            'pull_request_url':f'https://example.invalid/fixture/pull/{number}','workspace_task':True,'product_package':data.get('product_package',False)}
        pulls[number]=row;result.append(row)
    return {'pulls':result}
@app.post('/gate')
def gate(data:dict,authorization:str=Header('')):
    auth(authorization);return {'eligible':all(p['number'] in allowed for p in data['pulls'])}
@app.post('/release')
def release(data:dict,authorization:str=Header('')):
    auth(authorization);epic=data['epic_id'];number=100000+epic
    row={'epic_id':epic,'number':number,'commit':format(number,'040x'),'pull_request_url':f'https://example.invalid/fixture/pull/{number}','workspace_task':True}
    releases[epic]=row;return row
@app.post('/finalize-release')
def finalize(data:dict,authorization:str=Header('')):
    auth(authorization)
    if data['number'] not in allowed:raise HTTPException(409)
    return {'tag':'fixture-epic-'+str(data['epic_id']),'commit':data['commit']}
