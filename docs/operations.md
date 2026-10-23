# Эксплуатация и переносимость

Это локальный MVP. Успешный запуск на ARM64, S3 smoke и восстановление локального снимка не означают проверенного размещения в AWS/Yandex Cloud, готовой private LLM или production SLA.

## Артефакты local/S3

По умолчанию ARTIFACT_BACKEND=local и volume delivery_artifacts. Новые manifests фиксируют backend, SHA256 и size. Старые manifests читаются как local. Предел файла 8 MiB. API выдаёт файл только после аутентификации и проверки всего содержимого, без public/presigned URL. Повреждение/отсутствие даёт 503, а не скачивание неверного результата.

Для S3 оператор создаёт private bucket, worker credentials GET/PUT только под S3_PREFIX и отдельные API credentials GET-only. Заполняются S3_ENDPOINT (HTTPS origin без path/query/userinfo), S3_BUCKET, S3_REGION, S3_PREFIX, S3_ACCESS_KEY_ID/S3_SECRET_ACCESS_KEY, S3_READ_ACCESS_KEY_ID/S3_READ_SECRET_ACCESS_KEY, ARTIFACT_BACKEND=s3. Пересоздать api/worker. Ключи не передаются UI, sandbox и git-broker. Локальное исключение S3_ALLOW_LOCAL_HTTP=true допускает только hostname garage/localhost/127.0.0.1; для облака оно не разрешает HTTP. Тестовый Garage single-node не является отказоустойчивым хранилищем.

Используются только SigV4 path-style GET/PUT, content-addressed object keys и проверка чтения после записи; повтор того же digest не создаёт новую запись. Запись другого содержимого в уже опубликованный manifest не допускается workflow. Нет list/delete/multipart/presign, автоматического получения IAM/STS credentials, автоматической миграции local↔S3 и защиты от администратора хранилища. Для production включить versioning/Object Lock и provider-side backups отдельно, проверить совместимость конкретного провайдера. Не менять endpoint/bucket/prefix без переноса прежних объектов: manifests содержат backend, но не полный независимый профиль подключения.

## Внешняя PostgreSQL

Три отдельных URI: APP_DATABASE_URL (ai_team_runtime), APP_MIGRATION_DATABASE_URL (owner/migrations), SANDBOX_DATABASE_URL (ai_team_sandbox). Runtime не получает migration URI. URI password необходимо percent-encode. Для внешнего hostname необходимы sslmode=verify-full и sslrootcert=/run/secrets/postgres-ca.crt. CA монтируется read-only в нужные контейнеры через отдельный Compose override. Проверяется hostname сертификата; sslmode=require не заменяет verify-full. Постоянная инфраструктура postgres в основном Compose остаётся локальной; зависимости на неё для remote deployment переопределяет оператор. Приложение не создаёт аккаунты на удалённой БД автоматически.

Перед переключением перенести схему/данные/checkpoints, создать owner/runtime/sandbox с прежними минимальными правами, применить миграции owner-ролью и сверить synthetic checksum/количество историй/usage/артефакты. В managed PostgreSQL физический volume не переносится: использовать pg_dump/pg_restore и проверить grants. Наш cold backup предназначен для восстановления той же закреплённой PostgreSQL/MariaDB версии, а не для смены major версии или managed provider.

Sandbox по умолчанию не имеет внешней сети. Для managed DB нужен отдельный override сети с выходом только к PostgreSQL endpoint:5432; не подключать sandbox к общей frontend/NAT сети. Security group/firewall разрешает соединение от sandbox, API/worker и отдельного migration runner, запрещает public DB ingress. На локальном Docker нельзя обещать фильтрацию egress по доменам одной только Compose-сетью.

## Private LLM

LLM_BACKEND=private включает только fail-closed PrivateLLMStub: CapabilityError до кеша и резервирования бюджета, без сети и подмены другой моделью. Для интеграции потребуется отдельный адаптер/контракт capabilities, авторизация, тарификация/usage, структурированный вывод, таймауты и тесты. Рабочие backends сейчас openrouter и явно обозначенный mock.

## Зашифрованная резервная копия

Операторская команда запускается на хосте, Docker socket в runtime не добавляется:

```sh
python3 scripts/backup_installation.py snapshot .backups/2026-10-22
python3 scripts/backup_installation.py verify .backups/2026-10-22
python3 scripts/backup_installation.py restore .backups/2026-10-22 --prefix ai-team-dr-a1b2c3d4
```

Snapshot кратко останавливает только запущенные Compose-контейнеры ai-team, копирует все существующие именованные persistent volumes и возвращает именно их в finally. Архивы шифруются AES-256-CBC/PBKDF2 200000 с отдельным encrypt-then-MAC: HMAC-SHA256 подписывает manifest с SHA256 ciphertext. Проверка подписи/хешей проводится до дешифрования и создания volumes. Ключ .backup-key (0600) генерируется один раз; .backups (0700), .backup-key и .local исключены из Git и Docker build. .env сохранён только в encrypted deployment.env.enc, может содержать все рабочие credentials. Не печатать/отправлять содержимое backup/key/env в логи или чат.

Restore разрешает только новый префикс ai-team-dr-<8 hex>, заранее отвергает существующие target volumes и никогда не заменяет рабочие данные. Полученные volumes содержат прежних пользователей/секреты/данные: не подключать их к открытому Internet. Расшифрованный .env — в .local/<prefix>/.env (0600), не внутри каталога, предназначенного для копирования архивов. Частичный restore оставляет только созданные изолированные volumes для проверки; production rollback не запускается. Контейнеры на восстановленных volumes оператор создаёт отдельно с прежними pinned images. Перед запуском worker проверить данные и отключить внешние действия: scoped WORKER_STORY_IDS, mock и broker без GitHub token. После проверки удалять только точно перечисленные временные контейнеры/volumes.

Держать зашифрованные копии вне исходного компьютера; ключ — отдельно (например, в менеджере секретов). Проверять verify и периодически восстанавливать тестовую копию. Без ключа архив не восстанавливается. Задание scheduler/внешняя доставка/ротация архивов пока операторские; автоматически не настроены. Snapshot для ARTIFACT_BACKEND=s3 отказывает: внешние объекты не входят в volume backup, нужен отдельный согласованный backup/versioning plan провайдера. Исключение ради успешного отчёта не делается.

## План AWS / Yandex Cloud

| Назначение | AWS (план) | Yandex Cloud (план) |
|---|---|---|
| Сеть | VPC, private application/data subnets, security groups | VPC, private subnets, security groups |
| PostgreSQL | RDS PostgreSQL без public access, provider CA | Managed PostgreSQL без public access, provider CA |
| Артефакты | private S3 bucket, отдельные reader/writer permissions | private Object Storage bucket, отдельные reader/writer permissions |
| Секреты | Secrets Manager/KMS, отдельный migration runner | Lockbox/KMS, отдельный migration runner |
| Вход | HTTPS ingress, SSO/OIDC перед UI/API | HTTPS ingress, SSO/OIDC перед UI/API |
| Восстановление | RDS restore/PITR + object backups + конфигурация | Managed PostgreSQL restore + object backups + конфигурация |

В облаке нужны пользовательская auth вместо общего локального token, управление доступом, отдельные IAM identities и проверка сетевых политик. Открывать наружу только HTTPS ingress; PostgreSQL/OpenProject/BookStack/Loki/Tempo/Collector/Prometheus закрыты. Worker egress ограничивается LLM endpoint и внутренними инструментами; Git broker — GitHub API; sandbox — только БД. UI не получает credentials моделей/хранилищ. Запрет авто-merge и независимое review сохраняются. Документировать retention, стоимость, требуемые RPO/RTO и провести restore drill до определения SLA. Реальный cloud deployment и выбор провайдера требуют отдельной задачи.

Официальные источники: [RDS TLS](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/PostgreSQL.Concepts.General.SSL.html), [Yandex PostgreSQL restore](https://yandex.cloud/ru/docs/managed-postgresql/operations/cluster-backups), [Lockbox](https://yandex.cloud/ru/docs/lockbox/quickstart), [S3 API](https://docs.aws.amazon.com/AmazonS3/latest/API/API_PutObject.html).

## Диагностика

- /ready=503: сначала PostgreSQL, права runtime и свежесть worker heartbeat; не меняйте worker на mock для обхода ошибки.
- Artifact 503: проверить backend сохранённого manifest, SHA256/size, local volume/S3 endpoint и раздельные reader credentials. Не заменять опубликованный файл другим содержимым.
- TLS error: проверить hostname/CA/срок сертификата и время; не отключать verify-full.
- Private CapabilityError: ожидаемое ограничение stub, нужен адаптер, а не другой токен.
- WAITING_GITHUB: проверить сервисный token, base contract, права; ACCEPTANCE требует отдельного действия пользователя, release — независимого review/merge.
- Backup verify error: не продолжать restore; проверить ключ и целостность, использовать предыдущую проверенную копию.
- При ошибке возврата сервисов после backup: список текущих контейнеров docker compose --profile '*' ps; запускать прежние сервисы с прежними pinned images, не docker compose down -v.
