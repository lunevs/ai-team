"""Versioned multi-service profile. All infrastructure files are platform-owned."""
import json
from pathlib import Path
from app.source_policy import sources_policy

PROFILE='airflow-postgres-v1'
AIRFLOW_IMAGE='apache/airflow:3.2.2-python3.12@sha256:bbe58e3204d550ab98dbf738a42c0e6663c455357ecd0e2d1440ef9cb6a75f00'
POSTGRES_IMAGE='postgres:17.6-alpine@sha256:ef257d85f76e48da1c64832459b59fcaba1a4dac97bf5d7450c77753542eee94'
OWNED={'Airflow.Dockerfile','airflow-requirements.lock','source_gateway.py','sources.json','database_init.py','database-init.sql','platform_migrate.py','source_client.py','data/profile_marker.md','airflow_entrypoint.py'}

def app_lock():
    return Path(__file__).with_name('product_templates').joinpath('runtime-requirements.lock').read_text()+'\nopenpyxl==3.1.5\net_xmlfile==2.0.0\nlxml==6.1.1\n'

def airflow_dockerfile():
    return f'''FROM {AIRFLOW_IMAGE}
COPY airflow-requirements.lock /tmp/airflow-requirements.lock
RUN pip install --no-cache-dir --no-deps -r /tmp/airflow-requirements.lock
COPY --chown=50000:0 dags /opt/airflow/dags
COPY --chown=50000:0 data /opt/airflow/dags/data
COPY --chown=50000:0 source_client.py /opt/airflow/dags/source_client.py
COPY --chown=50000:0 airflow_entrypoint.py /opt/airflow/platform_entrypoint.py
ENV PYTHONDONTWRITEBYTECODE=1
ENTRYPOINT ["python", "/opt/airflow/platform_entrypoint.py"]
'''


def compose(key,version,sources):
    from app.product_policy import BASE_IMAGE,proxy_code
    sources_policy(sources)
    common={'read_only':True,'cap_drop':['ALL'],'security_opt':['no-new-privileges:true'],
            'pids_limit':128,'cpus':1,'mem_limit':'512m','networks':['private'],
            'tmpfs':['/tmp:size=96m,mode=1777'],'logging':{'driver':'json-file','options':{'max-size':'5m','max-file':'2'}},
            'labels':{'ai-team.product-managed':'true'},'restart':'unless-stopped'}
    app=common|{'build':'.','image':f'ai-product-{key}:{version}','user':'10001:10001',
        'environment':{'DATABASE_URL':'postgresql://product:${PRODUCT_DB_PASSWORD:?required}@postgres/product','OUTPUT_DIR':'/data','SOURCE_GATEWAY_URL':'http://source-gateway:8080'},
        'volumes':['product-data:/data'],'depends_on':{'postgres':{'condition':'service_healthy'}},
        'healthcheck':{'test':['CMD','python','-c',"import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=2)"],'interval':'5s','timeout':'3s','retries':20}}
    afenv={'AIRFLOW_CONFIG':'/tmp/airflow.cfg','AIRFLOW__CORE__EXECUTOR':'LocalExecutor','AIRFLOW__CORE__PARALLELISM':'1',
        'AIRFLOW__CORE__LOAD_EXAMPLES':'false','AIRFLOW__CORE__DAGS_ARE_PAUSED_AT_CREATION':'false',
        'AIRFLOW__CORE__EXECUTION_API_SERVER_URL':'http://airflow-api:8080/execution/',
        'AIRFLOW__DATABASE__SQL_ALCHEMY_CONN':'postgresql+psycopg2://airflow:${AIRFLOW_DB_PASSWORD:?required}@postgres/airflow',
        'AIRFLOW__CORE__FERNET_KEY':'${AIRFLOW_FERNET_KEY:?required}',
        'AIRFLOW__API_AUTH__JWT_SECRET':'${AIRFLOW_JWT_SECRET:?required}',
        'AIRFLOW__API__SECRET_KEY':'${AIRFLOW_JWT_SECRET:?required}',
        'AIRFLOW__CORE__AUTH_MANAGER':'airflow.api_fastapi.auth.managers.simple.simple_auth_manager.SimpleAuthManager',
        'AIRFLOW__CORE__SIMPLE_AUTH_MANAGER_USERS':'admin:admin',
        'AIRFLOW_ADMIN_PASSWORD':'${AIRFLOW_ADMIN_PASSWORD:?required}',
        'AIRFLOW__CORE__SIMPLE_AUTH_MANAGER_PASSWORDS_FILE':'/tmp/airflow-passwords.json',
        'AIRFLOW__DAG_PROCESSOR__PARSING_PROCESSES':'1',
        'AIRFLOW__API__WORKERS':'1',
        'PYTHONPATH':'/opt/airflow/dags',
        'DATABASE_URL':'postgresql://product:${PRODUCT_DB_PASSWORD:?required}@postgres/product',
        'SOURCE_GATEWAY_URL':'http://source-gateway:8080','OUTPUT_DIR':'/data'}
    af=common|{'build':{'context':'.','dockerfile':'Airflow.Dockerfile'},'image':f'ai-product-airflow-{key}:{version}',
        'user':'50000:0','environment':afenv,'volumes':['airflow-logs:/opt/airflow/logs','product-data:/data'],
        'depends_on':{'postgres':{'condition':'service_healthy'}}}
    postgres=common|{'image':POSTGRES_IMAGE,'user':'70:70','read_only':False,'mem_limit':'256m','pids_limit':64,
        'environment':{'POSTGRES_PASSWORD':'${POSTGRES_ADMIN_PASSWORD:?required}','PRODUCT_DB_PASSWORD':'${PRODUCT_DB_PASSWORD:?required}','AIRFLOW_DB_PASSWORD':'${AIRFLOW_DB_PASSWORD:?required}'},
        'volumes':['postgres-data:/var/lib/postgresql/data','./database-init.sql:/docker-entrypoint-initdb.d/10-products.sql:ro'],
        'healthcheck':{'test':['CMD-SHELL','pg_isready -h 127.0.0.1 -U postgres -d product'],'interval':'2s','timeout':'3s','retries':40}}
    # No Docker socket, host paths, host network or team networks. Only trusted gateway has egress.
    gateway=common|{'image':BASE_IMAGE,'user':'10001:10001','mem_limit':'64m','cpus':0.25,
        'command':['python','/policy/source_gateway.py'],'volumes':['./source_gateway.py:/policy/source_gateway.py:ro','./sources.json:/policy/sources.json:ro'],
        'networks':['private','egress']}
    access=common|{'image':BASE_IMAGE,'user':'10001:10001','mem_limit':'64m','cpus':0.25,
        'command':['python','-c',proxy_code('app')],'networks':['private','access'],
        'ports':['127.0.0.1:${PRODUCT_PORT:-8600}:8000'],'depends_on':{'app':{'condition':'service_healthy'}}}
    return {'name':'product-'+key,'services':{'postgres':postgres,'app':app,'source-gateway':gateway,'gateway':access,
        'airflow-api':af|{'command':['api-server'],'mem_limit':'768m'},
        'airflow-scheduler':af|{'command':['scheduler'],'mem_limit':'1024m'},
        'airflow-dag-processor':af|{'command':['dag-processor']}},
        'networks':{'private':{'internal':True},'egress':{},'access':{}},
        'volumes':{v:{'labels':{'ai-team.product-managed':'true'}} for v in ['postgres-data','product-data','airflow-logs']}}


def owned_files(sources):
    return {'airflow_entrypoint.py':'''import json,os,sys
from pathlib import Path
p=Path(os.environ['AIRFLOW__CORE__SIMPLE_AUTH_MANAGER_PASSWORDS_FILE'])
p.write_text(json.dumps({'admin':os.environ['AIRFLOW_ADMIN_PASSWORD']}));p.chmod(0o600)
os.execv('/entrypoint',['/entrypoint',*sys.argv[1:]])
''','data/profile_marker.md':'Business modules owned by the product.\n','source_client.py':Path(__file__).with_name('product_templates').joinpath('source_client.py').read_text(),
        'Airflow.Dockerfile':airflow_dockerfile(),
        'airflow-requirements.lock':'openpyxl==3.1.5\net_xmlfile==2.0.0\n',
        'source_gateway.py':Path(__file__).with_name('source_policy.py').read_text(),
        'sources.json':json.dumps(sources_policy(sources),ensure_ascii=False,indent=2)+'\n',
        'platform_migrate.py':Path(__file__).with_name('product_templates').joinpath('migrate.py').read_text(),
        'database-init.sql':r'''\getenv product_password PRODUCT_DB_PASSWORD
\getenv airflow_password AIRFLOW_DB_PASSWORD
CREATE ROLE product LOGIN PASSWORD :'product_password' NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
CREATE ROLE airflow LOGIN PASSWORD :'airflow_password' NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
CREATE DATABASE product OWNER product;
CREATE DATABASE airflow OWNER airflow;
REVOKE CONNECT ON DATABASE postgres, template1, product, airflow FROM PUBLIC;
GRANT CONNECT ON DATABASE product TO product;
GRANT CONNECT ON DATABASE airflow TO airflow;
'''}


def install(key,version,solution):
    return f'''# {solution.title} — {version}

Самостоятельный профиль {PROFILE}. Команда AI, её API, OpenProject и BookStack не требуются.
Docker Engine/Desktop, Compose v2. ARM64/AMD64; Python 3.12, Airflow 3.2.2,
PostgreSQL 17.6, LocalExecutor parallelism=1. Бюджет контейнеров до 3.2 GiB,
на хосте необходимо не менее 4 GiB свободной памяти плюс место под образы/данные.
Это локальный профиль с одним исполнителем; отказоустойчивый кластер и отдельная BI-система не заявлены.

## Установка с нуля

В каталоге products/{key} скопируйте .env.example в .env, права 600.
Задайте PRODUCT_PORT (например 8600), три РАЗНЫХ случайных пароля PostgreSQL
(64 hex-символа каждый), AIRFLOW_JWT_SECRET и AIRFLOW_ADMIN_PASSWORD (разные 64 hex), AIRFLOW_FERNET_KEY
(base64url 32 случайных байтов). Пароли URL-safe. Секреты не добавлять в Git.
Сгенерировать hex: `python3 -c "import secrets;print(secrets.token_hex(32))"`;
Fernet: `python3 -c "import secrets,base64;print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"`.

1. `docker compose build app airflow-api` (первоначально нужен доступ Docker Hub/PyPI).
2. `docker compose up -d --wait postgres`.
3. `docker compose run --rm --no-deps --user 0 --cap-add CHOWN --entrypoint chown app 10001:0 /data`;
   `docker compose run --rm --no-deps --user 0 --cap-add CHOWN --entrypoint chown airflow-api 50000:0 /opt/airflow/logs`.
   `docker compose run --rm --no-deps --user 0 --cap-add FOWNER --entrypoint chmod app 770 /data`.
   Airflow UID 50000 входит в группу 0, приложение UID 10001 — владелец.
4. `docker compose run --rm --no-deps app python platform_migrate.py`.
5. `docker compose run --rm --no-deps airflow-api db migrate`.
6. `docker compose run --rm --no-deps app python -m unittest discover -s tests -v`.
7. `docker compose up -d`; `docker compose ps`. Веб-результат: http://127.0.0.1:PRODUCT_PORT/result;
   /health проверяет запуск, содержание результата сверяется с критериями истории.

DAG лежат в dags/, бизнес-модули в data/. Airflow: psycopg2, pandas 2.3.3,
openpyxl 3.1.5, lxml 6.1.1, stdlib; приложение: psycopg3, pandas из lock,
openpyxl/lxml. Импорт DAG: `docker compose exec airflow-scheduler airflow dags list-import-errors`.
История запусков: `docker compose exec airflow-scheduler airflow dags list-runs DAG_ID`.
Повторный запуск должен использовать UPSERT/уникальные ключи и сверку источника.
Расписание, полнота и бизнес-результат проверяются отдельно от unit-тестов.

## Источники и доступ

sources.json содержит согласованные точные HTTPS URL и форматы csv/json/xml/zip/xlsx.
GET http://source-gateway:8080/sources/ID — единственный сетевой путь к источнику.
Шлюз проверяет публичные IP перед TLS, запрещает redirects, private/loopback/link-local,
нестандартные порты, произвольные заголовки/секреты и ответы свыше 8 MB.
ZIP распаковывать только с проверкой путей, количества и суммарного размера; XML без DTD/внешних сущностей.
Новый URL требует нового плана/пакета и одобрения. Приложение и Airflow не подключены
к сети команды или другим продуктам. PostgreSQL не публикует порт; роли product/airflow
имеют доступ только к своим БД. Airflow API доступен только внутри продукта.

## Обновление, backup и восстановление

{solution.operating_notes}

SQL в migrations/*.sql применяются по имени одной транзакцией; удаление/изменение уже
применённой миграции блокируется checksum. Перед обновлением остановить ВСЕ контейнеры
старой версии (`docker compose stop`), сохранить старый пакет/.env и снимки трёх volumes
(postgres-data, product-data, airflow-logs). Физическая копия PostgreSQL корректна только
при остановленной БД и той же версии образа PostgreSQL. Скопировать volumes в отдельный
Compose project новой версии с сохранением UID/GID/прав, сохранив старые volumes.
В новой копии повторить шаги 2–7. При неуспехе остановить новую версию, запустить старую
с её прежними volumes и .env; произвольный обратный SQL не выполняется.
Ручной host-оператор команды выполняет этот порядок автоматически для данного профиля.
Для внешней установки используйте согласованный backup инструмент/снимок диска и проверьте
восстановление на копии до обновления. Онлайн backup: pg_dump отдельно product и airflow;
восстановление — в пустые БД теми же ролями до старта Airflow/приложения.
### Команды автономного backup/restore

Из каталога старого пакета, со свободным местом для второй копии данных:

```sh
OLD_PROJECT=product-{key}
NEW_PROJECT=product-{key}-next
docker compose -p "$OLD_PROJECT" stop
mkdir -p backup
for V in postgres-data product-data airflow-logs; do
  docker run --rm --network none --read-only --user 0 --cap-drop ALL --cap-add DAC_OVERRIDE --entrypoint sh --mount "type=volume,source=${{OLD_PROJECT}}_${{V}},target=/data,readonly" --mount "type=bind,source=$(pwd)/backup,target=/backup" {POSTGRES_IMAGE} -c "umask 077; tar -C /data -czf /backup/$V.tgz ."
done
```

Сохраните backup, старый пакет и приватный .env вне нового каталога. В новом каталоге
с новой версией скопируйте тот же .env и backup, задайте те же OLD_PROJECT/NEW_PROJECT.
Новые volumes должны отсутствовать; не распаковывайте поверх рабочих данных.

```sh
for V in postgres-data product-data airflow-logs; do
  docker volume create --label ai-team.product-managed=true "${{NEW_PROJECT}}_${{V}}"
  docker run --rm --network none --read-only --user 0 --cap-drop ALL --cap-add CHOWN --cap-add DAC_OVERRIDE --cap-add FOWNER --entrypoint sh --mount "type=volume,source=${{NEW_PROJECT}}_${{V}},target=/data" --mount "type=bind,source=$(pwd)/backup,target=/backup,readonly" {POSTGRES_IMAGE} -c "tar -C /data -xzf /backup/$V.tgz"
done
```

Повторите шаги установки 2–7 с `docker compose -p "$NEW_PROJECT"` вместо
`docker compose`. При неуспехе остановите новую версию; из СТАРОГО каталога
выполните `docker compose -p "$OLD_PROJECT" up -d`. Старые volumes и .env сохраняются.
Сравните контрольные количества/суммы до обновления и после восстановления.
Запуск host-оператором использует собственные имена project из приватного current.json;
для него предпочтителен встроенный снимок/возврат версии, а не эти имена по умолчанию.

`docker compose down` сохраняет данные; НЕ использовать `down -v` для рабочей установки.
Файлы: `docker compose cp app:/data ./results`. Общий Docker-хост для нескольких продуктов
разрешён; разные project names, сети, роли, volumes, секреты и порты обязательны.
'''
