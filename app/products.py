"""Durable independent-product lane. No generated code executes in this process."""
import base64
import json
import uuid
from psycopg.types.json import Jsonb
from app import db
from app.product_contract import Contribution, ProductSolution, ProductReview, build_package, inspect_package
from app.providers import generate
from app.storage import store, read_artifact
from app.integrations import tracker, knowledge, sync_status

FIELDS={'status','specification','package','qa','review','git','documentation_url','deployment','accepted'}

def get(story_id,revision=None):
    with db.connection() as conn:
        if revision is None:revision=db.get_story(story_id,conn)['revision']
        return conn.execute('SELECT * FROM product_releases WHERE story_id=%s AND revision=%s',(story_id,revision)).fetchone()


def update(id,**values):
    from psycopg import sql
    if not values or not set(values)<=FIELDS:raise ValueError('Unknown product field')
    with db.connection() as conn:
        conn.execute(sql.SQL('UPDATE product_releases SET {},updated_at=now() WHERE id=%s').format(
            sql.SQL(',').join(sql.SQL('{}=%s').format(sql.Identifier(k)) for k in values)),
            tuple(Jsonb(v) if isinstance(v,(dict,list)) else v for v in values.values())+(id,))


def package_bytes(record):
    if not record.get('package'):raise db.Conflict('Package is not ready')
    return read_artifact(record['id'],'product.zip',record['package'])


def run(story_id):
    story=db.active(story_id);tracker.honor_remote_stop(story_id)
    workspace_mode=(story.get('plan') or {}).get('development_mode')=='workspace'
    if not workspace_mode and len(story['plan']['epics'])!=1:raise db.Conflict('Legacy product profile requires one epic per version')
    if story['delivery_mode']!='product' or not story['approved']:raise db.Conflict('Product plan must be approved')
    from app.feasibility import require_ready
    require_ready(story)
    with db.connection() as conn:
        approved=conn.execute('SELECT 1 FROM approvals WHERE story_id=%s AND revision=%s AND plan_digest=%s',
            (story_id,story['revision'],story['plan_digest'])).fetchone()
        if not approved:raise db.Conflict('Plan digest not approved')
        conn.execute('''INSERT INTO product_releases(id,story_id,revision,product_key,version)
            VALUES(%s,%s,%s,%s,%s) ON CONFLICT(story_id,revision) DO NOTHING''',
            (uuid.uuid4(),story_id,story['revision'],'p-'+uuid.UUID(str(story_id)).hex[:12],'1.0.'+str(story['revision'])))
    record=get(story_id)
    if story['status']=='PLAN_REVIEW':sync_status(story_id,'IN_PROGRESS')
    if workspace_mode and record['status'] in ['DEVELOPING','DEVELOPING_TASKS','WAITING_GITHUB']:
        from app import workspaces
        workspace=workspaces.advance(story)
        if not workspace:
            if record['status']=='DEVELOPING':update(record['id'],status='DEVELOPING_TASKS')
            return
        solution=ProductSolution(**workspace['definition'],files=workspace['files'])
        contributions={}
        for row in workspaces.tasks(workspace['id']):
            previous=contributions.setdefault(row['role'],{'specification':'','files':{}})
            previous['specification']+='\n'+(row['summary'] or '')
        content,metadata=build_package(solution,record['product_key'],record['version'],story_id,story['revision'],contributions)
        manifest=store().put(record['id'],'product.zip',content)
        page=knowledge.ensure_page(story_id,f"r{story['revision']}:product",solution.title,
            '# Продукт после выполнения задач\n\n'+solution.architecture+'\n\n'+solution.operating_notes+
            '\n\nПроверенные задачи и релизы:\n```json\n'+json.dumps(workspace['git'],ensure_ascii=False,indent=2)+'\n```')
        update(record['id'],status='CHECK_PENDING',specification=solution.model_dump()|{'contributions':contributions},
            package=manifest,documentation_url=page,git=workspace['git'])
        return
    if record['status']=='DEVELOPING':
        context={'story':story['text'],'answers':story['answers'],'plan':story['plan'],
            'delivery_mode':'product','product_key':record['product_key'],
            'policy':'Only public/synthetic input. Never include credentials. Independent Python 3.12 product; no team API/DB dependency. Shared product infrastructure permitted. Do not claim execution.'}
        contributions={}
        for role,instruction in [
            ('financial_analyst','Опиши user stories, экономические проверки результата, единицы и граничные случаи. Если нет финансовых расчётов, отметь это. files оставь пустым.'),
            ('designer','Опиши правила представления результата, единицы и подписи. files оставь пустым.'),
            ('data_developer','Спроектируй продуктовые данные, миграции и тестовые данные при необходимости. Не загружай внешние данные до отдельной задачи. Файлы только внутри data/ или migrations/ (.py/.sql/.json/.md/.csv); не более двух небольших файлов. БД через PRODUCT_DATABASE_URL, приложение получает DATABASE_URL; миграции должны быть идемпотентны.')]:
            contribution=generate(story_id,role,Contribution,context|{'instruction':instruction},result_role='product_'+role)
            if role!='data_developer' and contribution.files:raise db.Conflict('This role contributes specifications only')
            if role=='data_developer' and any(not name.startswith(('data/','migrations/')) for name in contribution.files):
                raise db.Conflict('Data developer sources must stay in data/ or migrations/')
            contributions[role]=contribution.model_dump()
        solution=generate(story_id,'application_developer',ProductSolution,context|{'contributions':contributions,
            'instruction':'Создай полноценные исходники самостоятельного продукта и независимые unittest-тесты. Включи app.py, tests/test_product.py и нужные модули/миграции. Разрешены stdlib и библиотеки из доверенного lock: httpx, psycopg, pandas, fastapi, streamlit. Предпочти компактный код stdlib. service: app.py запускает HTTP на 0.0.0.0:8000, GET /health и GET /result возвращают JSON или результат; job: app.py завершается с кодом 0 и пишет /data/result.json. Не запускай сервер при импорте. Результаты файлов в /data, БД только DATABASE_URL, Unit-тесты работают без сети; при needs_database=true smoke получает пустую изолированную PostgreSQL через DATABASE_URL, приложение идемпотентно инициализирует схемы. Внешняя сеть закрыта. Никогда не читай окружение/файлы команды. Dockerfile/Compose/install.md будут сгенерированы платформой; не включай их в files. В operating_notes опиши реальные миграции, получение результата и backup. До 6 файлов, код до 10000 символов.'},result_role='product_solution')
        # The application developer integrates data migrations into the final complete file set.
        for contribution in contributions.values():
            for name,content in contribution['files'].items():
                if name not in solution.files:solution.files[name]=content
        solution=ProductSolution.model_validate(solution.model_dump())
        content,metadata=build_package(solution,record['product_key'],record['version'],story_id,story['revision'],contributions)
        inspect_package(content)
        manifest=store().put(record['id'],'product.zip',content)
        page=knowledge.ensure_page(story_id,f"r{story['revision']}:product",solution.title,
            '# Самостоятельный продукт\n\n'+solution.architecture+'\n\n## Эксплуатация\n'+solution.operating_notes+
            '\n\n## Паспорт поставки\n```json\n'+json.dumps(metadata,ensure_ascii=False,indent=2)+'\n```')
        update(record['id'],status='CHECK_PENDING',specification=solution.model_dump()|{'contributions':contributions},package=manifest,documentation_url=page)
        with db.connection() as conn:db.event(conn,story,'product.packaged',{'product_key':record['product_key'],'sha256':manifest['sha256']},f"{record['id']}:packaged")
        return
    if record['status']=='CHECKED':
        # Review code once; repeated full planning/design/data documents can exhaust
        # conservative input reservations even when actual usage is well below budget.
        source={k:v for k,v in record['specification'].items() if k!='contributions'}
        business=record['specification'].get('contributions',{}).get('financial_analyst',{}).get('specification','')
        qa=generate(story_id,'qa',ProductReview,{'story':story['text'],'answers':story['answers'],'plan':story['plan'],'package_manifest':record['package'],'platform_files':['Dockerfile','docker-compose.yml','install.md','runtime-requirements.lock'],'solution':source,'business_rules':business,'checks':record['qa'],
            'instruction':'Независимо оцени соответствие исходников критериям, адекватность тестов, экономические и граничные случаи. Реальный оператор выполнил тесты; не считай smoke достаточным для бизнес-приёмки. approved=false при ошибках или неподтверждённых требованиях.'},result_role='product_qa')
        review=generate(story_id,'system_analyst',ProductReview,{'story':story['text'],'answers':story['answers'],'plan':story['plan'],'platform_files':['Dockerfile','docker-compose.yml','install.md','runtime-requirements.lock'],'solution':source,'checks':record['qa'],'qa':qa.model_dump(),
            'instruction':'Проверь архитектуру, безопасность, самостоятельность, миграции и исходники. Не одобряй при серьёзных замечаниях; GitHub review остаётся отдельным.'},result_role='product_review')
        update(record['id'],review={'qa':qa.model_dump(),'architecture':review.model_dump()},status='READY_TO_PUBLISH' if qa.approved and review.approved else 'ESCALATED')
        if not qa.approved or not review.approved:sync_status(story_id,'BLOCKED')
        record=get(story_id)
    if record['status'] in {'READY_TO_PUBLISH','WAITING_GITHUB','PUBLISHED'}:
        publish(story,record)
    if record['status']=='ACCEPTANCE' and record['accepted']:
        from app.delivery import broker
        final_epic=db.external(story_id,f"r{story['revision']}:epic:{story['plan']['epics'][-1]['key']}")['external_id']
        final_pulls=[p for p in record['git']['pulls'] if p['epic_id']==final_epic]
        release=broker('/release',{'pulls':final_pulls,'epic_id':final_epic})
        update(record['id'],status='RELEASE_REVIEW',git=record['git']|{'release':release})
    if record['status']=='RELEASE_REVIEW':
        from app.delivery import broker
        release=record['git']['release']
        gate=broker('/gate',{'pulls':[{'number':release['number'],'commit':release['commit'],'product_package':True,'workspace_task':bool(release.get('workspace_task'))}]})
        if gate['eligible']:
            tagged=broker('/finalize-release',{'epic_id':release['epic_id'],'number':release['number'],
                'commit':release['commit'],'version':record['version'],'product_package':True,'workspace_task':bool(release.get('workspace_task'))})
            if tagged.get('pending'):return
            update(record['id'],git=record['git']|{'tag':tagged})
            from app.config import settings
            ids={p['task_id'] for p in record['git']['pulls']}|{p['epic_id'] for p in record['git']['pulls']}
            goal=db.external(story_id,f"r{story['revision']}:goal")
            if goal:ids.add(goal['external_id'])
            for id in sorted(ids):
                tracker.honor_remote_stop(story_id)
                item=tracker.request('/api/v3/work_packages/'+str(id))
                tracker.request('/api/v3/work_packages/'+str(id),'PATCH',{'lockVersion':item['lockVersion'],
                    '_links':{'status':{'href':'/api/v3/statuses/'+str(settings.status_ids['DONE'])}}})
            knowledge.ensure_page(story_id,f"r{story['revision']}:product-acceptance",'Приёмка и выпуск продукта',
                '# Продукт принят и выпущен\n\n'+record['documentation_url']+'\n\n'+json.dumps(tagged,ensure_ascii=False,indent=2))
            sync_status(story_id,'DONE')
            update(record['id'],status='DONE')



def publish(story,record):
    from app.delivery import broker
    if record['status']!='PUBLISHED':
        readiness=broker('/ready')
        workspace_mode=(story.get('plan') or {}).get('development_mode')=='workspace'
        if not readiness.get('configured') or not readiness.get('workspace_base_ready' if workspace_mode else 'product_base_ready'):
            update(record['id'],status='WAITING_GITHUB');return
        metadata,files=inspect_package(package_bytes(record))
        tasks=[]
        workspace_mode=(story.get('plan') or {}).get('development_mode')=='workspace'
        # Each planned task receives its own immutable contribution and PR to its epic release.
        for epic in story['plan']['epics']:
            epic_id=db.external(story['id'],f"r{story['revision']}:epic:{epic['key']}")['external_id']
            for task in epic['tasks']:
                if workspace_mode and task.get('kind')!='package':continue
                task_id=db.external(story['id'],f"r{story['revision']}:task:{task['key']}")['external_id']
                prefix=f"products/{record['product_key']}/"
                if task['role']=='application_developer':
                    selected={prefix+k:v for k,v in files.items()}
                else:
                    # Distinct task documentation retains per-task traceability without overwriting the package.
                    report={'task':task,'product':metadata['product_key'],'package_sha256':record['package']['sha256'],
                        'contribution':record['specification'].get('contributions',{}).get(task['role']),
                        'checks':record['qa'],'reviews':record['review']}
                    selected={prefix+f"task-{task_id}.json":json.dumps(report,ensure_ascii=False,indent=2).encode()}
                    if task['role']=='data_developer':
                        for name in (record['specification'].get('contributions',{}).get('data_developer') or {}).get('files',{}):
                            if not name.startswith(('data/','migrations/')):raise db.Conflict('Invalid data contribution path')
                            selected[prefix+name]=files[name]
                result_page=knowledge.ensure_page(story['id'],f"r{story['revision']}:product-result:{task['key']}",
                    'Результат: '+task['title'], '# Результат задачи\n\nПакет: '+record['package']['sha256']+'\n\n'+record['documentation_url']+
                    '\n\n```json\n'+json.dumps({'contribution':record['specification'].get('contributions',{}).get(task['role']),
                        'qa':record['qa'],'reviews':record['review']},ensure_ascii=False,indent=2)+'\n```')
                item=tracker.request('/api/v3/work_packages/'+str(task_id))
                if result_page not in item['description']['raw']:
                    tracker.request('/api/v3/work_packages/'+str(task_id),'PATCH',{'lockVersion':item['lockVersion'],
                        'description':{'format':'markdown','raw':item['description']['raw']+'\n\nРезультат: '+result_page}})
                tasks.append({'task_id':task_id,'epic_id':epic_id,'role':task['role'],
                    'files':{k:base64.b64encode(v).decode() for k,v in selected.items()}})
        if not any(t['role']=='application_developer' for t in tasks):raise db.Conflict('Plan requires an application developer task')
        git=broker('/publish',{'run_id':str(record['id']),'story_id':str(story['id']),'revision':story['revision'],
            'documentation_url':record['documentation_url'],'tasks':tasks,'product_package':True,
            'workspace_delivery':workspace_mode,'previous_release':record['git'].get('previous_release') if workspace_mode else None})
        if workspace_mode:git=record['git']|git|{'pulls':record['git']['pulls']+git['pulls']}
        update(record['id'],status='PUBLISHED',git=git)
        record=get(story['id'])
    for pull in record['git']['pulls']:
        tracker.honor_remote_stop(story['id'])
        item=tracker.request('/api/v3/work_packages/'+str(pull['task_id']))
        if pull['pull_request_url'] not in item['description']['raw']:
            tracker.request('/api/v3/work_packages/'+str(pull['task_id']),'PATCH',{'lockVersion':item['lockVersion'],
                'description':{'format':'markdown','raw':item['description']['raw']+'\n\nPR: '+pull['pull_request_url']+'\nПроверенный commit: '+pull['commit']}})
    gate_pulls=record['git']['pulls']
    if (story.get('plan') or {}).get('development_mode')=='workspace':
        final_epic=db.external(story['id'],f"r{story['revision']}:epic:{story['plan']['epics'][-1]['key']}")['external_id']
        gate_pulls=[p for p in gate_pulls if p['epic_id']==final_epic]
    gate=broker('/gate',{'pulls':gate_pulls})
    if gate['eligible']:
        update(record['id'],status='DEPLOY_READY')


def public(record):
    if not record:return None
    return {k:record[k] for k in ['id','story_id','product_key','version','status','package','qa','review','git','documentation_url','deployment','accepted','created_at','updated_at']}
