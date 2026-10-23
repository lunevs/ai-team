# Матрица приёмки MVP — 23 октября 2026

Техническая реализация и приёмка заказчиком — разные состояния. Отчёты содержат backend; mock не считается проверкой реальной модели/GitHub.

| № ТЗ | Критерий | Подтверждение / что остаётся |
|---|---|---|
| 1 | Локальный запуск Mac ARM64 | Проверен на этапах 0–4; версия 0.6.0 запущена: API ready, прежний Excel прошёл SHA256, основной режим local/OpenRouter. Облачная инсталляция не проверялась. |
| 2 | История → work package/план/задачи | Реальные OpenProject API и связи задач; stage1, stage3, stage4 E2E. |
| 3 | Документация в KnowledgeBase | BookStack API, канонический документ и страницы историй; ссылки из задач. |
| 4 | Человек одобряет до SQL | Отдельные approvals плана/аналитики и digest; отрицательные API/тесты; чужие истории не одобряются тестами. |
| 5 | Спецификация → SQL → независимая QA | Реальный OpenRouter прогон №219, 24 строки; отдельный Decimal-эталон, Bug и ограниченные исправления. Этап 5 дополнительно проверяет mock workflow на восстановленной БД/S3. |
| 6 | Восстановление worker без дублей | Stage1/3/4, crash/retry и неизменный usage/артефакты. |
| 7 | Сквозная наблюдаемость | Реальные Collector/Loki/Tempo/Prometheus/Grafana; stage4 reports, единый trace API+worker. |
| 8 | Права/изоляция/секреты | Негативные SQL/network tests, metadata-only export, исключение .env/.backup-key/.backups/.local из Git и Docker build; отдельные S3 reader/writer, внешняя DB TLS. |
| 9 | Unit/integration/E2E/CI | 52 unit/integration и 6 финальных E2E проверок passed; проверки в GitHub Actions и opt-in host scripts. |
| 10 | Эксплуатация/backup/restore/cloud plan | scripts/backup_installation.py + docs/operations.md. Реальный encrypted snapshot 10 volumes, restore в новые volumes, TLS, 63 файла SHA256, DB/checkpoint/BookStack/OpenProject controls — docs/stage5-recovery-check.json. |
| 11 | Семь ролей, SA=architect/assigner | Реальные назначения в OpenProject, команда и история в UI. Designer/Application/отчётность используют детерминированные адаптеры/шаблоны, а не свободное исполнение кода моделей. |
| 12 | Feature/release/PR/review/релиз | Ветки и настоящие PR работают, gates требуют независимого review и CI. **Реальный полный выпуск release→main→tag→DONE пока не подтверждён**: остаётся пользовательская приёмка, независимое review/merge релизных PR. Mock-проверка логики не заменяет эту приёмку. |
| 13 | Передача цифр/Excel/дашборда/инструкций | Реальные Excel с QA/контролем источников, дашборд и инструкции переданы; финальное принятие результата заказчиком остаётся отдельным действием. Презентация не запрашивалась. |

Ограничения: основной сценарий — согласованная кредитная аналитика на синтетических данных. Нет универсальной автономной разработки произвольных приложений, реальной private LLM, облачного развёртывания, IAM/STS auto-discovery, расписания/off-host доставки backups и внешних notifications. Managed PostgreSQL, AWS/Yandex S3 и production SSO/network policies требуют отдельных интеграционных проверок. S3-проверка использует настоящий локальный Garage; новый E2E — mock LLM с настоящими локальными инструментами, без платных вызовов или тестовой записи в GitHub.
