# Bank AI Delivery Team

Локальная команда AI-агентов для банковской аналитики на синтетических данных.
Требования, роли и план: [agent_bank_mvp_spec.md](agent_bank_mvp_spec.md).

## Текущий объём

Этап 0: реальная инфраструктура OpenProject + PostgreSQL + BookStack/MariaDB,
проверки API, OpenRouter и GitHub. В OpenProject: проект `ai-team`, эпик №38, задачи №39 и №40. FastAPI, LangGraph, Streamlit и агенты пока не реализованы.

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
