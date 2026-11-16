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

**Статус:** принято; session-часть уточнена ADR-041. **Решение:** не переписывать молча последующие
session matches/bracket и сохранять фактическую историю. Для session correction ADR-041 запрещает
ретроспективное изменение очереди, ротации, пар, последующих матчей и status сессии; `needs_review`
для сессии автоматически не устанавливается. Tournament bracket conflict по-прежнему использует
отдельный review workflow. **Почему:** последующие игры реально произошли и не должны исчезать.

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

**Статус:** принято и реализовано в PS-012.5; scheduler orchestration расширена ADR-040.
**Решение:** closer последовательно
переводит сохранённые due periods из `open` в `calculating`; это запечатывает inputs, но ещё не
вычисляет и не публикует rating. Каждый период использует тот же advisory lock и `FOR UPDATE`, что
intake. Несколько API workers могут запустить цикл, поскольку PostgreSQL сериализует изменение;
ошибка цикла повторяется через минуту. **Почему:** пяти минут достаточно как явное grace window для
завершений около UTC-границы, а локальному монолиту не нужен отдельный broker/leader service.
**Следствия:** restart выполняет catch-up всех существующих overdue `open` rows; повторный close не
увеличивает version. Celery/Redis и ручной HTTP endpoint не добавляются. Пустые дни без period row
этот этап не материализует.

## ADR-014: Динамический состав активной сессии и отмена матча

**Статус:** принято, уточнено в PS-007.4. **Решение:** joining добавляет игрока в конец waiting queue; waiting participant может выйти, organizer — удалить. Current player сначала завершает матч либо использует атомарную операцию cancel-and-leave. Обычная отмена не определяет победителя, не создаёт sets/rating/win-loss, восстанавливает audited queue и сразу создаёт successor; cancel-and-leave сначала закрывает участие и создаёт successor только при наличии двух игроков. **Почему:** нельзя присуждать несыгранное поражение, терять причинность очереди или создавать бесконечную цепочку отмен. **Следствия:** если после cancel-and-leave остаётся один игрок, active-сессия приостановлена без pair/open match; следующий join возобновляет её с новым sequence number. Номер cancelled match не переиспользуется.

## ADR-015: Политика исправления результатов

**Статус:** заменено для standalone/session решением ADR-041; tournament policy остаётся отдельной.
Историческая формулировка разрешала correction любому участнику в течение 24 часов. Актуальное
утверждённое правило: standalone — только `created_by` в течение 24 часов, session — только organizer
сессии без срока. Reason необязателен, но author/time и оба полных снимка обязательны. Rating
correction атомарно создаёт recalculation records; downstream session history не переписывается.

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

## ADR-025: Централизованный Telegram viewport controller

**Статус:** принято и реализовано в PS-010.1. **Решение:** frontend bootstrap владеет единственным
ref-counted controller для Telegram viewport. Он один раз выполняет поддерживаемые imperative calls
`disableVerticalSwipes()` и mobile-only `requestFullscreen()`/`expand()`, подписывается на viewport и
оба safe-area event и `activated`, публикуя текущие значения через CSS variables. Последний consumer
снимает все подписки; повторный mount может подписаться снова, не повторяя fullscreen-запрос. Layout
складывает device safe area и Telegram content safe area внутри каждого источника, затем берёт
максимум между browser env, Telegram CSS variables и bridge values. Так разные защитные слои не
теряются, а одинаковые данные разных источников не суммируются повторно. **Почему:**
системный chrome и viewport меняются независимо от React render и клавиатуры, а Telegram API не
должен распространяться по страницам. **Следствия:** браузер без bridge остаётся работоспособным,
нативная iOS-прокрутка сохраняется, глобальный touch `preventDefault()` не используется; Telegram
по-прежнему может закрыть или свернуть Mini App жестом на собственной верхней панели.

Навигация Telegram также принадлежит общему `AppShell`, а не отдельным страницам. Корневые маршруты
явно скрывают BackButton; вложенные используют существующую history роутера и логический fallback
только при прямом входе. `offset` в URL сохраняется как начальная точка старой прямой ссылки, но
автоматическая подгрузка истории не изменяет URL. Scroll position сохраняется по ключу history entry,
отдельный navigation stack не создаётся. Это сохраняет экран списка при возврате и не накапливает
bridge-handlers или повторные записи активной нижней вкладки.

## ADR-026: Одноразовый WebSocket ticket и single-process realtime manager

**Статус:** принято и реализовано в PS-010.3.1. **Решение:** access JWT используется только для
защищённой HTTP-выдачи opaque ticket. Ticket связан с user/session, имеет короткий TTL, хранится как
SHA-256 digest и атомарно consume-ится один раз. Browser передаёт его в `Sec-WebSocket-Protocol`
рядом с публичным `pingstats.v1`; URL остаётся без credentials, сервер не отражает ticket обратно.
После consume доступ повторно проверяется короткой SQLAlchemy session, закрытой до accept. Sockets
группирует in-memory manager одного Uvicorn worker. **Почему:** browser WebSocket API не позволяет
задать Authorization header, а query credentials попадают в access logs/history; Redis и новая
таблица для текущего single-worker deployment избыточны. **Следствия:** restart инвалидирует tickets
и sockets; multi-worker требует общего ticket/replay store и Pub/Sub. Business services не
импортируют manager; транспорт подключён через application-порт.

## ADR-027: Best-effort post-commit invalidation для игровых сессий

**Статус:** принято и реализовано в PS-010.3.2. **Решение:** `GameSessionService` регистрирует в UoW
keyed callback через application-порт `SessionUpdatePublisher`. UoW выполняет callback только после
успешного SQL commit, удаляет при rollback и дедуплицирует по `(session.updated, session_id)` в одной
команде. WebSocket adapter публикует минимальный invalidation через существующий manager; transport
ошибка журналируется и не меняет committed response. **Почему:** отправка до commit создаёт ложные
обновления, а отправка внутри транзакции удерживает PostgreSQL lock на сетевой операции. Для UI
достаточно best-effort сигнала с REST как source of truth. **Следствия:** validation/conflict/replay
не публикуют; нет фоновых задач и дополнительных транзакций. Механизм не является durable delivery
и не заменяет ADR-010 transactional outbox. Restart или single-process failure может потерять
invalidation; multi-worker потребует согласованного Pub/Sub.

## ADR-028: Персональная подписка списка сессий по истории участия

**Статус:** принято и реализовано в PS-010.3.3. **Решение:** list WebSocket использует отдельный
ticket scope и отдельную user-keyed область существующего connection manager. После lifecycle или
membership commit `SessionsUpdatePublisher` отправляет `sessions.updated` только UUID пользователей
из существующей historical participation выборки. Payload не содержит user/session IDs. **Почему:**
REST `list_for_user` уже определяет видимость через факт участия; глобальное событие раскрывало бы
активность недоступных сессий. В текущей модели нет public/open session mode, поэтому новая политика
видимости не вводится. **Следствия:** вышедший исторический участник продолжает получать invalidation
доступной ему записи, outsider не получает ничего. Result/cancel-match/skip не меняют list card и не
публикуются. Ticket store, post-commit callbacks и single-process ограничения ADR-026/027 сохранены.

## ADR-029: WebSocket как ускоритель REST с lifecycle-aware fallback

**Статус:** принято и реализовано в PS-010.4.1. **Решение:** frontend controller владеет одним
WebSocket на consumer, получает новый one-use ticket на каждую попытку, проверяет negotiated
subprotocol и использует capped exponential backoff с jitter. При нездоровом канале один REST
fallback interval обеспечивает eventual refresh; successful open останавливает polling и запускает
full REST sync. Hidden/offline/logout/unmount отменяют ticket и refetch callbacks через AbortSignal,
закрывают socket и очищают timers/listeners. **Почему:** WebSocket best-effort и не является source
of truth, а Telegram/iOS регулярно замораживает приложение и сетевые соединения. **Следствия:**
heartbeat сам по себе не инвалидирует данные; неизвестные/повреждённые сообщения игнорируются.
401/403, session-ticket 404 и close 4401/4403/4404 не создают бесконечный reconnect. List-ticket
404 считается временной ошибкой совместимости deployment. Page/query integration выполняется
отдельно, без изменения transport controller.

## ADR-030: Realtime списка инвалидирует только активную REST-проекцию

**Статус:** принято и расширено в PS-011.7. **Решение:** `/games` включает персональный list channel
только при готовой авторизации. `sessions.updated`, reconnect и fallback coalesce-ятся: list-prefix
помечается stale, но refetch выполняется только для текущего infinite TanStack Query key и всех уже
загруженных страниц; initial offset и кешированные данные не сбрасываются. Fullscreen loader
применяется только при первой загрузке, а background error не скрывает ранее полученный список.
**Почему:** WebSocket сообщает только факт изменения, REST
остаётся source of truth, а несколько близких lifecycle/event сигналов не должны создавать storm
запросов. **Следствия:** heartbeat, неизвестный и повреждённый payload не вызывают refetch;
logout/unmount отменяет consumer через AbortSignal. Другие страницы и session channel этим решением
не затронуты.

## ADR-034: Offset-compatible infinite history

**Статус:** принято и реализовано в PS-011.7. **Решение:** список `/games` использует
`useInfiniteQuery` с серверным `limit=20`; legacy `?offset=N` является `initialPageParam`, а
`IntersectionObserver` с опережающей зоной загружает следующие offset-pages без записи в browser
history. Страницы объединяются в исходном порядке и дедуплицируются по session UUID. Один in-flight
guard исключает повторный `fetchNextPage`; observer отключается при unmount или после достижения
`total`. **Почему:** backend уже возвращает стабильную offset-пагинацию и total, поэтому новый API и
cursor не нужны. URL не должен меняться от прокрутки, иначе Telegram BackButton и history entries
становятся непредсказуемыми. **Следствия:** возврат использует сохранённые pages TanStack Query и
существующую scroll position по location key. Offset pagination допускает сдвиг границ при новых
записях, поэтому realtime refetch перечитывает все загруженные страницы, а UI дополнительно удаляет
дубликаты. Infinite-list query живёт до завершения frontend runtime (`gcTime: Infinity`), чтобы
длительный открытый detail route не удалил состояние исходного списка; query key включает user UUID,
поэтому cache разных пользователей изолирован. Память ограничена страницами, которые пользователь
действительно подгрузил.

## ADR-035: Build manifest и явное безопасное обновление frontend

**Статус:** принято и реализовано в PS-011.8. **Решение:** Vite генерирует уникальный build ID на
каждый build, встраивает его в JS и выпускает same-origin `/version.json`. Frontend проверяет manifest
с cache bypass при старте, каждые 15 минут и при `visibilitychange` в visible. Отличающийся ID только
показывает banner; reload выполняется пользователем. Содержательный draft `(user UUID, match UUID)`
требует дополнительного inline-подтверждения и не удаляется. **Почему:** автоматический reload может
прервать матч, а повторная Telegram initData-аутентификация через длительное время невозможна;
существующие HttpOnly refresh credential и local draft уже обеспечивают безопасное восстановление.
**Следствия:** `index.html` и `version.json` должны отдаваться с `no-store/no-cache`, hashed assets —
с immutable year cache. Ошибка manifest/check не блокирует приложение. Проверка является polling
только версии, не данных, и использует один 15-минутный timer на смонтированный AppShell.

## ADR-031: Session realtime заменяет постоянный polling

**Статус:** принято и реализовано в PS-010.4.3. **Решение:** detail и result routes используют общий
session-scoped consumer, но каждый route монтирует только собственное соединение. Matching
`session.updated`, reconnect и fallback coalesce-ятся в invalidation session-prefix; refetch
выполняется только для активных TanStack Query observers. Постоянный четырёхсекундный polling
удалён. **Почему:** backend event уже сигнализирует каждое committed изменение, а transport имеет
контролируемый 15-секундный fallback на время недоступности socket. Параллельный polling создавал бы
лишние запросы и гонки UI. **Следствия:** REST остаётся source of truth; cached state сохраняется при
background error. React editor remount привязан к `(match.id, version)`, а persisted draft — к
`(user UUID, match UUID)`: новая revision того же матча восстанавливает его, successor использует
другой ключ. HTTP 409 блокирует submit и остаётся обязательным recovery для гонки между последним
refetch и записью.

## ADR-032: Явная WebSocket runtime-зависимость Uvicorn

**Статус:** принято после deployment-аудита PS-010. **Решение:** добавить `websockets>=13.0` как
прямую runtime-зависимость и проверять через unit-тест, что Uvicorn `ws=auto` выбирает доступную
protocol implementation. Не использовать `uvicorn[standard]`, поскольку его дополнительные
`uvloop`, `httptools`, `watchfiles` и прочие пакеты не требуются для устранения blocker. **Почему:**
базовый пакет Uvicorn не устанавливает `websockets` или `wsproto`, поэтому HTTP сервер запускается,
но WebSocket Upgrade недоступен. **Следствия:** production обязан выполнить
`uv sync --frozen --no-dev` до restart; версия фактически фиксируется `uv.lock`.

## ADR-033: HttpOnly refresh credential и локальный draft результата

**Статус:** принято и реализовано в PS-011.4. **Решение:** часовой access JWT остаётся в памяти и
автоматически восстанавливается через `POST /auth/refresh`. Долгоживущий JWT типа `refresh`
устанавливается только после Telegram/dev login как host-only `HttpOnly`, `SameSite=Strict`,
production-`Secure` cookie с ограниченным configurable lifetime. Refresh дополнительно требует
same-origin custom header. Frontend coalesce-ит параллельные 401 в один refresh и повторяет каждый
отклонённый auth dependency запрос ровно один раз. Draft результата хранится локально по
`(user UUID, match UUID)` и удаляется после успешной записи либо подтверждённой смены current match.
**Почему:** Telegram `initData` действительно только пять минут, поэтому через час оно уже не может
быть безопасной renewal credential; увеличение access TTL расширяло бы ущерб от утечки. 401
возникает в auth dependency до бизнес-команды, поэтому однократный retry не дублирует mutation.
**Следствия:** миграция и server draft store не нужны; refresh-cookie недоступна JavaScript и не
логируется. Logout endpoint сейчас отсутствует. Stateless refresh нельзя выборочно отозвать до expiry
без смены JWT secret; нельзя утверждать, что удаление cookie отзовёт уже скопированный token. Если
появятся logout нескольких устройств или security-session management, потребуется server-side
hashed refresh-session store и rotation/revocation.

## ADR-036: Атомарный intake рейтингового результата

**Статус:** принято и реализовано в PS-012.4. **Решение:** после завершения ranked standalone или
session match application orchestrator вызывает `RatingMatchInputRecorder` до commit через
репозитории того же UoW. Recorder выбирает календарный UTC-период по `completed_at`, берёт общий с
будущим closer transaction-level PostgreSQL advisory lock по UTC-дню, создаёт либо блокирует
`rating_period` и принимает input только при `status=open`. PK периода/version/match и сравнение
`source_match_version` с каноническим SHA-256 fingerprint превращают точный retry в no-op, а
расхождение — в явный conflict без перезаписи. **Почему:** match result, session rotation и frozen
rating input должны иметь одну границу атомарности; отдельная транзакция или after-commit доставка
могли бы потерять input либо записать его после публикации периода. **Следствия:** lock order остаётся
`match/session` до period advisory lock; реализованный PS-012.5 closer использует тот же lock
protocol. Active, cancelled, unranked и структурно неполные результаты input не создают. Corrections,
rebuild, вычисление и публикация числового rating не входят в intake.

## ADR-037: Исторический source state и неперсистентный расчёт периода

**Статус:** принято и реализовано в PS-012.6.1. **Решение:** `RatingPeriodCalculator` рассчитывает
только период `calculating` и возвращает deterministic DTO, не записывая results и не меняя
`ratings`/period status. Исходные состояния берутся из полного `rating_period_results` ближайшего
предыдущего периода; current rating snapshot намеренно не читается. Первый период и новые участники
получают initial state из parameter snapshot. Все предыдущие rated players входят в следующий
расчёт, поэтому неактивным применяется стандартный рост RD. **Почему:** Glicko-2 использует
одновременные состояния начала периода, а current snapshot может быть сформирован позже target
period. UUID/timestamp persistence result сделал бы чистый повтор недетерминированным и преждевременно
смешал calculation с publish boundary. **Следствия:** отсутствие/неполнота предыдущего result set или
незавершённый предыдущий период дают controlled conflict и запрещают расчёт вне хронологической
очереди. PS-012.6.2 должен атомарно преобразовать DTO в versioned `rating_period_results`, обеспечить
идемпотентность хранения и только затем определить publish workflow.

## ADR-038: Completion marker атомарного расчёта рейтингового периода

**Статус:** принято и реализовано в PS-012.6.2.1. **Решение:** nullable
`rating_periods.calculated_version` является единственным признаком полного сохранения calculation
snapshot; расчёт завершён только при его равенстве текущему `calculation_version`.
`RatingPeriodResultPersister` под общим period advisory lock и `FOR UPDATE` вычисляет DTO через
существующий calculator, детерминированно добавляет все `rating_period_results` и обновляет marker в
одной транзакции. Повтор сравнивает полный business snapshot; совпадение является no-op, любое
расхождение или result rows без marker — controlled conflict без перезаписи. **Почему:** наличие хотя
бы одной result row не отличает полный набор от частичного и не может представить успешно
рассчитанный пустой период. Отдельный статус периода преждевременно усложнил бы state machine.
**Следствия:** rollback атомарно убирает строки и marker; constraint допускает только NULL либо
`0 < calculated_version <= calculation_version`. Смена calculation version автоматически делает
старый marker неактуальным. Этот этап не меняет `ratings`, `published_at` или status и не является
публикацией.

## ADR-039: Глобально сериализованная атомарная публикация рейтинга

**Статус:** принято и реализовано в PS-012.6.2.2. **Решение:** `RatingPeriodPublisher` читает только
сохранённый snapshot с `calculated_version = calculation_version`. Перед period advisory lock и
`FOR UPDATE` он берёт отдельный глобальный transaction-level publication advisory lock. В одной
транзакции publisher проверяет хронологию и целостность snapshot, создаёт/обновляет все `ratings`,
затем переводит период в `published` и устанавливает UTC `published_at`. **Почему:** lock одного
периода не защищает общие player rating rows от одновременной публикации разных периодов; глобальный
lock является минимальной надёжной координацией без новой таблицы или инфраструктуры. **Следствия:**
publication throughput сознательно равен одному периоду за раз, что соответствует суточной модели.
Точный повтор опубликованной версии — no-op с неизменным `published_at`; старая версия, разрыв
хронологии, несовпадение rating-before/current state или повреждённый snapshot дают controlled
conflict и полный rollback. С PS-012.6.3 scheduler запускает полный workflow согласно ADR-040.

## ADR-040: Restart-safe orchestration рейтингового workflow

**Статус:** принято и реализовано в PS-012.6.3. **Решение:** process-local
`RatingPeriodScheduler` из FastAPI lifespan запускает единый `RatingWorkflowOrchestrator` при старте
и ежедневно в `00:05 UTC`. Orchestrator переиспользует closer, persister и publisher, сохраняя их
отдельные transaction boundaries, обрабатывает `calculating` periods строго по `starts_at` и после
каждого этапа перечитывает durable state. **Почему:** status и completion marker уже являются
достаточным checkpoint; дополнительная workflow-таблица или distributed worker не нужны. Несколько
Uvicorn workers безопасны благодаря существующим PostgreSQL locks и идемпотентным повторам.
**Следствия:** падение после close или persistence продолжается с следующего незавершённого этапа;
ошибка раннего периода блокирует поздние. Инфраструктурная ошибка получает retry через 60 секунд,
business/data conflict — только следующий плановый запуск и структурированный error log. Scheduler
не материализует отсутствующие дни и не выполняет corrections/rebuild.

## ADR-041: Политики correction и отдельное аннулирование завершённого матча

**Статус:** принято и реализовано в PS-012.7.5. **Решение:** standalone result исправляет только organizer
`matches.created_by` в течение 24 часов; session result — только organizer сессии без ограничения
времени, включая completed session. Число corrections не ограничено, каждая создаёт immutable
revision; участники и `completed_at` не меняются. Session correction не создаёт матч, не меняет
очередь, ротацию, пары, последующие матчи или status сессии. Ранее completed match может быть
аннулирован отдельным состоянием `voided`, не равным cancellation незавершённого матча; физическое
удаление запрещено. **Почему:** organizer является владельцем контекста, а фактически сыгранная
последовательность не должна ретроспективно переписываться. **Следствия:** текущая correction policy
«любой standalone participant» и запрет session corrections должны быть заменены. `ResultSnapshot`
расширяется outcome, nullable winner и immutable `completed_at`; voided match исключается из rating
и played/win/loss projections. Аннулировать completed match может organizer без ограничения времени:
как standalone, так и session match, в том числе после завершения сессии. С PS-012.7.6 rated
correction/void разрешены при наличии исходного immutable rating input и атомарно координируются с
versioned inputs либо persistent rebuild; отсутствие input даёт controlled conflict и полный
rollback общей транзакции.

## ADR-042: Shadow rebuild и атомарное переключение rating history

**Статус:** полностью реализовано и проверено PS-012.7.4–PS-012.7.10: infrastructure,
coordination/barrier, shadow calculation, atomic cutover, orchestration и PostgreSQL concurrency.
**Решение:** добавлены
`rating_periods.published_version`, persistent singleton `rating_rebuild_jobs`, per-period
`rating_rebuild_periods` и связь recalculation requests с job. Active history читается только по
`published_version`; монотонные `calculation_version` готовят shadow inputs/results, а
`calculated_version` подтверждает полноту. Одновременно активен один rebuild, новая correction может
supersede его. Horizon содержит существующие non-open зависимые periods; новые open periods и intake
продолжают работать. Cutover одним commit переключает все active versions, current ratings и job,
при этом игроки без оставшейся rated history удаляются из current `ratings`. **Почему:** in-place
republication старых периодов временно показала бы смешанную историю и могла затереть более свежий
rating. **Следствия:** первоначальный `published_at` сохраняется, cutover time хранится в job;
Normal publisher теперь атомарно устанавливает `published_version` и читает predecessor через active
version. Partial unique index считает `failed` незавершённым, поэтому ошибка не освобождает durable
barrier; только `completed`/`superseded` допускают новый job. Persister и publisher проверяют
barrier внутри global publication/period locks, а workflow останавливает проход без интенсивного
retry. Partial shadow branch недоступна Rating API. Crash продолжается с checkpoints, failed job
имеет явный manual retry через внутреннюю CLI-команду. Redis/Celery/broker не добавляются. Nullable
request→job хранит текущее назначение; если
потребуется полная история переназначений, понадобится отдельное append-only решение.

Shadow executor использует зарезервированные target versions, строит inputs из актуального match и
последней revision, исключает voided match и рассчитывает periods строго последовательно. Первый
period опирается на предыдущую active published version, последующие — на shadow results
предыдущего checkpoint. Результаты и checkpoint атомарны на период; job становится `ready` только
после полной ветки. Это не является cutover: active versions/current ratings не переключаются.

Cutover принимает только актуальный `ready` job с полным набором ready checkpoints и completion
markers. В одной транзакции он активирует target versions, восстанавливает current ratings и
cumulative match count из immutable inputs, завершает requests/job и тем самым освобождает barrier
только после commit. Ранее published timestamps сохраняются; unpublished periods впервые
публикуются временем cutover. Ошибка оставляет прежнюю published branch доступной, а повторный
вызов completed job безопасно возвращает завершённое состояние.

Один существующий in-process scheduler сначала обслуживает rebuild, затем normal workflow. Новый job
будит scheduler after commit в создавшем процессе; остальные workers и рестарт обнаруживают durable
job периодическим PostgreSQL polling с интервалом до 30 секунд. Business failure фиксирует job как
`failed` и удерживает barrier; автоматического retry нет. Инфраструктурные сбои получают ограниченный
backoff и затем degraded interval. Безопасный ручной retry — внутренняя CLI-команда, переводящая
только текущий active failed job в `preparing`; checkpoints и target versions не пересоздаются.
Отдельный worker, broker, Redis и Celery не вводятся.

## ADR-043: Calendar inactivity без materialization пустых периодов

**Статус:** принято и реализовано в PS-012.7.3. **Решение:** rating period остаётся календарными UTC-
сутками, но пустые rows не создаются. Для algorithm version `2` calculator определяет число пропусков
как `UTC-date(current.starts_at) - UTC-date(previous.ends_at)` и применяет к ранее рейтингованным
игрокам эквивалентную многошаговую inactivity evolution:
`phi_gap = sqrt(phi_before² + missing_periods × volatility²)`, с ограничением `max_rd`. Rating и
volatility не меняются. Затем обычный calculation текущего периода применяется ровно один раз;
соперники используют те же calendar-adjusted working states. Новый игрок получает initial state без
inactivity до первого участия. **Почему:** sparse реализация version `1` недооценивала RD после
календарных разрывов, а ежедневные snapshots всех игроков создавали бы лишний объём. Прямая формула
эквивалентна последовательным пустым периодам, включая cap RD.

**Совместимость:** `rating_before`, `rd_before`, `volatility_before` сохраняют исходный previous
snapshot; промежуточное adjusted state существует только в calculator, поэтому publisher contract
не меняется. Version `1` сохраняет legacy semantics, version `2` назначается новым periods. Уже
рассчитанные snapshots не переинтерпретируются и автоматически не пересчитываются. Правило version
`2` обязательно переиспользовать в normal calculation, rebuild и backfill. Пример 1 ноября →
10 ноября учитывает восемь пропусков 2–9 ноября, но не удваивает 10 ноября. Политика read-time RD без
новой publication утверждена отдельно в ADR-044.

## ADR-044: Read-only effective RD и согласованное чтение рейтинга

**Статус:** принято и реализовано в PS-012.8. **Решение:** Rating API всегда возвращает stored
rating/RD/volatility последнего active published snapshot и отдельный `effective_rd`. Projection
применяет существующий `Glicko2Strategy.advance_inactivity` к stored state по числу полностью
завершённых UTC-суток после `as_of_period_ends_at`; незавершённые текущие сутки не учитываются.
Параметры берутся из immutable snapshot соответствующего периода. Projection не записывается в БД,
не меняет volatility/rating и сопровождается `inactivity_periods`.

History SQL выбирает только `calculation_version = published_version`. Current state и history
читаются под transaction-level shared advisory publication lock, совместимым с exclusive lock
publisher/cutover. Поэтому concurrent atomic cutover не раскрывает shadow branch и не создаёт
смешанный response. **Почему:** клиенту нужна актуальная оценка неопределённости между публикациями,
но published snapshots должны оставаться воспроизводимыми и immutable. **Следствия:** игрок без
published history получает 404 current rating и пустую history page; read не создаёт фиктивный
rating. Effective RD является projection, а не опубликованным значением и не используется как
predecessor будущего расчёта.

С PS-012.9 тот же lock и projection используются глобальным leaderboard. Допуск не вводит новых
порогов: участвует каждая строка current `ratings`, связанная с published period. Уникальная позиция
вычисляется PostgreSQL как `row_number()` по rating DESC, RD ASC, `rated_matches_count` DESC и UUID
ASC. Профиль присоединяется в page query без N+1, а position текущего пользователя вычисляется
отдельно от pagination в рамках той же read transaction. Поэтому leaderboard также видит целиком
старое либо новое состояние atomic cutover.

## ADR-045: Historical rating backfill через обычный workflow

**Статус:** принято и реализовано в PS-012.10. **Решение:** backfill не имеет отдельного
математического или publication pipeline. Внутренняя CLI-команда выбирает current snapshots
исторических `completed + ranked` матчей, записывает их существующим intake recorder и после commit
запускает calculator/persister/publisher только для затронутых UTC-периодов. Новые periods используют
актуальную algorithm version `2`; календарные разрывы обрабатывает ADR-043 без materialization пустых
суток. Latest correction revision сохраняется в input, `voided`, `cancelled` и unranked исключаются.

Весь intake запуска атомарен под global publication lock и согласованным period lock order.
Идентичные inputs пропускаются, поэтому повтор после успешного intake или частичного workflow
безопасен. Активный rebuild блокирует процедуру. Published snapshot никогда не меняется напрямую:
missing/mismatched historical input sealed периода или попытка создать период перед опубликованной
веткой завершает команду controlled conflict. **Почему:** автоматическое создание correction/rebuild
без пользовательской revision исказило бы audit provenance. **Следствия:** такой конфликт требует
отдельного согласованного rebuild/import процесса; текущий backfill намеренно fail-closed.
Матч текущих UTC-суток при apply создаёт только input в `open` period: backfill не обходит штатное
закрытие в 00:05 UTC, не создаёт ранний snapshot и не меняет current rating.
