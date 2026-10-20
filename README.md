# Bank AI Delivery Team

Локальная команда AI-агентов для банковской аналитики на синтетических данных.
Требования, роли и план: [agent_bank_mvp_spec.md](agent_bank_mvp_spec.md).

## Текущий объём

Этап 0 принят: OpenProject + PostgreSQL + BookStack/MariaDB, проверки API/OpenRouter/GitHub.
Этап 1: FastAPI, отдельный worker с LangGraph/PostgreSQL checkpoints, PM/SA и Streamlit.
Семь unit/integration тестов и 13 e2e проверок с mock LLM прошли; реальное планирование
OpenRouter также проверено (2 вызова, 7 832 токена, около $0,003385). Эпик реализации №41, задачи №42–45.
SQL-исполнение и остальные исполнительные роли относятся к следующим этапам.

## Запуск

Нужны Docker Compose v2 и Python 3.12+ для служебных скриптов (без сторонних зависимостей).

```sh
# Для новой установки; существующий .env не перезаписывать:
cp .env.example .env
python3 scripts/init_env.py
# Указать OPENROUTER_API_KEY и OPENROUTER_MODEL в .env.
docker compose config --quiet
docker compose up -d
# Дождаться healthy у openproject и bookstack:
docker compose ps --all
python3 scripts/bootstrap_services.py
python3 scripts/check_integrations.py
python3 scripts/check_openrouter.py
python3 scripts/check_github.py
```

Первое скачивание образов и миграции занимают несколько минут. При изменении локальных
секретов после создания volumes пароли в БД автоматически не меняются.
`init_env.py` сохраняет существующие переменные. `bootstrap_services.py` заменяет стандартные
пароли admin и сохраняет bootstrap credentials только в `.env` (права 0600).
Не запускать bootstrap одновременно в нескольких процессах.

- OpenProject: http://localhost:8080 — логин `admin`, пароль `OPENPROJECT_ADMIN_PASSWORD` в `.env`.
- BookStack: http://localhost:8082 — логин `admin@admin.com`, пароль `BOOKSTACK_ADMIN_PASSWORD` в `.env`.
- Hocuspocus: ws://localhost:8081 — совместное редактирование OpenProject.

Bootstrap-токены административные, предназначены для инфраструктурного spike. В этапе 1
нужно создать сервисные аккаунты с минимальными правами; не передавать bootstrap credentials
агентам. Токен BookStack действует 30 дней. При восстановлении старой БД сверять её с `.env`.

`check_integrations.py` создаёт/находит проект, эпик и две задачи, связывает задачи зависимостью,
проверяет чтение/изменение/комментарии и публикует актуальное ТЗ в BookStack.
После первого создания проекта повторить `bootstrap_services.py`, чтобы включить Epic/User story/Bug (API v3 не изменяет список типов проекта), затем повторить smoke test. Последовательный повтор использует те же сущности; защита от конкурентных повторов здесь
ещё не реализована. `check_openrouter.py` делает один платный запрос только с синтетическим текстом.
`check_github.py` использует существующий Git credential helper, не печатает credentials.
Полный вывод `docker compose config` содержит секреты: для проверки использовать `--quiet`
или `--services`, не публиковать отрендеренную конфигурацию.

## Изоляция и ресурсы

Образы закреплены версиями и digest, поддерживают ARM64. Базы и cache доступны только во
внутренней сети. Веб-сервисы подключены также к frontend-сети; опубликованные порты привязаны
к loopback. На этапе 0 у веб-сервисов возможен исходящий доступ; sandbox ещё не существует.
Docker socket в контейнеры не монтируется; OpenRouter-ключ не передаётся инфраструктуре.
Официальные entrypoint БД и LinuxServer требуют начального запуска с root для настройки
прав; внутренние процессы переключают пользователя по правилам образов.
Логи Docker ограничены 3 файлами по 10 MB на сервис. Полные промпты не логируются нашими
скриптами; единая JSON-телеметрия приложения вводится в этапе 1.

Compose адаптирован из официального OpenProject stable/17 (upstream commit
`20732244fe11f3d569b8151a199331a9c6740f56`): сохранены seeder/web/worker/cron/cache/hocuspocus,
PostgreSQL отделён, прямой loopback-порт заменяет proxy для локального spike,
autoheal с Docker socket исключён. Это локальная HTTP-конфигурация; для облака потребуется TLS.

## Диагностика и остановка

```sh
docker compose ps --all
docker compose logs --tail 50 op-seeder openproject postgres
docker compose stats --no-stream
docker compose stop
docker compose start
# Удалить контейнеры и сети, сохранив volumes:
docker compose down
```

Если первый `up` завершился на временной неготовности БД, проверить логи и повторить
`docker compose up -d`. Seeder должен завершиться с кодом 0.
При `role does not exist` проверить результат init.sql; он запускается только для новой БД.
Не удалять volumes как способ устранения ошибки без резервной копии.
При нехватке памяти проверять `docker stats` и настройки Docker Desktop; другие проекты
автоматически не останавливаются.

## Резервные копии

Пока приложение не реализовано, хранить вместе: PostgreSQL, MariaDB, volumes `opdata` и
`bookstack_data`, защищённую копию `.env`. Копии содержат credentials и доступны только владельцу.
Для согласованной копии приостановить записи в веб-сервисы и worker; использовать pg_dump
для обеих БД (`openproject`, `ai_team`), mariadb-dump для `bookstack`, затем архивировать volumes
с сохранением владельцев. Перед восстановлением остановить потребителей, восстановить БД
в совместимые версии и файлы, затем проверить API. Автоматизация и проверка восстановления
входят в последующие этапы; восстановление пока не проверено.

Полная очистка данных — отдельная разрушительная операция `docker compose down -v`;
применять только осознанно после копирования нужных данных. Никогда не удалять volumes чужих проектов.

## Лицензии и ограничения

OpenProject Community: GPLv3 (проверен LICENSE установленного образа), BookStack: MIT.
Docker-образ BookStack — community distribution LinuxServer, указанный в документации BookStack.
Платные функции OpenProject не используются. Реальные ограничения интеграций и измерения
фиксируются в ТЗ и `docs/integration-check.json`. Готовность к production не заявляется.

## Этап 1: API, worker и Streamlit

Работа ведётся в эпике №41, задачах №42–45. После принятой инфраструктуры подготовлены
API, очередь, LangGraph checkpoints, PM/SA, публикация плана и отдельный Streamlit UI.

Для runtime-доступов оператор запускает `python3 scripts/bootstrap_runtime.py` после
согласования прав: неадминистративная роль PostgreSQL; семь участников OpenProject с
чтением/созданием/изменением задач, назначаемостью, управлением иерархией и зависимостями;
BookStack — чтение книг и создание/изменение страниц с API, без удаления и управления аккаунтами.
OpenProject-роль действует только в проекте `ai-team`. BookStack-роль действует на локальный
инстанс; её токен — 30 дней. Значения остаются в `.env`.

```sh
python3 scripts/bootstrap_runtime.py
docker compose build api
docker compose --profile ui up -d api worker ui
docker compose ps --all
```

- UI: http://localhost:8501
- API/OpenAPI: http://localhost:8000/docs
- API требует `Authorization: Bearer <APP_API_TOKEN>` (кроме `/health` и `/ready`).
- `POST /stories` требует `Idempotency-Key`, текст и `synthetic_only: true`.
- Одобрение содержит `revision` и `plan_digest`, полученные из истории. Устаревший план
  отклоняется с 409; повторное одобрение того же плана безопасно.
- При недоступном трекере создание возвращает 202: история уже сохранена, внешний ID
  появится после восстановления интеграции. Повторять с тем же ключом.
- `POST /stories/{id}/resume` принимает `answers`; уточнение создаёт новую ревизию и
  требует нового одобрения. Отмена сохраняется в БД и синхронизируется с трекером.
- После одобрения статус `IN_PROGRESS` означает конец вертикального среза этапа 1.
  Аналитики исполнения, SQL, витрины, QA расчётов и отчёты следующих этапов ещё не запускаются.

API не получает OpenRouter и BookStack ключи; UI получает только API-токен. Worker использует
отдельные PM/SA токены и документационный токен. Миграции идут отдельным процессом под
владельцем базы; API/worker используют роль без DDL. Контейнеры приложения non-root,
read-only rootfs, без capabilities и без Docker socket.

Для детерминированной проверки используется отдельный запуск worker с `LLM_BACKEND=mock`:

```sh
LLM_BACKEND=mock docker compose up -d worker
python3 scripts/run_stage1_checks.py
# Вернуть реальную модель из .env:
docker compose up -d worker
```

Скрипт создаёт только синтетические тестовые истории, проверяет API/трекер/документацию,
одобрение, вопросы, бюджет, отмену и перезапускает worker. Требуются реальные локальные
сервисы; mock относится только к LLM. Его нельзя выдавать за проверку реального OpenRouter.

```sh
# Unit без сети и секретов:
docker run --rm --network none ai-team-app:0.2.0 python -m pytest -q -p no:cacheprovider tests/unit
# Integration проверяет реальный PostgreSQL. На время теста остановить worker,
# чтобы он не забирал технические задания тестов из той же локальной очереди:
docker compose stop worker
docker compose run --rm --no-deps worker python -m pytest -q -p no:cacheprovider tests/integration
docker compose start worker
```

LLM reservations сохраняются до запроса; при таймауте резерв не освобождается автоматически.
Цена оценочная по `LLM_INPUT_USD_PER_MILLION`/`LLM_OUTPUT_USD_PER_MILLION`; сопоставьте их с
моделью. `LLM_STRUCTURED_MODE=json_object` включает JSON mode, `text` оставляет проверку
Pydantic без запроса JSON capability; неподдерживаемая настройка даёт явную ошибку.
Действия инструментов выбирает код, модель не получает произвольный shell или HTTP.

При отмене уже отправленный запрос внешнему сервису может завершиться; следующие действия
останавливаются. Marker IDs и внешний реестр позволяют найти такой результат после сбоя.
Полные story/промпты не выводятся в логи; в БД и KnowledgeBase сохраняется нужное содержание
синтетической задачи. Полный профиль OTel/Grafana относится к этапу 4.

Демонстрационная история с реальной моделью: OpenProject #70, статус PLAN_REVIEW.
План оставлен для проверки заказчиком; полные результаты — docs/stage1-check.json
и docs/stage1-openrouter-check.json. Первоначальные PR #4–7 слиты; финальные исправления — в PR #8–11. Локально используется
validation/epic-41. После слияния и приёмки этапа 1 можно переходить к аналитическим данным.

Истории с mock backend помечаются предупреждением в UI; реальный backend остаётся openrouter.
Ожидающие workflow отслеживают ручные BLOCKED/CANCELLED в OpenProject в фоне.
