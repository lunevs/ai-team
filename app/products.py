"""Durable independent-product lane. No generated code executes in this process."""
import base64
import json
import uuid
from psycopg.types.json import Jsonb
from app import db
from app.product_contract import Contribution, ProductSolution, ProductReview, AcceptancePlan, build_package, inspect_package
from app.providers import generate
from app.storage import store, read_artifact
from app.integrations import tracker, knowledge, sync_status

FIELDS={'status','specification','package','qa','review','git','documentation_url','deployment','accepted','verification'}

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
    return read_artifact(record['package'].get('storage_id',record['id']),'product.zip',record['package'])


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
        conn.execute('''INSERT INTO product_releases(id,story_id,revision,product_key,version,assurance_version)
            VALUES(%s,%s,%s,%s,%s,1) ON CONFLICT(story_id,revision) DO NOTHING''',
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
        manifest=store().put(uuid.uuid5(uuid.UUID(str(record['id'])),__import__('hashlib').sha256(content).hexdigest()),'product.zip',content)
        manifest['storage_id']=str(uuid.uuid5(uuid.UUID(str(record['id'])),manifest['sha256']))
        page=knowledge.ensure_page(story_id,f"r{story['revision']}:product",solution.title,
            '# Продукт после выполнения задач\n\n'+solution.architecture+'\n\n'+solution.operating_notes+
            '\n\nПроверенные задачи и релизы:\n```json\n'+json.dumps(workspace['git'],ensure_ascii=False,indent=2)+'\n```')
        update(record['id'],status='VERIFICATION_PLANNING',specification=solution.model_dump()|{'contributions':contributions},
            package=manifest,documentation_url=page,git=workspace['git'])
        from app.workspaces import schedule
        schedule(story_id)
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
            'instruction':'Создай полноценные исходники самостоятельного продукта и независимые unittest-тесты. Включи app.py, tests/test_product.py и нужные модули/миграции. Разрешены stdlib и библиотеки из доверенного lock: httpx, psycopg, pandas, fastapi, streamlit. Предпочти компактный код stdlib. service: app.py запускает HTTP на 0.0.0.0:8000, GET /health и GET /result возвращают JSON или результат; job: app.py завершается с кодом 0 и пишет /data/result.json. Не запускай сервер при импорте. Для dashboard /result возвращает charts: [{series: [{values: [числа]}]}] с фактическими данными, UI на /. Результаты файлов в /data, БД только DATABASE_URL, Unit-тесты работают без сети; при needs_database=true smoke получает пустую изолированную PostgreSQL через DATABASE_URL, приложение идемпотентно инициализирует схемы. Внешняя сеть закрыта. Никогда не читай окружение/файлы команды. Dockerfile/Compose/install.md будут сгенерированы платформой; не включай их в files. В operating_notes опиши реальные миграции, получение результата и backup. До 6 файлов, код до 10000 символов.'},result_role='product_solution')
        # The application developer integrates data migrations into the final complete file set.
        for contribution in contributions.values():
            for name,content in contribution['files'].items():
                if name not in solution.files:solution.files[name]=content
        solution=ProductSolution.model_validate(solution.model_dump())
        content,metadata=build_package(solution,record['product_key'],record['version'],story_id,story['revision'],contributions)
        inspect_package(content)
        manifest=store().put(uuid.uuid5(uuid.UUID(str(record['id'])),__import__('hashlib').sha256(content).hexdigest()),'product.zip',content)
        manifest['storage_id']=str(uuid.uuid5(uuid.UUID(str(record['id'])),manifest['sha256']))
        page=knowledge.ensure_page(story_id,f"r{story['revision']}:product",solution.title,
            '# Самостоятельный продукт\n\n'+solution.architecture+'\n\n## Эксплуатация\n'+solution.operating_notes+
            '\n\n## Паспорт поставки\n```json\n'+json.dumps(metadata,ensure_ascii=False,indent=2)+'\n```')
        update(record['id'],status='VERIFICATION_PLANNING',specification=solution.model_dump()|{'contributions':contributions},package=manifest,documentation_url=page)
        with db.connection() as conn:db.event(conn,story,'product.packaged',{'product_key':record['product_key'],'sha256':manifest['sha256']},f"{record['id']}:packaged")
        from app.workspaces import schedule
        schedule(story_id)
        return
    if record['status']=='VERIFICATION_PLANNING':
        from app.product_assurance import make_contract
        context={'story':story['text'],'answers':story['answers'],'criteria':story['pm']['acceptance_criteria'],
                 'architecture':record['specification']['architecture'],'output_type':record['specification']['output_type'],
                 'sources':record['specification'].get('sources',[]),
                 'instruction': 'До просмотра реализации задай независимые исполнимые проверки. Каждый criterion — индекс критерия с нуля. QA обязан покрыть все критерии. Доступны только JSON /result, файлы JSON/CSV/XLSX/PPTX в /data; pointer — JSON Pointer; XLSX /sheet1/A1; CSV массив строк; PPTX /slides массив текстов слайдов. Формулы Excel без независимого пересчёта не поддерживаются. number сравнивает Decimal с tolerance, equals строгое равенство, min_count требует положительное число строк, nonempty не заменяет проверку расчёта. basis объясняет независимый источник ожидаемого значения: требования, заранее рассчитанный пример, утверждённый контроль. Не выдумывай ожидаемые числа для неизвестных реальных данных, не подменяй критерий запуском или наличием поля. Для невыполнимых этим протоколом критериев или неизвестных независимых ожидаемых значений укажи индексы в unsupported_criteria: они обязательно блокируют приёмку. Не подменяй их упрощённой проверкой. Проверенные и unsupported критерии QA вместе покрывают все критерии. Для финансовых расчётов нужны числовые контроли, граничные случаи, единицы. Если их нет, финансовый аналитик объясняет not_applicable_reason и проверяет реальный пользовательский результат. QA not_applicable_reason оставляет пустым.'}
        suffix=record['package']['sha256'][:16]+'_'+str(record['review_round'])
        qa=generate(story_id,'qa',AcceptancePlan,context,result_role='acceptance_plan_qa_'+suffix)
        financial=generate(story_id,'financial_analyst',AcceptancePlan,context,result_role='acceptance_plan_financial_'+suffix)
        contract=make_contract(record,story,qa.model_dump(),financial.model_dump())
        knowledge.ensure_page(story_id,f"r{story['revision']}:verification:{suffix}",'Независимые проверки результата',
            '# Проверки пользовательских требований\n\n'+json.dumps(contract,ensure_ascii=False,indent=2))
        update(record['id'],verification=contract,status='CHECK_PENDING')
        return
    if workspace_mode and record['status']=='REPAIR_PENDING':
        from app.rework import product_failure
        finding={'summary':'Итоговый пакет не прошёл проверку','criterion':'; '.join(story['pm']['acceptance_criteria'])[:2000],
            'evidence':json.dumps(record.get('deployment') if (record.get('deployment') or {}).get('error_type')=='EvidenceFailed' else record['qa'],ensure_ascii=False)[-6000:],'severity':'major','verification':'Повторить тесты и независимые ревью полного пакета'}
        product_failure(story,record,[finding],{'verification':record.get('verification'),'checks':record['qa']},actual_failure=True);return
    if record['status']=='CHECKED':
        # Review code once; repeated full planning/design/data documents can exhaust
        # conservative input reservations even when actual usage is well below budget.
        source={k:v for k,v in record['specification'].items() if k!='contributions'}
        _,full_package=inspect_package(package_bytes(record))
        source=source|{'full_package':{name:content.decode() for name,content in full_package.items()}}
        business=record['specification'].get('contributions',{}).get('financial_analyst',{}).get('specification','')
        qa=generate(story_id,'qa',ProductReview,{'story':story['text'],'answers':story['answers'],'plan':story['plan'],'package_manifest':record['package'],'platform_files':['Dockerfile','docker-compose.yml','install.md','runtime-requirements.lock'],'solution':source,'business_rules':business,'verification':record.get('verification'),'checks':record['qa'],
            'instruction':'Независимо оцени соответствие исходников критериям, адекватность тестов, экономические и граничные случаи. В solution.full_package даны ВСЕ файлы финального пакета, включая Dockerfile и install.md: проверяй их содержимое, не заявляй отсутствие по списку авторских files. Сверь КАЖДЫЙ критерий с verification и фактическими evidence. Подмена критерия числом 42/nonempty, неподтверждённая интеграция, неизвестное происхождение данных или неполная проверка означают approved=false. Реальный оператор выполнил тесты; не считай smoke достаточным для бизнес-приёмки. approved=false при ошибках или неподтверждённых требованиях.'},result_role='product_qa_'+str(record['review_round'])+'_'+record['package']['sha256'][:16]+'_'+(record.get('verification') or {}).get('digest','legacy')[:16])
        review=generate(story_id,'system_analyst',ProductReview,{'story':story['text'],'answers':story['answers'],'plan':story['plan'],'platform_files':['Dockerfile','docker-compose.yml','install.md','runtime-requirements.lock'],'solution':source,'verification':record.get('verification'),'checks':record['qa'],'qa':qa.model_dump(),
            'instruction':'Проверь архитектуру, безопасность, самостоятельность, зависимости, миграции и конфигурацию ВСЕГО solution.full_package. Dockerfile и install.md добавляет платформа, они представлены полностью. Не одобряй при серьёзных замечаниях; GitHub review остаётся отдельным.'},result_role='product_review_'+str(record['review_round'])+'_'+record['package']['sha256'][:16]+'_'+(record.get('verification') or {}).get('digest','legacy')[:16])
        financial=generate(story_id,'financial_analyst',ProductReview,{'story':story['text'],'answers':story['answers'],'solution':source,
            'criteria':story['pm']['acceptance_criteria'],'verification':record.get('verification'),'checks':record['qa'],
            'instruction':'Независимо проверь экономический смысл, единицы и итоговые цифры. Наличие значений и запуск не доказывают расчёт. Если контроль не независим, отсутствует, данные пусты или требование не проверено — approved=false и конкретные замечания. Не заявляй загрузку реальных данных по синтетическому примеру.'},
            result_role='product_financial_'+str(record['review_round'])+'_'+record['package']['sha256'][:16]+'_'+(record.get('verification') or {}).get('digest','legacy')[:16])
        approved=qa.approved and review.approved and financial.approved
        if record.get('assurance_version')==1:
            from app.product_assurance import receipt_valid
            approved=approved and receipt_valid(record['verification'],(record['qa'] or {}).get('evidence') or {},'check')
        update(record['id'],review={'qa':qa.model_dump(),'architecture':review.model_dump(),'financial':financial.model_dump(),
            'contract_digest':(record.get('verification') or {}).get('digest')},status='READY_TO_PUBLISH' if approved else 'ESCALATED')
        if not approved:
            if workspace_mode:
                from app.rework import product_failure
                findings=[{'summary':('Замечание проверяющего: '+f)[:1000],'criterion':'; '.join(story['pm']['acceptance_criteria'])[:2000],
                    'evidence':('Замечание: '+f)[:6000],'severity':'major','verification':'Повторить независимое ревью исходников и полного пакета'} for r in [qa,review,financial] for f in r.findings]
                if not findings:findings=[{'summary':'Проверяющий не подтвердил готовность','criterion':'; '.join(story['pm']['acceptance_criteria'])[:2000],'evidence':'Отрицательное ревью без пояснений','severity':'major','verification':'Повторить проверку с конкретными доказательствами'}]
                product_failure(story,record,findings,{'qa':qa.model_dump(),'architecture':review.model_dump()});return
            sync_status(story_id,'BLOCKED')
        if workspace_mode and approved:
            from app.rework import resolved
            from app.workspace_policy import source_digest
            resolved(story,'product_'+str(record['id']),{'source_digest':source_digest(source['files']),'package_sha256':record['package']['sha256'],'verification':record.get('verification'),'checks':record['qa'],'qa':qa.model_dump(),'review':review.model_dump()})
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
        allowed=readiness.get('workspace_profiles' if workspace_mode else 'product_profiles',['python-locked-v1'])
        if metadata.get('runtime_profile','python-locked-v1') not in allowed:
            update(record['id'],status='WAITING_GITHUB');return
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
                        'verification':record.get('verification'),'checks':record['qa'],'reviews':record['review']}
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
            'supersedes':{str(p['task_id']):p['commit'] for p in (record['git'] or {}).get('pulls',[]) if p.get('product_package')} if workspace_mode else {},
            'workspace_delivery':workspace_mode,'previous_release':record['git'].get('previous_release') if workspace_mode else None})
        if workspace_mode:git=record['git']|git|{'pulls':list({p['task_id']:p for p in record['git']['pulls']+git['pulls']}.values())}
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
    elif (story.get('plan') or {}).get('development_mode')=='workspace':
        feedback=broker('/feedback',{'pulls':gate_pulls})
        from app.rework_contract import feedback_key
        if feedback.get('findings') and record['git'].get('dismissed_feedback')!=feedback_key(feedback):
            from app.rework import product_failure
            product_failure(story,record,feedback['findings'],feedback,actual_failure=feedback.get('execution_failed',False))
        else:
            from app.workspaces import schedule
            schedule(story['id'],30)


def public(record):
    if not record:return None
    result={k:record[k] for k in ['id','story_id','product_key','version','status','package','qa','review','git','documentation_url','deployment','accepted','created_at','updated_at']}

    if record.get('assurance_version')==1:
        from app.product_assurance import ready
        result.update(verification=record.get('verification'),ready_for_acceptance=ready(record))
        with db.connection() as conn:
            result['outputs']=conn.execute('SELECT o.id,o.path,o.sha256 FROM product_outputs o JOIN product_evidence e ON e.operation_id=o.operation_id WHERE o.release_id=%s AND e.operation_id=%s',
                (record['id'],(record.get('deployment') or {}).get('operation_id'))).fetchall()
    return result
