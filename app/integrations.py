"""Allowlisted external adapters. Call under the per-story advisory lock."""
import json
import time
from urllib.parse import quote
from typing import Protocol
import httpx
from app import db
from app.config import settings
from app.observability import tracer,log

class RetryableIntegrationError(Exception): pass
class IntegrationError(Exception): pass

class TaskTracker(Protocol):
    def ensure(self,story_id,key,title,body,type_id,parent=None,assignee=None,role='system_analyst'): ...
    def set_status(self,story_id,status): ...

class KnowledgeBase(Protocol):
    def ensure_page(self,story_id,key,title,markdown): ...

class OpenProject:
    def request(self,path,method='GET',data=None,role='system_analyst'):
        if not path.startswith('/api/v3/') or '://' in path: raise IntegrationError()
        token=settings.pm_token if role=='pm' else settings.sa_token
        if not token: raise IntegrationError()
        for attempt in range(3):
            try:
                with httpx.Client(timeout=20) as client:
                    r=client.request(method,settings.op_url+path,auth=('apikey',token),json=data,headers={'Accept':'application/json'})
            except httpx.TransportError:
                # POST outcome may be ambiguous: caller must reconcile marker before retry.
                raise RetryableIntegrationError() from None
            if r.status_code in [301,302,303,307,308]:
                location=r.headers.get('location','')
                if not location.startswith('/api/v3/') or '://' in location:
                    raise IntegrationError('Redirect outside allowed API')
                if method!='GET' and r.status_code not in [307,308]:
                    raise IntegrationError('Ambiguous mutation redirect')
                path=location
                continue
            if r.status_code in [429,500,502,503,504]:
                if method=='POST' or attempt==2: raise RetryableIntegrationError()
                time.sleep(0.3*2**attempt)
                continue
            if r.status_code==409: raise RetryableIntegrationError()
            if r.status_code>=400:
                log('integration.error',service='openproject',http_status=r.status_code)
                raise IntegrationError(f'OpenProject HTTP {r.status_code}')
            return r.json()
        raise RetryableIntegrationError()

    def collection(self,path,role='system_analyst'):
        while path:
            page=self.request(path,role=role)
            yield from page['_embedded']['elements']
            path=page.get('_links',{}).get('nextByOffset',{}).get('href')

    def ensure(self,story_id,key,title,body,type_id,parent=None,assignee=None,role='system_analyst'):
        cached=db.external(story_id,key)
        if cached: return cached['external_id']
        db.active(story_id)
        marker=f'[AI:{story_id}:{key}]'
        # Narrow by stable title, then verify the exact immutable description marker.
        # The operator '~' is verified against the local OpenProject API; no full-project scan.
        filters=quote(json.dumps([{'subject':{'operator':'~','values':[title[:200]]}}]),safe='')
        for item in self.collection(f'/api/v3/projects/{settings.project_id}/work_packages?pageSize=100&filters='+filters,role):
            if marker in item.get('description',{}).get('raw',''):
                db.save_external(story_id,key,item['id'],settings.op_public_url+'/work_packages/'+str(item['id']))
                return item['id']
        links={'type':{'href':f'/api/v3/types/{type_id}'}}
        if parent: links['parent']={'href':f'/api/v3/work_packages/{parent}'}
        if assignee: links['assignee']={'href':f'/api/v3/users/{assignee}'}
        db.active(story_id)
        with tracer.start_as_current_span('tracker.create',attributes={'story_id':str(story_id),'role':role}):
            item=self.request(f'/api/v3/projects/{settings.project_id}/work_packages','POST',{'subject':title[:200],'description':{'format':'markdown','raw':marker+'\n\n'+body},'_links':links},role)
        db.save_external(story_id,key,item['id'],settings.op_public_url+'/work_packages/'+str(item['id']))
        return item['id']

    def set_status(self,story_id,status):
        main=db.external(story_id,'main')
        if not main or status not in settings.status_ids: return
        item=self.request('/api/v3/work_packages/'+str(main['external_id']),role='pm')
        status_id=settings.status_ids[status]
        if item['_links']['status']['href']==f'/api/v3/statuses/{status_id}': return
        self.request('/api/v3/work_packages/'+str(item['id']),'PATCH',{'lockVersion':item['lockVersion'],'_links':{'status':{'href':f'/api/v3/statuses/{status_id}'}}},'pm')

    def honor_remote_stop(self,story_id):
        story=db.active(story_id)
        main=db.external(story_id,'main')
        if not main or story['status']=='NEW': return
        remote=self.request('/api/v3/work_packages/'+str(main['external_id']),role='pm')
        remote_id=remote['_links']['status']['href'].rsplit('/',1)[-1]
        name=next((k for k,v in settings.status_ids.items() if str(v)==remote_id),None)
        if name=='CANCELLED':
            with db.connection() as conn:
                conn.execute("UPDATE stories SET status='CANCELLED' WHERE id=%s",(story_id,))
                db.event(conn,story,'story.cancelled',{'source':'openproject'},f'{story_id}:cancelled')
            raise db.Cancelled()
        if name=='BLOCKED' and story['status']!='BLOCKED':
            db.set_status(story_id,'BLOCKED')
            raise RemoteBlocked()

class RemoteBlocked(Exception): pass

class BookStack:
    def request(self,path,method='GET',data=None):
        with tracer.start_as_current_span('tool.knowledge'):
            return self._request(path,method,data)

    def _request(self,path,method='GET',data=None):
        if not path.startswith('/') or '://' in path: raise IntegrationError()
        try:
            with httpx.Client(timeout=20) as client:
                response=client.request(method,settings.book_url+'/api'+path,headers={'Authorization':'Token '+settings.book_token_id+':'+settings.book_token_secret},json=data)
        except httpx.TransportError:
            raise RetryableIntegrationError() from None
        if response.status_code in [429,500,502,503,504]: raise RetryableIntegrationError()
        if response.status_code>=400:
            log('integration.error',service='bookstack',http_status=response.status_code)
            raise IntegrationError(f'BookStack HTTP {response.status_code}')
        return response.json()

    def pages(self):
        offset=0
        while True:
            result=self.request(f'/pages?count=100&offset={offset}')
            yield from result['data']
            offset+=len(result['data'])
            if offset>=result['total'] or not result['data']: return

    def ensure_page(self,story_id,key,title,markdown):
        cache_key='doc:'+key
        cached=db.external(story_id,cache_key)
        if cached: return cached['url']
        db.active(story_id)
        marker=f'AI {story_id} {key}'
        name=(marker+' — '+title)[:200]
        found=next((p for p in self.pages() if p['book_id']==settings.book_id and p['name']==name),None)
        if found is None:
            db.active(story_id)
            found=self.request('/pages','POST',{'book_id':settings.book_id,'name':name,'markdown':markdown})
        url=settings.book_public_url+'/books/bank-ai-delivery-team/page/'+found['slug']
        db.save_external(story_id,cache_key,found['id'],url)
        return url

tracker=OpenProject()
knowledge=BookStack()

def ensure_main(story_id):
    story=db.active(story_id)
    return tracker.ensure(story_id,'main','История: '+story['text'][:150],
        f"Роль: PM\nStory ID: {story_id}\nTrace ID: {story['trace_id']}\n\n{story['text']}",6,assignee=settings.agent_ids.get('pm'),role='pm')

def sync_status(story_id,status):
    if status!='CANCELLED': tracker.honor_remote_stop(story_id)
    tracker.set_status(story_id,status)
    if status!='CANCELLED': db.set_status(story_id,status)

def publish_plan(story_id,pm,plan,feasibility=None):
    tracker.honor_remote_stop(story_id)
    story=db.active(story_id)
    revision=story['revision']
    prefix=f'r{revision}:'
    text='# Цель\n'+pm.goal+'\n\n# Критерии приёмки\n'+'\n'.join('- '+x for x in pm.acceptance_criteria)+'\n\n# Архитектура\n'+plan.architecture+'\n\n# План\n'+json.dumps(plan.model_dump(),ensure_ascii=False,indent=2)
    if feasibility:text+='\n\n# Проверка возможностей\n'+feasibility['documentation_url']
    page=knowledge.ensure_page(story_id,prefix+'plan','План и архитектура',text)
    main=ensure_main(story_id)
    main_item=tracker.request(f'/api/v3/work_packages/{main}',role='pm')
    if page not in main_item['description']['raw']:
        db.active(story_id)
        tracker.request(f'/api/v3/work_packages/{main}','PATCH',{'lockVersion':main_item['lockVersion'],'description':{'format':'markdown','raw':main_item['description']['raw']+'\n\nДокументация: '+page}},'pm')
    goal=tracker.ensure(story_id,prefix+'goal',pm.goal,'Документация: '+page,3,parent=main,assignee=settings.agent_ids.get('pm'),role='pm')
    task_ids={}
    tasks=[]
    for epic in plan.epics:
        tracker.honor_remote_stop(story_id)
        epic_id=tracker.ensure(story_id,prefix+'epic:'+epic.key,epic.title,'Архитектура: '+page,5,parent=goal,assignee=settings.agent_ids.get('system_analyst'))
        for task in epic.tasks:
            tracker.honor_remote_stop(story_id)
            task_page=knowledge.ensure_page(story_id,prefix+'task:'+task.key,task.title,
              f'# {task.title}\n\nИсполнитель: {task.role.value}\n\nПлан: {page}\n\nКритерии:\n'+'\n'.join('- '+x for x in task.acceptance_criteria)+'\n\nЗависимости: '+', '.join(task.depends_on)+('\n\nКритерии заказчика и независимые проверки:\n'+'\n'.join('- '+row['criterion']+' — '+row['verification'] for row in feasibility['criteria'] if task.key in row['task_keys']) if feasibility else ''))
            task_ids[task.key]=tracker.ensure(story_id,prefix+'task:'+task.key,task.title,'Документация: '+task_page+'\nРоль: '+task.role.value+f"\nTrace ID: {story['trace_id']}",1,parent=epic_id,assignee=settings.agent_ids.get(task.role.value))
            tasks.append(task)
    for task in tasks:
        for dep in task.depends_on:
            db.active(story_id)
            source,target=task_ids[dep],task_ids[task.key]
            relations=tracker.request(f'/api/v3/work_packages/{source}/relations')['_embedded']['elements']
            def matches(r):
                a=int(r['_links']['from']['href'].rsplit('/',1)[-1]);b=int(r['_links']['to']['href'].rsplit('/',1)[-1])
                return (r['type']=='precedes' and (a,b)==(source,target)) or (r['type']=='follows' and (a,b)==(target,source))
            if not any(matches(r) for r in relations):
                tracker.request(f'/api/v3/work_packages/{source}/relations','POST',{'type':'precedes','_links':{'to':{'href':f'/api/v3/work_packages/{target}'}}})
    with db.connection() as conn:
        conn.execute('INSERT INTO artifacts(story_id,key,url) VALUES(%s,%s,%s) ON CONFLICT(story_id,key) DO UPDATE SET url=excluded.url',(story_id,prefix+'plan',page))
        db.event(conn,story,'plan.published',{'revision':revision,'tasks':len(tasks)},f'{story_id}:{revision}:published')
    return page
