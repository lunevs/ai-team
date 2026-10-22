"""Deterministic report delivery and fixed Git broker publication after QA/review."""
import base64
import hashlib
import json
import os
from pathlib import Path
import httpx
from app import db
from app.config import settings
from app.integrations import knowledge,tracker,sync_status,RetryableIntegrationError
from app.reports import create_workbook,monthly,STYLE
from app.observability import tracer

ARTIFACT_FILES={'report.xlsx','query.sql','qa.json','review.json','financial.json','style.md','architecture.md','acceptance.md','dashboard.py'}

def broker(path,payload=None):
    with tracer.start_as_current_span('tool.github',attributes={'stage':path.strip('/').replace('-','_')}):
        return _broker(path,payload)

def _broker(path,payload=None):
    if path not in ['/ready','/publish','/gate','/release','/finalize-release']:raise ValueError('Broker route outside policy')
    try:
        with httpx.Client(timeout=180) as client:
            response=client.request('GET' if payload is None else 'POST',os.getenv('GIT_BROKER_URL','http://git-broker:8000')+path,
                headers={'Authorization':'Bearer '+os.getenv('GIT_BROKER_API_TOKEN','')},json=payload)
    except httpx.TransportError:raise RetryableIntegrationError('Git broker unavailable') from None
    if response.status_code==409 and path=='/finalize-release':return {'pending':True}
    if response.status_code>=400:raise RetryableIntegrationError('Git broker HTTP '+str(response.status_code))
    return response.json()

def artifact_path(run_id,name):
    import uuid
    if name not in ARTIFACT_FILES:raise ValueError('Unknown artifact')
    return Path(os.getenv('ARTIFACT_ROOT','/data/artifacts'))/uuid.UUID(str(run_id)).hex/name

def publish_tasks(story,record,documentation_url,tasks):
    from app.execution import update,event
    existing=record['git'] or {}
    pulls={p['task_id']:p for p in existing.get('pulls',[])}
    result=existing|{'pulls':list(pulls.values()),'documentation_url':documentation_url}
    for task in tasks:
        tracker.honor_remote_stop(story['id'])
        if task['task_id'] in pulls:continue
        published=broker('/publish',{'run_id':str(record['id']),'story_id':str(story['id']),
            'revision':story['revision'],'documentation_url':documentation_url,'tasks':[task]})
        for pull in published['pulls']:pulls[pull['task_id']]=pull
        result=published|{'pulls':list(pulls.values()),'documentation_url':documentation_url}
        # An already sent HTTP request can complete during cancellation. Keep its result
        # for audit before checking the stop again; never start the next task after a stop.
        update(record['id'],status='PUBLISHED',git=result)
        event(story,'execution.task_published',{'pulls':published['pulls']},f"{record['id']}:task-published:{task['task_id']}")
    return result

def complete_acceptance(story,record):
    from app.execution import update,event
    tracker.honor_remote_stop(story['id'])
    gate=broker('/gate',{'pulls':record['git']['pulls']})
    if not gate['eligible']:raise db.Conflict('Git gate changed after business acceptance')
    releases=[]
    for epic_id in sorted({p['epic_id'] for p in record['git']['pulls']}):
        tracker.honor_remote_stop(story['id'])
        releases.append(broker('/release',{'epic_id':epic_id,'pulls':[p for p in record['git']['pulls'] if p['epic_id']==epic_id]}))
    update(record['id'],status='RELEASE_REVIEW',git=record['git']|{'releases':releases})
    event(story,'execution.release_requested',{'releases':releases},f"{record['id']}:release-requested")

def finalize_delivery(story,record):
    from app.execution import update,event
    tracker.honor_remote_stop(story['id'])
    tags=[]
    for release in record['git']['releases']:
        tracker.honor_remote_stop(story['id'])
        result=broker('/finalize-release',{'epic_id':release['epic_id'],'number':release['number'],'commit':release['commit']})
        if result.get('pending'):
            update(record['id'],status='RELEASE_REVIEW')
            event(story,'execution.release_waiting',{'epic_id':release['epic_id']});return
        tags.append(result)
    record=record|{'git':record['git']|{'tags':tags}}
    update(record['id'],git=record['git'])
    for pull in record['git']['pulls']:
        tracker.honor_remote_stop(story['id'])
        item=tracker.request('/api/v3/work_packages/'+str(pull['task_id']))
        tracker.request('/api/v3/work_packages/'+str(pull['task_id']),'PATCH',{'lockVersion':item['lockVersion'],
            '_links':{'status':{'href':'/api/v3/statuses/'+str(settings.status_ids['DONE'])}}})
    for epic_id in sorted({p['epic_id'] for p in record['git']['pulls']}):
        item=tracker.request('/api/v3/work_packages/'+str(epic_id))
        tracker.request('/api/v3/work_packages/'+str(epic_id),'PATCH',{'lockVersion':item['lockVersion'],
            '_links':{'status':{'href':'/api/v3/statuses/'+str(settings.status_ids['DONE'])}}})
    goal=db.external(story['id'],f"r{story['revision']}:goal")
    if goal:
        tracker.honor_remote_stop(story['id'])
        item=tracker.request('/api/v3/work_packages/'+str(goal['external_id']))
        tracker.request('/api/v3/work_packages/'+str(goal['external_id']),'PATCH',{'lockVersion':item['lockVersion'],
            '_links':{'status':{'href':'/api/v3/statuses/'+str(settings.status_ids['DONE'])}}})
    knowledge.ensure_page(story['id'],f"r{story['revision']}:acceptance",'Приёмка результата',
        '# Приёмка\n\nЗаказчик: '+record['business_accepted_by']+'\nДата: '+record['business_accepted_at'].isoformat()+
        '\n\nПроверены неизменность commit после QA, независимое review, CI и слияние всех task PR.\n\n'+json.dumps(record['git'],ensure_ascii=False,indent=2))
    sync_status(story['id'],'DONE');update(record['id'],status='DONE')
    event(story,'execution.completed',{},f"{record['id']}:completed")

def prepare_and_publish(story,record):
    from app.execution import update,event
    if not record['qa']['passed'] or not record['review']['approved']:raise db.Conflict('QA and architecture review required')
    tracker.honor_remote_stop(story['id'])
    with db.connection() as conn:
        spec=conn.execute('SELECT * FROM analytics_runs WHERE story_id=%s AND revision=%s',(story['id'],story['revision'])).fetchone()
        dataset=conn.execute('SELECT version,seed,checksum FROM synthetic.dataset_info WHERE singleton=true').fetchone()
    if not spec or spec['status']!='APPROVED' or spec['digest']!=record['analytics_digest']:raise db.Conflict('Analytical approval changed')
    root=artifact_path(record['id'],'query.sql').parent;root.mkdir(parents=True,exist_ok=True)
    metadata={'dataset':f"{dataset['version']}, seed {dataset['seed']}",'checksum':dataset['checksum'],
              'sql_digest':hashlib.sha256(record['sql_text'].encode()).hexdigest(),'story_id':str(story['id']),
              'revision':story['revision'],'created_at':record['created_at'].isoformat()}
    financial={'metadata':metadata,'monthly':monthly(record['rows']),'economic_checks':record['qa']['checks'],
               'method':'Decimal aggregation; NPL weighted by outstanding balance; absent observations stay NULL'}
    architecture=story['plan']['architecture']+'\n\nSQL исполняется только в sandbox; QA использует независимый Decimal-эталон.\n'
    files={'query.sql':record['sql_text'],'qa.json':json.dumps(record['qa'],ensure_ascii=False,indent=2),
           'review.json':json.dumps(record['review'],ensure_ascii=False,indent=2),
           'financial.json':json.dumps(financial,ensure_ascii=False,indent=2),
           'style.md':'# Стандарт визуализации\n\n'+json.dumps(STYLE,ensure_ascii=False,indent=2)+'\nДенежные суммы: 2 знака; NPL: проценты; NULL: тире. Остатки не суммировать между месяцами.\n',
           'architecture.md':architecture,
           'acceptance.md':'# Приёмка\n\nПроверить QA, независимое GitHub review, CI и слияние всех задач. Затем заказчик подтверждает результат в UI.\n',
           'dashboard.py':'# Declarative dashboard specification; generated Python is never executed.\nMETRICS = '+repr(['balance','dpd30_balance','dpd90_balance','npl90_ratio','interest_income'])+'\nGRAIN = ("month", "product_id", "region")\n'}
    if record['artifacts']:
        artifacts=record['artifacts']
        for name,manifest in artifacts.items():
            path=artifact_path(record['id'],name)
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=manifest['sha256']:
                raise db.Conflict('Previously verified artifact is missing or changed')
    else:
        for name,content in files.items():(root/name).write_text(content)
        with tracer.start_as_current_span('artifact.publish',attributes={'story_id':str(story['id']),'backend':'deterministic'}):
            with tracer.start_as_current_span('agent.financial_analyst',attributes={'role':'financial_analyst','backend':'deterministic'}):
                create_workbook(record['rows'],metadata,root/'report.xlsx')
        artifacts={name:{'url':f"/stories/{story['id']}/execution/artifacts/{name}",'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                         'size':path.stat().st_size} for name in ARTIFACT_FILES if (path:=root/name).exists()}
    update(record['id'],artifacts=artifacts)
    for role in ['designer','financial_analyst','application_developer','qa']:
        event(story,'delivery.artifact',{'role':role,'backend':'deterministic','run_id':str(record['id'])},f"{record['id']}:artifact:{role}")
    doc=knowledge.ensure_page(story['id'],f"r{story['revision']}:execution",'Расчёт, QA и отчёт',
        '# Результат\n\n'+json.dumps(metadata,ensure_ascii=False,indent=2)+'\n\n## QA\n'+files['qa.json']+
        '\n\n## Архитектурное ревью\n'+files['review.json']+'\n\n## Финансовые показатели\n'+files['financial.json']+
        '\n\nАртефакты доступны в защищённом UI. Приёмка требует CI, независимого review и слияния PR; готовый отчёт сам по себе не закрывает задачу.')
    readiness=broker('/ready')
    if not readiness['configured'] or not readiness.get('base_ready',True):
        update(record['id'],status='WAITING_GITHUB',git=readiness|{'documentation_url':doc})
        event(story,'execution.waiting_github',{},f"{record['id']}:waiting-github");return
    role_files={'pm':['acceptance.md'],'system_analyst':['architecture.md','review.json'],
                'financial_analyst':['financial.json','report.xlsx'],'data_developer':['query.sql'],
                'application_developer':['dashboard.py'],'designer':['style.md'],'qa':['qa.json']}
    tasks=[]
    for epic in story['plan']['epics']:
        epic_ref=db.external(story['id'],f"r{story['revision']}:epic:{epic['key']}")
        if not epic_ref:raise db.Conflict('Missing tracker epic')
        for task in epic['tasks']:
            task_ref=db.external(story['id'],f"r{story['revision']}:task:{task['key']}")
            if not task_ref:raise db.Conflict('Missing tracker task')
            tasks.append({'task_id':int(task_ref['external_id']),'epic_id':int(epic_ref['external_id']),'role':task['role'],
                          'files':{n:base64.b64encode((root/n).read_bytes()).decode() for n in role_files[task['role']]}})
    tracker.honor_remote_stop(story['id'])
    result=publish_tasks(story,record,doc,tasks)
    # Persist actual PR identities before tracker writes, allowing safe retry after an outage.
    update(record['id'],status='PUBLISHED',git=result)
    for pull in result['pulls']:
        tracker.honor_remote_stop(story['id'])
        item=tracker.request('/api/v3/work_packages/'+str(pull['task_id']))
        addition='\n\nPR: '+pull['pull_request_url']+'\nCommit после QA: '+pull['commit']+'\nРезультат и QA: '+doc
        if pull['pull_request_url'] not in item['description']['raw']:
            tracker.request('/api/v3/work_packages/'+str(pull['task_id']),'PATCH',{'lockVersion':item['lockVersion'],
                'description':{'format':'markdown','raw':item['description']['raw']+addition}})
    page_ref=db.external(story['id'],f"doc:r{story['revision']}:execution")
    page=knowledge.request('/pages/'+str(page_ref['external_id']))
    publication_marker=f"publication:{record['id']}"
    if publication_marker not in page['markdown']:
        tracker.honor_remote_stop(story['id'])
        knowledge.request('/pages/'+str(page_ref['external_id']),'PUT',{'name':page['name'],
            'markdown':page['markdown']+'\n\n## GitHub PR\n\n'+publication_marker+'\n\n'+json.dumps(result,ensure_ascii=False,indent=2)})
    sync_status(story['id'],'ACCEPTANCE')
    update(record['id'],status='ACCEPTANCE')
    event(story,'execution.published',{'pulls':result['pulls'],'documentation_url':doc},f"{record['id']}:published")
