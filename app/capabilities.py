"""Trusted product-profile capabilities, not model claims or live health checks."""
import hashlib
import json
from pathlib import Path
from app.product_policy import BASE_IMAGE

DEFINITIONS = {
    'python_service': (True, 'Небольшое веб-приложение на Python', 'Один сервис Python 3.12, HTTP /health и /result.'),
    'python_job': (True, 'Одноразовая программа на Python', 'Один запуск с результатом в /data/result.json; без расписания.'),
    'product_postgres': (True, 'Отдельная база PostgreSQL продукта', 'Отдельные БД и роль; первоначальная инициализация. Произвольный откат миграций не подтверждён.'),
    'csv_json': (True, 'Обработка CSV и JSON', 'Локальные данные в поставке; стандартная библиотека и pandas.'),
    'synthetic_data': (True, 'Синтетические данные', 'Тестовые данные без обращения к внешним источникам.'),
    'embedded_dashboard': (True, 'Простой экран с результатами', 'Экран внутри одного Python-приложения; не отдельная BI-система.'),
    'portable_package': (True, 'Независимая установка продукта', 'Доверенные Docker/Compose/install.md; ручной оператор сборки и запуска.'),
    'external_http': (True, 'Загрузка согласованных публичных источников', 'Только airflow-postgres-v1: точные HTTPS URL в согласованном плане; доверенный шлюз без redirects/private IP, до 8 MB. Произвольный интернет недоступен.'),
    'scheduled_jobs': (True, 'Автоматический запуск по расписанию', 'airflow-postgres-v1: Airflow 3.2.2 LocalExecutor parallelism=1; расписание DAG и идемпотентность требуют реализации и проверки.'),
    'airflow': (True, 'Загрузки под управлением Airflow', 'Фиксированный airflow-postgres-v1: API, scheduler, dag-processor, своя PostgreSQL и веб-приложение; минимально 4 GiB свободной RAM.'),
    'external_bi': (False, 'Отдельная BI-система', 'Подключение и поставка отдельной BI-системы не реализованы.'),
    'xlsx': (True, 'Работа с Excel в самостоятельном продукте', 'Только airflow-postgres-v1: openpyxl 3.1.5, ограниченный ZIP/XLSX parser, оформление отчёта реализуется под критерии.'),
    'pptx': (False, 'Подготовка презентаций в самостоятельном продукте', 'Профиль не содержит проверенного инструмента создания PPTX.'),
    'multiple_services': (True, 'Фиксированный состав нескольких сервисов', 'airflow-postgres-v1: PostgreSQL, Airflow API/scheduler/dag-processor, Python app, два доверенных шлюза. Произвольный Compose не допускается.'),
    'multi_epic': (True, 'Последовательная разработка нескольких эпиков', 'Эпики выполняются последовательно; следующий начинается после независимого review/CI и слияния предыдущего релиза в main.'),
    'public_source_research': (True, 'Исследование согласованного источника', 'Workspace source: фактический HTTPS fetch точного URL из одобренного плана, SHA256/размер/ограниченный preview. Это не браузер/поиск и не доказательство runtime-загрузки.'),
    'xml_zip': (True, 'Ограниченный разбор XML/ZIP', 'airflow-postgres-v1: lxml без DTD/внешних сущностей; ZIP с проверкой путей/распакованного размера, до 200 файлов/32 MB.'),
    'interactive_browsing': (False, 'Исследование сайтов инструментами агента', 'У агентов нет инструмента браузера или поиска; URL в запросе сам по себе не прочитан.'),
    'incremental_workspace': (True, 'Доработка большого проекта по задачам', 'Сохраняемая рабочая копия, чтение/поиск и изменения отдельных файлов; проверки отдельным host-оператором; ветка/PR каждой задачи.'),
    'automated_rework': (True, 'Исправление подтверждённых замечаний', 'Дефекты с доказательствами, доработка по задачам, новые проверки и ревью; повторяющийся сбой передаётся аналитику, продуктовые развилки — PM.'),
}


def registry():
    lock = Path(__file__).with_name('product_templates').joinpath('runtime-requirements.lock').read_bytes()
    payload = {
        'version': 2, 'profile': 'python-locked-v1', 'profiles':{'python-locked-v1':{'external_network':False},'airflow-postgres-v1':{'free_memory_gib':4,'source_bytes':8000000,'airflow_parallelism':1,'source_formats':['csv','json','xml','zip','xlsx']}}, 'base_image':BASE_IMAGE,
        'scope': 'Возможности доверенного профиля. Это не проверка доступности сервисов и не гарантия выполнения бизнес-требований.',
        'runtime_lock_sha256': hashlib.sha256(lock).hexdigest(),
        'limits': {'epics': 12, 'source_files': 256, 'file_characters':90000, 'read_chunk_characters':12000, 'source_total_bytes': 2_000_000,
                   'development': 'Последовательные действия над рабочей копией по задачам; один файл или операция за ответ. Два доверенных версионируемых профиля; новый источник требует согласования плана.'},
        'capabilities': {key: {'supported': supported, 'name': name, 'detail': detail}
                         for key, (supported, name, detail) in DEFINITIONS.items()},
        'implementation_sha256': {name:hashlib.sha256(Path(__file__).parent.joinpath(name).read_bytes()).hexdigest() for name in ['product_policy.py','product_contract.py','products.py','feasibility.py','workflow.py','workspaces.py','workspace_policy.py','planning.py','rework.py','rework_contract.py','product_profiles.py','source_policy.py','product_templates/migrate.py','product_templates/source_client.py','../scripts/profile_operator.py','../scripts/workspace_operator.py']},
        'evidence': ['app/product_policy.py', 'app/product_contract.py', 'app/products.py',
                     'app/product_templates/runtime-requirements.lock', 'scripts/product_operator.py','scripts/profile_operator.py','docs/stage10-product-check.json'],
    }
    payload['digest'] = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return payload
