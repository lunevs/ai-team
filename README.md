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
docker run --rm --network none ai-team-app:0.5.0 python -m pytest -q -p no:cacheprovider tests/unit
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

Панель «Команда» на всю ширину основного блока справа от боковой панели показывает семь ролей, текущую работу, выполненные/оставшиеся задачи выбранной истории и историю по кнопке. Обновление — каждые 10 секунд. Счётчики относятся к текущей ревизии: результат PM/SA — завершённая работа планирования, выполнение задач подтверждает закрытый статус OpenProject. При недоступности трекера счётчики помечаются как неполные. API: `GET /stories/{id}/team` с bearer token.

Карточки сотрудников расположены горизонтально с автоматическим переносом на следующие строки при недостатке ширины.

### Этап 2: аналитическая спецификация

После одобрения плана нажмите «Подготовить аналитическую спецификацию». SA описывает гранулярность и source-to-target mapping, Financial Analyst — паспорт метрик, бизнес-правила, экономические проверки и пользовательские истории. Результат и независимые ожидаемые значения доступны в UI и BookStack, со ссылками из задач. Для утверждения используется отдельная кнопка «Одобрить аналитическую спецификацию»; SQL-витрина пока не создаётся. Уточнение возвращает историю на новую ревизию и повторное одобрение плана.

Источники: схема `synthetic`, набор `credit-demo-v1`, seed 4182, январь–июнь 2026. Runtime читает источники, но не изменяет их. Миграции загружают набор от владельца БД; повторный запуск проверяет checksum. Детерминированный ежедневный доход — учебный денежный поток, он не рассчитывается из `annual_rate` и не моделирует договорный график/IFRS. Это ограничение набора, а не экономическая формула банка.

API: `GET /metadata/synthetic`, `POST /stories/{id}/analytics/start`, `/analytics/approve` (revision + plan_digest аналитики), `/analytics/revise` (answers). Все защищены bearer token. `GET /stories/{id}` содержит объект analytics. Бюджет общий для всех фаз истории; backend настроен на сервере.

Проверки: `python -m pytest -q tests/unit tests/integration` в worker-контейнере с остановленной очередью; `scripts/run_stage2_checks.py` запускается с отдельным mock worker на реальных сервисах и перезапускает только контейнер `ai-team-stage2-mock`. Он создаёт собственные тестовые истории, не одобряет пользовательские. Отчёты — `docs/stage2-check.json` и `docs/stage2-openrouter-check.json`.

Для структурированных контрактов OpenRouter по умолчанию используется `LLM_REASONING_MODE=disabled` (`reasoning.enabled=false`); `default` оставляет поведение провайдера. Финансовый контракт резервирует до 8192 output tokens, остальные — 4096. Пустой/невалидный final content не считается результатом, его usage учитывается, если провайдер его сообщил.

### Этап 3: SQL, QA, дашборд и выпуск

После одобрения аналитики нажмите «Запустить реализацию и QA». Developer создаёт SELECT; отдельный sandbox исполняет его в PostgreSQL. QA сверяет 24 среза с независимым Decimal-эталоном, а системный аналитик проверяет SQL. При дефекте создаётся Bug в OpenProject; допускаются две попытки исправления. Все вызовы входят в общий бюджет истории. Поддержан фиксированный демонстрационный контракт `credit-demo-v1`; произвольные ETL/Python и миграции от модели не исполняются.

Дашборд показывает фильтры продукта/региона, остаток и NPL выбранного месяца, динамику и отдельный контроль покрытия источников. Excel содержит формулы, данные SQL и контроль качества, checksum и SQL digest. Пропуск отличается от нуля, месячные остатки не складываются между месяцами. Скачивания требуют авторизации; volume доступен worker для записи и API для чтения. После формирования файлы проверяются по сохранённым SHA256 и не заменяются при повторе публикации.

Первичная настройка sandbox оператором:

```sh
docker compose build migrate
docker compose run --rm --no-deps migrate
python3 scripts/bootstrap_sandbox.py
docker compose up -d sandbox git-broker api worker ui
```

Sandbox использует отдельную роль без DDL в источниках/public, внутреннюю сеть без внешнего выхода, таймаут SQL 5 секунд и лимит 1000 строк. Worker не получает sandbox DB login, а sandbox не получает LLM/GitHub/трекер credentials. Контейнеры работают без root, с readonly rootfs и без Docker socket.

В локальный `.env` добавляется `GITHUB_RUNTIME_TOKEN` — fine-grained token только для `lunevs/ai-team`: Contents/Pull requests read-write, Metadata/Checks/Commit statuses read-only. Затем `docker compose up -d --no-deps --force-recreate git-broker`. Токен не передаётся worker/API/моделям; не вставляйте его в чат. `GITHUB_BASE_REF` по умолчанию `release/188`: PR реализации #17–21 уже слиты в эту ветку; контракт runtime доступен. Операторский GitHub credential не используется runtime. Без токена расчёт доступен, статус доставки — `WAITING_GITHUB`.

Broker создаёт `feature/<task>` и PR в `release/<epic>`; commit фиксируется после QA, force-push/автослияния нет. Для приёмки нужны независимое GitHub review другим аккаунтом, успешные `application`/`validate` и слияние task PR. Автор PR не может одобрить собственный PR, даже с другим токеном того же пользователя. После явной приёмки создаются release PR в main (`RELEASE_REVIEW`). После их независимого review/CI/ручного merge кнопка проверки выпуска создаёт тег по реальному merge commit и переводит задачи/эпики и историю в DONE. Объекты версий OpenProject создаются оператором, runtime-права не расширяются.

API: `POST /stories/{id}/execution/start`, `/retry`, `/gate`, `/accept` (revision + analytics digest в `plan_digest`), `/finalize-release`; `GET /stories/{id}/execution/artifacts/{name}`. `GET /stories/{id}` включает execution, QA, review, реальные PR и манифест файлов. После старта SQL критерии меняются через отмену и новую историю.

Проверки: `tests/unit` и `tests/integration`; host-скрипт `scripts/run_stage3_checks.py` требует отдельный worker `ai-team-stage3-mock` с `LLM_BACKEND=mock` и остановленный штатный worker. Он создаёт и отменяет собственные истории, проверяет исправления, скачивания, рестарт и отсутствие ложной приёмки. `scripts/run_stage3_security_checks.py` проверяет действительные права/сеть/лимиты внутри ограниченных сервисов. Отчёты: `docs/stage3-*.json`; реальный LLM и mock отмечены раздельно. Положительный выпуск с настоящим GitHub требует сервисного токена, независимого review и ручных merge.

Mock E2E требует отдельный broker без GitHub-токена, чтобы тестовые истории не публиковались в репозиторий:

```sh
docker compose stop worker
docker compose run -d --no-deps --name ai-team-stage3-git-mock -e GITHUB_RUNTIME_TOKEN= git-broker
docker compose run -d --no-deps --name ai-team-stage3-mock -e LLM_BACKEND=mock -e GIT_BROKER_URL=http://ai-team-stage3-git-mock:8000 worker
python3 scripts/run_stage3_checks.py
# Выполнить очистку и вернуть штатный worker также при ошибке теста:
docker rm -f ai-team-stage3-mock ai-team-stage3-git-mock
docker compose start worker
```

Служебная реальная история №219 прошла создание шести PR #22–27 в release/221 и release/226 от тестовой feature/193. Повтор вернул тот же commit/PR; CI успешен, независимое review и merge ожидаются. Штатная база остаётся release/188. Подробности — `docs/stage3-github-check.json`. PR реализации #17–21 слиты в release/188; PR результатов #22–27 ожидают человеческого review/merge и имеют другое назначение.

## Наблюдаемость — этап 4

Опциональный профиль `observability`: Grafana + Prometheus + Loki + Tempo + OTLP Collector. Первый запуск после получения всех PR этапа:

```sh
python3 scripts/bootstrap_observability.py
docker compose build migrate api worker ui sandbox git-broker
docker compose run --rm --no-deps migrate
docker compose --profile observability up -d otel-collector loki tempo prometheus grafana api worker ui
python3 scripts/check_observability_stack.py
```

Grafana: [дашборд команды](http://127.0.0.1:3001/d/ai-team-operations), логин `admin`; пароль — значение `GRAFANA_ADMIN_PASSWORD` в локальном `.env`. Bootstrap создаёт пароль и отдельный `TELEMETRY_METRICS_TOKEN`, сохраняет файл с правами 0600, не печатает секреты и не меняет существующие значения. На существующем Grafana volume изменение `.env` само по себе не меняет пароль аккаунта. Порт 3001 занят — сначала остановите запуск и согласуйте другой loopback-порт; не отправляйте пароль неизвестному сервису. Health-проверка подтверждает идентичность Grafana до Basic authentication.

Из карточки выбранной истории UI открывается тот же дашборд с фильтром story ID. TraceID в логах открывает Tempo; из span можно перейти в Loki. Поля экспорта ограничены метаданными: ID, роли, этапы, статусы, latency, usage. Истории, SQL, prompts, ответы, тексты исключений и credentials не экспортируются. HTTP auto-instrumentation отсутствует. Бюджеты/стоимость/QA берутся из committed PostgreSQL ledger; метрики — **gauges за весь журнал**, а не выбранный интервал расходов. Actual и reserved показаны отдельно, повтор worker не увеличивает значения. Длительность операций в Loki относится к выбранному окну; LLM p95 в ledger — за всю историю вызовов. Backend-фильтр применяется к метрикам, story-фильтр — к логам и бюджетам.

Бюджетные правила 80% (1 минута) и 100% (15 секунд), отсутствие ledger/Collector (1 минута) видны на дашборде; внешние email/мессенджер уведомления не настроены. Достижение 100% ещё не означает неуспех истории: проверяйте workflow. На метриках индивидуальных бюджетов отображаются до 100 последних активных историй; глобальные агрегаты охватывают весь журнал, в том числе явно помеченные mock-тесты.

Retention Loki/Tempo — 48 часов; Prometheus — 48 часов/128 MB. Это политика хранения, **не жёсткая квота диска Loki/Tempo**. Память пяти сервисов ограничена суммарно 1216 MiB; фактические замеры — `docs/stage4-resources.json`. Ошибки экспорта не откатывают бизнес-транзакции; очереди ограничены, часть telemetry при длительном отказе может теряться. PostgreSQL остаётся источником usage и аудита.

Отключение профиля без удаления данных:

```sh
docker compose --profile observability stop grafana prometheus loki tempo otel-collector
# В .env очистить OTEL_EXPORTER_OTLP_ENDPOINT и GRAFANA_PUBLIC_URL, затем:
docker compose up -d --no-deps --force-recreate api worker ui
```

JSON stdout остаётся включён. Volumes сохраняются. Повторный bootstrap снова включает локальный OTLP endpoint.

`python3 scripts/run_stage4_checks.py` — отдельный локальный opt-in E2E, временно останавливает штатный worker и профиль для замера RAM. Создаёт только свои синтетические истории, запускает scoped mock worker и tokenless Git broker, проверяет реальные SQL/QA/Bug/BookStack, рестарт, privacy canaries, бюджетный alert и отказ Collector. В `finally` отменяет свои истории, удаляет только два тестовых контейнера и возвращает штатный worker. На время теста не создавайте новые истории. Денежные LLM-вызовы и GitHub PR в этом тесте отсутствуют. Отчёты `docs/stage4-*.json` отделены от предыдущего реального OpenRouter-прогона.
