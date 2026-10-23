"""Trusted product-profile capabilities, not model claims or live health checks."""
import hashlib
import json
from pathlib import Path

DEFINITIONS = {
    'python_service': (True, 'Небольшое веб-приложение на Python', 'Один сервис Python 3.12, HTTP /health и /result.'),
    'python_job': (True, 'Одноразовая программа на Python', 'Один запуск с результатом в /data/result.json; без расписания.'),
    'product_postgres': (True, 'Отдельная база PostgreSQL продукта', 'Отдельные БД и роль; первоначальная инициализация. Произвольный откат миграций не подтверждён.'),
    'csv_json': (True, 'Обработка CSV и JSON', 'Локальные данные в поставке; стандартная библиотека и pandas.'),
    'synthetic_data': (True, 'Синтетические данные', 'Тестовые данные без обращения к внешним источникам.'),
    'embedded_dashboard': (True, 'Простой экран с результатами', 'Экран внутри одного Python-приложения; не отдельная BI-система.'),
    'portable_package': (True, 'Независимая установка продукта', 'Доверенные Docker/Compose/install.md; ручной оператор сборки и запуска.'),
    'external_http': (False, 'Загрузка из внешних источников', 'У продукта нет разрешённого выхода в интернет.'),
    'scheduled_jobs': (False, 'Автоматический запуск по расписанию', 'Нет профиля планировщика и постоянного исполнителя загрузок.'),
    'airflow': (False, 'Загрузки под управлением Airflow', 'Airflow отсутствует в продуктовом профиле и наборе зависимостей.'),
    'external_bi': (False, 'Отдельная BI-система', 'Подключение и поставка отдельной BI-системы не реализованы.'),
    'xlsx': (False, 'Работа с Excel в самостоятельном продукте', 'Доверенный профиль не содержит движка чтения/записи XLSX; кредитный демо-отчёт не расширяет профиль продукта.'),
    'pptx': (False, 'Подготовка презентаций в самостоятельном продукте', 'Профиль не содержит проверенного инструмента создания PPTX.'),
    'multiple_services': (False, 'Несколько прикладных сервисов', 'Поддержаны одно приложение, доверенный шлюз и продуктовая БД; произвольный состав сервисов не поддержан.'),
    'multi_epic': (False, 'Последовательная разработка нескольких эпиков', 'Текущий выпуск продукта требует ровно один эпик.'),
    'interactive_browsing': (False, 'Исследование сайтов инструментами агента', 'У агентов нет инструмента браузера или поиска; URL в запросе сам по себе не прочитан.'),
    'incremental_workspace': (False, 'Доработка большого проекта по задачам', 'Текущий исполнитель генерирует небольшой полный набор файлов; постоянная рабочая копия по задачам ещё не реализована.'),
    'automated_rework': (False, 'Автоматическое исправление замечаний', 'Отрицательная проверка пока останавливает продукт; цикл исправления относится к этапу 9.'),
}


def registry():
    lock = Path(__file__).with_name('product_templates').joinpath('runtime-requirements.lock').read_bytes()
    payload = {
        'version': 1, 'profile': 'python-locked-v1',
        'scope': 'Возможности доверенного профиля. Это не проверка доступности сервисов и не гарантия выполнения бизнес-требований.',
        'runtime_lock_sha256': hashlib.sha256(lock).hexdigest(),
        'limits': {'epics': 1, 'source_files': 24, 'generation_file_target':6, 'generation_code_target_chars':10000, 'source_total_bytes': 2_000_000,
                   'development': 'Полный небольшой продукт за одну генерацию; не универсальная среда разработки.'},
        'capabilities': {key: {'supported': supported, 'name': name, 'detail': detail}
                         for key, (supported, name, detail) in DEFINITIONS.items()},
        'implementation_sha256': {name:hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest() for name in ['product_policy.py','product_contract.py','products.py']},
        'evidence': ['app/product_policy.py', 'app/product_contract.py', 'app/products.py',
                     'app/product_templates/runtime-requirements.lock', 'scripts/product_operator.py'],
    }
    payload['digest'] = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return payload
