# План реализации PingStats

Правило: одна задача — один логически завершённый результат. После каждой задачи: тесты, `ruff check .`, `ruff format --check .`, актуализация документации и отчёт. Задачи не разрешают контейнерную инфраструктуру.

## Этап 0 — проектирование

| ID | Название | Цель | Зависимости | Объём работ | Готово, когда |
|---|---|---|---|---|---|
| PS-000 | Архитектурный baseline | Зафиксировать продукт, модули, БД, API, ADR | — | Документы этапа 0 | Все документы согласованы заказчиком |
| PS-001 | Закрыть открытые решения — завершено | Утвердить period/tau, privacy, corrections, seeding, session membership, notifications и инфраструктуру | PS-000 | Точечно обновить PRD/architecture/DB/API/ADR/AGENTS | Решения зафиксированы; отложенные вопросы не блокируют PS-002 |

## Этап 1 — фундамент

| ID | Название | Цель | Зависимости | Объём работ | Готово, когда |
|---|---|---|---|---|---|
| PS-002 | Python skeleton — завершено | Локально запускаемый проект | PS-001 | uv, pyproject, package, settings, Ruff/pytest; без бизнес-функций и контейнеров | App health test и quality checks проходят |
| PS-003 | PostgreSQL infrastructure — завершено | Async SQLAlchemy, DI, UoW и Alembic baseline | PS-002 | engine/session lifespan, explicit UoW, dev DB health, safe initial revision | Connection/migration/tests/quality checks подтверждены |
| PS-004 | Users и development auth — завершено | Пользователи и защищённый локальный API | PS-003 | users migration/repository/service/API, JWT, fixed dev-login, get_current_user, tests | Migration/API/JWT/concurrency/quality checks подтверждены |
| PS-005 | Friendships — завершено | Полный lifecycle заявок и взаимной дружбы | PS-004 | migration, repository/UoW, service, protected API, concurrency tests | Lifecycle/permissions/constraints/races/quality checks подтверждены |
| PS-006 | Matches Core — завершено | Единые правила счёта и standalone-матчи | PS-005 | migration, domain validator, repository/UoW, lifecycle/results/corrections API, race tests | Score/lifecycle/permissions/audit/constraints/concurrency/quality checks подтверждены |
| PS-011 | App factory и errors | Стабильный HTTP shell | PS-002 | FastAPI factory, lifespan, `/health`, error envelope, request ID | API tests подтверждают contract |
| PS-014 | Test infrastructure | Повторяемые PostgreSQL integration tests | PS-003 | fixtures, transaction isolation, factories | Тесты изолированы и документирован локальный запуск |

## Этап 2 — users и временный auth

| ID | Название | Цель | Зависимости | Объём работ | Готово, когда |
|---|---|---|---|---|---|
| PS-020 | Users schema/repository — покрыто PS-004 | Надёжный Telegram upsert | PS-003 | users migration, model, repository | Unique Telegram ID и repository integration tests проходят |
| PS-021 | Users services/API — покрыто PS-004 | Профиль и каталог | PS-020 | upsert/query services, schemas, endpoints | API permissions/pagination tests проходят |
| PS-022 | JWT token service — покрыто PS-004 | Изолированная token issuance/validation | PS-002 | JWT settings/service, expiry/signature/type/UUID tests | JWT tests проходят |
| PS-023 | Development login — покрыто PS-004 | Безопасный локальный вход | PS-020, PS-022 | conditional router, fixed identity, get_current_user | Dev success; production 404; no impersonation |
| PS-024 | Bot `/start` user sync | Идемпотентная регистрация ботом | PS-020 | aiogram adapter вызывает общий upsert | Повторный start обновляет без дубля |

## Этап 3 — friendships

| ID | Название | Цель | Зависимости | Объём работ | Готово, когда |
|---|---|---|---|---|---|
| PS-030 | Friendship schema — покрыто PS-005 | Защитить пары на уровне БД | PS-004 | requests/friendships migration | Self/duplicate/concurrent constraints tested |
| PS-031 | Friendship commands — покрыто PS-005 | Полный lifecycle заявки | PS-030 | send/accept/reject/cancel/remove services | Unit + transaction tests всех переходов |
| PS-032 | Friendship API — покрыто PS-005 | Доступ клиенту | PS-031, PS-023 | endpoints, lists, permissions | HTTP acceptance scenarios проходят |

## Этап 4 — единое ядро матчей

| ID | Название | Цель | Зависимости | Объём работ | Готово, когда |
|---|---|---|---|---|---|
| PS-040 | Score domain — покрыто PS-006 | Единая проверка партий/матча | PS-002, PS-001 | value objects, validator, winner resolver | Граничные deuce/series tests проходят |
| PS-041 | Match schema — покрыто PS-006 | Persist matches, current sets и correction audit | PS-020, PS-040 | migration, constraints/indexes/deferred participant trigger | DB integrity tests, ровно 2 participants |
| PS-042 | Standalone lifecycle — покрыто PS-006 | Invitation → active/rejected/cancelled | PS-041 | repositories/services | Transition/concurrency tests проходят |
| PS-043 | Result recording — покрыто PS-006 | Атомарная incremental/full запись | PS-041 | row lock, version, semantic full-result retry | Retry не дублирует; winner server-derived |
| PS-044 | Result correction — покрыто PS-006 | Аудируемые исправления | PS-043 | JSONB snapshots, author/time/reason, 24h policy | Previous/new snapshots доступны и неизменны |
| PS-045 | Match API — покрыто PS-006 | Защищённые standalone endpoints | PS-042–PS-044, PS-023 | routes/schemas/list/history | API acceptance и permissions tests |

## Этап 5 — игровые сессии

| ID | Название | Цель | Зависимости | Объём работ | Готово, когда |
|---|---|---|---|---|---|
| PS-007.1 | Game Sessions schema — завершено | Хранение сессий, истории участия, текущей очереди/аудита и match sequence | PS-006 | ORM metadata, revision 0005, PostgreSQL constraints, transaction tests; без services/API | Metadata/schema/checks/FK/unique/standalone compatibility подтверждены |
| PS-007.2 | Game Sessions lifecycle и участники — завершено | Application/domain-логика create/read/join/leave/start/complete/cancel | PS-007.1 | DTO, repository, UoW integration, row locks, membership/audit history, unit и PostgreSQL race tests; без API/queue/matches | Permissions/status transitions/rollback/history и concurrent join/start/complete подтверждены |
| PS-007.3 | Game Sessions queue и ротация — завершено | Durable current pair, ordered waiting queue и loser-rotates | PS-007.2 | Pure strategy, revision 0006, start/join/leave integration, result-id deduplication, queue audit и PostgreSQL race tests; без session matches/API | 2/3/5/10 players, retries и concurrent rotation/join/leave сохраняют инварианты |
| PS-007.4 | Game Sessions + Matches — завершено | Общие match results, атомарная ротация и successor match | PS-007.3, PS-006 | Shared result writer, session match orchestration, revision 0007, cancel/cancel-and-leave/complete и PostgreSQL races; без HTTP API/corrections | Result→rotation→next match атомарен; retry и races не дают двойной ротации/sequence |
| PS-007.5a | Game Sessions HTTP API — завершено | JWT-защищённый transport для реализованных session workflows | PS-007.4 | Router, Pydantic schemas, DI, read-only history/current queries, API/unit tests и OpenAPI; без PostgreSQL E2E | Все session/lifecycle/queue/match endpoints опубликованы; stale match_id даёт 409; API contract tests проходят |
| PS-007.5b | Game Sessions E2E acceptance — завершено | Завершить приёмку PS-007 через реальный HTTP/PostgreSQL | PS-007.5a | Full 3-player flow, edge cases, permissions, HTTP races, fault rollback, OpenAPI/schema/regression checks | PostgreSQL E2E/concurrency и полный regression suite проходят без критических дефектов |
| PS-050 | Session schema — покрыто PS-007.1 | Состав/sequence/current queue audit | PS-041 | models, migration, constraints/indexes | FK/unique/check/history integrity tests |
| PS-051 | Rotation strategy | Детерминированный loser-rotates | PS-040 | pure strategy | 2, 3, N player scenarios tested |
| PS-052 | Session lifecycle | Create/start/join/leave/cancel/resume/finish | PS-050, PS-051 | dynamic membership, waiting-tail, queue restore, permissions | Lifecycle, cancellation and queue audit tests |
| PS-053 | Atomic session advance | Result → snapshot → next match | PS-043, PS-052 | orchestration under session lock | Parallel result creates one next match |
| PS-054 | Session correction conflict | Сохранить фактическую историю | PS-044, PS-053 | needs_review/stale event | Winner-changing correction never rewrites later rows |
| PS-055 | Session API | UI contracts | PS-052–PS-054 | routes/history/queue views | End-to-end 3-player rotation passes |

## Этап 5.1 — production authentication

| ID | Название | Цель | Зависимости | Объём работ | Готово, когда |
|---|---|---|---|---|---|
| PS-008.1 | Telegram Mini App Authentication — завершено | Безопасный production-вход через подписанный initData | PS-004, PS-007 | Official HMAC validation, auth_date freshness, Telegram user upsert, JWT endpoint, unit/API/PostgreSQL race tests | Подделки и stale/future data отклоняются; concurrent first login создаёт одного user; regression/quality checks проходят |
| PS-008.2a | React Mini App Foundation — завершено | Mobile shell, Telegram login и профиль | PS-008.1 | React/TypeScript/Vite, Router, Query, Tailwind, official Telegram SDK, typed Fetch client, memory-only JWT, development login, tests | Production build не содержит dev-login; Telegram bootstrap/profile/navigation states проходят build/lint/tests |
| PS-008.2b | Game Sessions Frontend MVP — завершено | Полный mobile UI существующего session API | PS-008.2a, PS-007.5b | Create/list/detail/join/invite, live queue/participants/history, lifecycle actions, full result, stale-state protection, polling/invalidation, frontend tests | Backend остаётся source of truth; invite/join и 409 recovery проверены; build/lint/tests и локальный smoke проходят |

## Этап 6 — турниры

| ID | Название | Цель | Зависимости | Объём работ | Готово, когда |
|---|---|---|---|---|---|
| PS-060 | Tournament schema | Турнир/участники/сетка | PS-041, PS-001 | migrations/indexes | Capacity/seed/node integrity tests |
| PS-061 | Single-elimination builder | Rating-seeded детерминированная сетка/BYE | PS-060 | rating snapshot strategy, standard seed placement | Sizes 2…non-power-of-two and strong-player separation tested |
| PS-062 | Registration lifecycle | Draft/registration/join/leave/start | PS-061 | password hash, locks, min 2, pre-start withdrawal | Concurrent capacity, password and lifecycle tests |
| PS-063 | Bracket progression | Winner → next node/final | PS-043, PS-062 | orchestrator, match creation | Double progression impossible; champion set |
| PS-064 | Tournament corrections | Не скрывать downstream conflict | PS-044, PS-063 | needs_review workflow boundary | Downstream history preserved |
| PS-065 | Tournament walkover | Техническое продвижение без фиктивного счёта | PS-063 | separate outcome, organizer permission, rating exclusion | No sets/rating input; bracket advances once |
| PS-066 | Tournament API | Полный клиентский flow | PS-062–PS-065 | routes/bracket/walkover view | End-to-end tournament acceptance passes |

## Этап 7 — рейтинг

| ID | Название | Цель | Зависимости | Объём работ | Готово, когда |
|---|---|---|---|---|---|
| PS-070 | Glicko-2 strategy | Чистая корректная математика | PS-001, PS-002 | Decimal algorithm, configurable 1500/350/0.06/tau 0.5 | Published reference vectors match tolerance |
| PS-071 | Rating schema | Current/period/input/history | PS-013, PS-020 | migrations/constraints | Period overlap/idempotency DB tests |
| PS-072 | Period input collection | Frozen rated results | PS-043, PS-071 | collector/deduplication | All contexts included once, unrated excluded |
| PS-073 | Daily period scheduler/publish | Atomic UTC daily rating и catch-up | PS-070–PS-072 | lifespan scheduler, advisory lock, dev run-due, missed periods | Retry identical; restart catches up; partial publish impossible |
| PS-074 | Rating correction rebuild | Recalculate stale chain | PS-044, PS-073 | invalidation/rebuild | Corrected revision propagates deterministically |
| PS-075 | Rating queries/leaderboard | История и рейтинг | PS-073, PS-023 | API/pagination/tie-break | Query and ordering tests pass |

## Этап 8 — statistics и notifications

| ID | Название | Цель | Зависимости | Объём работ | Готово, когда |
|---|---|---|---|---|---|
| PS-080 | Statistics schema/projector | Быстрые player/H2H counters | PS-043, PS-065 | projections and idempotent consumer | Rebuild equals incremental result |
| PS-081 | Streaks/session records | Последовательные серии | PS-053, PS-080 | ordered projector | Current/max/session streak tests pass |
| PS-082 | Tournament statistics | Participation/titles | PS-063, PS-080 | projection updates | Completion and correction tests pass |
| PS-083 | Statistics API | Profile/read views | PS-080–PS-082, PS-075 | endpoints | PRD fields returned and authorized |
| PS-084 | Transactional outbox | Надёжные domain events | PS-003 | schema/repository/dispatcher contract | Commit/rollback/retry tests pass |
| PS-085 | Notifications | Зафиксированные MVP event types | PS-084 | friend/match/session/tournament invites, tournament start/next match, store/list/read/preferences | Idempotent delivery; business modules do not import aiogram |

## Этап 9 — завершение MVP

| ID | Название | Цель | Зависимости | Объём работ | Готово, когда |
|---|---|---|---|---|---|
| PS-090 | Security review | Устранить auth/data leaks | Все MVP | threat review, headers, rate-limit decision, secret validation | Findings closed/documented |
| PS-091 | Concurrency suite | Проверить критичные races | Все write flows | parallel DB/API tests | No duplicate sequence/progression/publish |
| PS-092 | Performance baseline | Проверить query plans | Read APIs | EXPLAIN, indexes, N+1 audit | Agreed local dataset budgets met |
| PS-093 | Documentation/runbook | Воспроизводимый локальный запуск | Все MVP | local setup, operations, recovery | Fresh-machine walkthrough succeeds without Docker |
| PS-094 | MVP acceptance | Подтвердить продуктовые сценарии | PS-090–PS-093 | full test/ruff/docs checklist | Заказчик принимает MVP |

## Следующая задача

PS-008.2b завершён и готов к приёмке. Следующую задачу не начинать автоматически; её объём задаёт
заказчик отдельной командой.
