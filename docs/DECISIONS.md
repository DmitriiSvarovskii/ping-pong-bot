# Architecture Decision Records

## ADR-001: Модульный монолит с прагматичной Clean Architecture

**Статус:** принято. **Решение:** один deployable backend с жёсткими модульными границами; domain/application не зависят от delivery/infrastructure. **Почему:** масштаб продукта не оправдывает распределённую систему, но бизнес-области достаточно различны. **Следствия:** одна БД и простые транзакции; границы контролируются импортами и публичными ports.

## ADR-002: Единый агрегат matches

**Статус:** принято, уточнено PS-006. **Решение:** standalone, сессионный и турнирный матч используют `matches`, двух participants и общий score domain; контексты — subtype tables будущих этапов. **Почему:** единая валидация и статистический источник истины. **Следствия:** контекстные orchestrators вызывают match service; нужны deferred integrity checks.

## ADR-003: Текущий результат и immutable audit исправлений

**Статус:** принято, заменяет первоначальную формулировку. **Решение:** текущий итог хранится в
`match_sets`/`matches.winner_id`; исправление атомарно заменяет текущий итог и добавляет
immutable audit row с полными previous/new JSONB-снимками. **Почему:** простое и быстрое
чтение current state при полном аудите изменений. **Следствия:** downstream modules читают current
result и используют audit rows для invalidation/rebuild; hard delete истории запрещён.

## ADR-004: История очереди как нормализованные snapshots

**Статус:** принято. **Решение:** event header + ordered items после каждого изменения. **Почему:** состояние можно восстановить и проверить, JSON не скрывает FK. **Следствия:** больше rows, но простая диагностика и честная история.

## ADR-005: Development login как локальный auth provider

**Статус:** принято, область применения уточнена в PS-008.1. **Контекст:** локальной разработке нужен вход без Telegram. **Решение:** `POST /api/v1/auth/dev-login` только в `APP_ENV=development`, без request body и выбора Telegram ID. Фиксированная identity задаётся server settings, user upsert общий, выдаётся expiring JWT. Router отсутствует в production; `get_current_user` един для всех auth providers. **Безопасность:** secret только env, `iss/aud/exp/jti`, production startup validation, 404 вне development. **Следствие:** production Telegram-auth реализован отдельно и не удаляет dev-login как локальный инструмент. **Не является:** OAuth, парольной регистрацией или production fallback.

## ADR-006: Glicko-2 как Strategy и периодический immutable calculation

**Статус:** принято. **Решение:** чистая стратегия Glicko-2, frozen period inputs и versioned results. Конфигурируемые defaults: rating 1500, RD 350, volatility 0.06, tau 0.5. Период — календарные сутки `[00:00,00:00)` UTC. **Почему:** воспроизводимость, тестовые reference vectors, будущая замена алгоритма. **Следствия:** rating публикуется после daily close; correction invalidates период и последующие и требует rebuild, а не delta patch.

## ADR-007: Явная согласованность после ретроактивных исправлений

**Статус:** принято. **Решение:** не переписывать молча последующие session matches/bracket; ставить `needs_review`, сохранять фактическую историю и создавать stale/review records. **Почему:** последующие игры реально произошли и не должны исчезать. **Следствия:** нужен отдельный согласованный resolution workflow; MVP показывает конфликт.

## ADR-008: PostgreSQL constraints плюс транзакционные locks

**Статус:** принято. **Решение:** UUID/FK/UNIQUE/CHECK/partial indexes, deferred triggers для cross-table invariants, row locks для sequence/capacity/progression. **Почему:** одной application validation недостаточно при concurrency. **Следствия:** обязательны PostgreSQL integration tests; SQLite не подходит.

## ADR-009: Статистика как rebuildable projection

**Статус:** принято. **Решение:** results/rating history — source of truth; player/H2H tables — caches. **Почему:** быстрые чтения без смешивания rating и wins. **Следствия:** consumers идемпотентны, есть rebuild command, correction перепроецируется.

## ADR-010: Transactional outbox для межмодульных побочных эффектов

**Статус:** принято. **Решение:** событие пишется с бизнес-изменением, delivery обрабатывает after commit at-least-once. **Почему:** сбой уведомления не ломает матч, событие не теряется. **Следствия:** event IDs и consumer deduplication обязательны.

## ADR-011: Только локальная инфраструктура

**Статус:** принято до финального этапа и отдельного согласования. **Решение:** зависимости — uv, FastAPI — Uvicorn, PostgreSQL — локальный сервис, bot — aiogram polling. Dockerfile, Docker Compose, Kubernetes и иная контейнерная инфраструктура не создаются, Docker не предлагается. Redis запрещён без отдельного согласования. **Почему:** явное ограничение заказчика. **Следствия:** setup-документация и тесты поддерживают прямой локальный запуск.

## ADR-012: REST semantics и idempotency

**Статус:** принято. **Решение:** `/api/v1`, cursor pagination, structured errors, idempotency keys для критичных POST, optimistic version для edits. **Почему:** безопасные retries мобильного клиента и эволюция API.

## ADR-013: Локальное автоматическое закрытие рейтинговых периодов

**Статус:** принято. **Решение:** in-process scheduler из FastAPI lifespan вызывает общий `CloseDueRatingPeriods` в 00:00 UTC и при старте. Use case под PostgreSQL advisory lock создаёт/закрывает пропущенные сутки хронологически. Development-only ручной endpoint вызывает тот же use case. **Почему:** достаточно для локального монолита без преждевременного брокера задач. **Следствия:** status/version/unique period обеспечивают идемпотентность; Celery и Redis не используются; scheduler не содержит бизнес-математику.

## ADR-014: Динамический состав активной сессии и отмена матча

**Статус:** принято, уточнено в PS-007.4. **Решение:** joining добавляет игрока в конец waiting queue; waiting participant может выйти, organizer — удалить. Current player сначала завершает матч либо использует атомарную операцию cancel-and-leave. Обычная отмена не определяет победителя, не создаёт sets/rating/win-loss, восстанавливает audited queue и сразу создаёт successor; cancel-and-leave сначала закрывает участие и создаёт successor только при наличии двух игроков. **Почему:** нельзя присуждать несыгранное поражение, терять причинность очереди или создавать бесконечную цепочку отмен. **Следствия:** если после cancel-and-leave остаётся один игрок, active-сессия приостановлена без pair/open match; следующий join возобновляет её с новым sequence number. Номер cancelled match не переиспользуется.

## ADR-015: Политика исправления результатов

**Статус:** принято. **Решение:** любой участник — в течение 24 часов после completion; organizer сесии/турнира — без срока для матчей своего контекста. Reason необязателен, но author/time и оба полных снимка обязательны. **Почему:** игрокам даётся ограниченное окно, организаторам — возможность исправить архив контекста. **Следствия:** rating correction создаёт recalculation request; winner-changing correction не разрушает downstream sequence/bracket и переводит контекст в review при конфликте.

## ADR-016: Rating seeding и walkover single elimination

**Статус:** принято. **Решение:** MVP запускается минимум с двумя участниками и использует rating snapshot; сортировка rating DESC, RD ASC, UUID, затем standard bracket seed placement и BYE. До запуска self-withdrawal разрешён, после — только organizer walkover. Walkover хранится без фиктивных sets и исключён из Glicko-2. **Почему:** воспроизводимая честная сетка и отделение технического исхода от сыгранного результата. **Следствия:** seeding оформляется Strategy для будущих random/manual реализаций; match outcome различает `played` и `walkover`.

## ADR-017: Авторизованная видимость данных

**Статус:** принято. **Решение:** anonymous API не видит player/match/tournament data. Любой зарегистрированный пользователь видит каталог, профили, rating, wins/losses, завершённые обычные матчи и завершённые открытые турниры. Закрытые турниры и их матчи — только organizer/participants. **Почему:** продукт остаётся социальным, но закрытые события сохраняют приватность. **Следствия:** visibility policy применяется ко всем query services; скрытый closed resource возвращает 404.

## ADR-018: Уведомления MVP через модуль и outbox

**Статус:** принято. **Решение:** каталог событий: friend request, match/session/tournament invitation, tournament started, next tournament match assigned. Бизнес-сервисы публикуют события, notification module сохраняет/доставляет их; aiogram — внешний adapter. **Почему:** бизнес-логика не должна зависеть от Telegram delivery. **Следствия:** типы фиксируются сейчас, реализация может идти итерациями; at-least-once consumers идемпотентны.

## ADR-019: Порядок матчей свободной сессии

**Статус:** принято. **Решение:** серверный `sequence_number` уникален внутри сессии, монотонен и не переиспользуется; timestamps не определяют порядок. Queue snapshots фиксируются для каждого join/leave/remove/start/result/cancel/resume. **Почему:** только явная последовательность позволяет воспроизводимо считать серии, непрерывное пребывание за столом и рекорды. **Следствия:** consumers сортируют session matches только по `sequence_number`.

## ADR-020: Сериализация жизненного цикла и история участия в сессии

**Статус:** принято в PS-007.2, уточнено PS-007.3/PS-007.4. **Решение:** все изменения существующей сессии сериализуются
блокировкой корня `game_sessions FOR UPDATE`; активное участие дополнительно защищено partial unique
index. Повторное присоединение после выхода создаёт новую строку участия, а не переоткрывает старую.
Каждый переход статуса и состава атомарно добавляет типизированное событие. Проверка текущего матча
скрыта за application-портом и реализована чтением общего агрегата `matches`. **Почему:** один
порядок блокировки защищает join/leave/start/cancel/complete от гонок, а append-only история
сохраняет причинность. **Следствия:** PS-007.2 не создаёт queue rows и session matches; их добавление
не меняет публичный lifecycle service, а расширяет orchestrator и обработку событий. PS-007.3
добавляет durable current pair, waiting rows и unique result-id ledger. PS-007.4 использует единый
порядок `session → current match → queue/participants`, создаёт реальные session matches и
записывает `match.id` в ledger; nested services не выполняют commit.

## ADR-021: Контекстная оркестрация session matches

**Статус:** принято в PS-007.4. **Решение:** общая score-логика выделена в транзакционно нейтральный
`MatchResultWriter`, который используют `MatchService` и `GameSessionService`. Session match нельзя
изменять standalone-командами: result/cancel выполняет session orchestrator, а correction временно
запрещён до workflow, способного учитывать последующие пары и историю очереди. На active-сессию
разрешён максимум один active match, защищённый partial unique index. **Почему:** правила счёта не
дублируются, а владение транзакцией и историческими последствиями остаётся у контекста. **Следствия:**
завершение результата, ledger, rotation, audit и successor commit/rollback происходят вместе;
исправление не может незаметно переписать последующие матчи или очередь.

## ADR-022: Telegram Mini App initData как production auth provider

**Статус:** принято и реализовано в PS-008.1. **Решение:** `POST /api/v1/auth/telegram` принимает
только исходный `Telegram.WebApp.initData`. Подпись проверяется официальной двухступенчатой схемой
HMAC-SHA256 с secret key от bot token и constant-time comparison; затем проверяются возраст
`auth_date`, допустимое будущее смещение и структура подписанного `user`. Клиентские profile fields
вне initData игнорируются. После успешной проверки общий users service выполняет атомарный
PostgreSQL upsert по `telegram_id`, а существующий token service выдаёт обычный access JWT.
**Почему:** cryptographic trust остаётся на сервере, auth provider не проникает в бизнес-модули,
а конкурентная первая авторизация не создаёт дубликаты. **Конфигурация:** bot token — secret из
environment; его отсутствие не блокирует startup, но делает Telegram endpoint недоступным с 503.
Max age конфигурируется; секреты, initData и JWT не логируются. **Следствия:** dev-login остаётся
только development-provider, модели users и защищённые endpoints не меняются; отдельная server-side
replay ledger в PS-008.1 не вводится, окно повторного использования ограничено `auth_date` и JWT TTL.

## ADR-023: Пропуск current match как отдельная атомарная команда

**Статус:** принято в PS-009.2. **Решение:** существующий cancel сохраняет прежнюю семантику, а
`skip` принимает current `match_id` и либо user ID одного current player, либо `skip_both=true`.
Session row и current match блокируются в принятом порядке; cancel, queue snapshot, audit и один
successor фиксируются одной транзакцией. Один игрок перемещается после waiting, оба — в порядке
player1, player2. **Почему:** UI должен явно выбирать уходящих со стола, не смешивая пропуск с
выходом из сессии или результатом матча. **Следствия:** cancelled match не имеет winner/rating input;
stale и concurrent retry дают 409; схема БД не меняется.

## ADR-024: Статистика сессии как вычисляемая read projection

**Статус:** принято и реализовано в PS-009.3. **Решение:** отдельный JWT-защищённый read endpoint
вычисляет wins/losses и сумму мячей за/против из завершённых матчей и сетов конкретной сессии.
Исторический состав берётся из всех participation rows, поэтому вышедшие и игроки без матчей не
исчезают. Application projector фильтрует контекст/сессию/status/completed_at/winner/sets и сортирует
по wins DESC, difference DESC, losses ASC, UUID ASC. Adapter использует bulk loading без N+1.
**Почему:** существующие нормализованные факты достаточны и дают повторяемый идемпотентный результат.
**Следствия:** чтение не пишет в БД, skip/cancel ничего не начисляют, отдельная таблица и миграция не
нужны; это не глобальная статистика и не Glicko-2.
