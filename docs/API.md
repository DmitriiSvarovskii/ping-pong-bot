# Предварительный HTTP и WebSocket API

Base path: `/api/v1`. JSON, UTC timestamps ISO 8601, UUID strings. Все endpoints с данными игроков, матчей и турниров требуют Bearer JWT и единый `get_current_user`; публичного anonymous read API в MVP нет. Реализованные users-списки используют `?offset=&limit=` (default 20, max 100); cursor pagination может быть введена позднее без изменения write API.

## 1. Общие контракты

Ошибка:

```json
{"error":{"code":"match_invalid_score","message":"...","details":{},"request_id":"..."}}
```

Критичные повторяемые команды будущих модулей могут принимать `Idempotency-Key`.
В matches PS-006 lost update предотвращает `expected_version`: он обязателен для добавления
партии и исправления, опционален для полной записи. Конфликт — 409. Validation — 422,
неаутентифицированный запрос — 401, forbidden — 403, not found — 404.

Технические endpoints:

| Method | Path | Назначение |
|---|---|---|
| GET | `/health` | Process health без обращения к PostgreSQL; доступен во всех окружениях |
| GET | `/health/db` | Только development; выполняет `SELECT 1`, возвращает 200 либо безопасный 503; в production отсутствует |

## 2. Auth и users

| Method | Path | Назначение |
|---|---|---|
| POST | `/auth/telegram` | Реализовано; проверяет подписанный Telegram Mini App `initData`, синхронизирует user, возвращает `{access_token, token_type, expires_in}` и устанавливает refresh-cookie |
| POST | `/auth/refresh` | Реализовано; по `HttpOnly`, `SameSite=Strict` refresh-cookie выдаёт новый access JWT без повторного `initData` |
| POST | `/auth/dev-login` | Реализовано; только development, body отсутствует; get-or-create фиксированного `DEV_USER_ID`; `{access_token, token_type, expires_in, user}`. В production route отсутствует (404) |
| GET | `/users/me` | Реализовано; текущий профиль, включая собственный nullable `telegram_id` |
| GET | `/users` | Реализовано; каталог, `limit` 1…100 и `offset >= 0` |
| GET | `/users/search` | Реализовано; `q` 1…100 символов, `limit`, `offset`; имя/фамилия/username |
| GET | `/users/{user_id}` | Публичный профиль |
| GET | `/users/{user_id}/matches` | История матчей |
| GET | `/users/{user_id}/rating-history` | История rating/RD/volatility |
| GET | `/users/{user_id}/statistics` | Статистика |
| GET | `/users/{user_id}/head-to-head/{opponent_id}` | Личные встречи |

Telegram login request:

```json
{"init_data":"query_id=...&user=...&auth_date=...&hash=..."}
```

Успешный ответ —
`200 {"access_token":"...","token_type":"bearer","expires_in":3600}`. Endpoint принимает только
исходную подписанную строку из `Telegram.WebApp.initData`; отдельные `user_id`, username и profile
fields не считаются доверенными. Неверная подпись, структура, устаревший или недопустимо будущий
`auth_date` возвращают безопасный 401. Если `TELEGRAM_BOT_TOKEN` не настроен, возвращается 503 без
раскрытия конфигурации. Endpoint доступен во всех окружениях независимо от dev-login.

Успешный Telegram/dev login устанавливает host-only cookie `pingstats_refresh_token` с атрибутами
`HttpOnly`, `SameSite=Strict`, `Path=/api/v1/auth` и `Secure` в production. Cookie содержит
подписанный JWT типа `refresh`, не доступна JavaScript и живёт
`JWT_REFRESH_TOKEN_EXPIRE_DAYS` (default 30 дней). `POST /auth/refresh` не принимает request body,
требует same-origin header `X-PingStats-CSRF: 1`, не использует устаревший `initData` и возвращает
новый часовой access token. Отсутствующая, просроченная, неверно подписанная cookie или token другого
типа дают безопасный 401; отсутствие CSRF header даёт 403. Auth responses используют
`Cache-Control: no-store`.

`GET /users/{user_id}` реализован и не раскрывает `telegram_id`/`updated_at`. Остальные user history
маршруты в таблице являются планом будущих этапов. JWT обоих auth providers использует единый
`get_current_user`, поэтому защищённый API не зависит от способа входа.

## 3. Друзья

| Method | Path | Назначение |
|---|---|---|
| POST | `/friends/requests` | Реализовано; `{receiver_id}`; создать pending, ответ 201 |
| GET | `/friends/requests/incoming` | Реализовано; входящая история, `limit`/`offset` |
| GET | `/friends/requests/outgoing` | Реализовано; исходящая история, `limit`/`offset` |
| POST | `/friends/requests/{request_id}/accept` | Реализовано; только receiver, атомарно создаёт friendship |
| POST | `/friends/requests/{request_id}/reject` | Реализовано; только receiver |
| POST | `/friends/requests/{request_id}/cancel` | Реализовано; только sender |
| GET | `/friends` | Реализовано; взаимные друзья с профилями, `limit`/`offset` |
| DELETE | `/friends/{friend_id}` | Реализовано; взаимное удаление, ответ 204 |

Все маршруты требуют Bearer JWT. Ответы списков и команд содержат публичный профиль второго
пользователя, но не раскрывают его `telegram_id`. Ожидаемые ошибки используют общий envelope:
400 для self-request, 403 для неверной роли, 404 для отсутствующего ресурса/пользователя и 409
для существующей pending-заявки, дружбы или уже завершённой заявки.

## 4. Матчи

Создание обычного матча:

```json
{
  "invited_user_id":"uuid",
  "points_to_win":11,
  "sets_to_win":3,
  "is_ranked":true
}
```

Одна партия:

```json
{"player1_score":11,"player2_score":8,"expected_version":2}
```

Полный результат:

```json
{
  "sets":[{"player1_score":11,"player2_score":8},{"player1_score":11,"player2_score":7},{"player1_score":11,"player2_score":6}],
  "expected_version":2
}
```

В `PATCH /results` то же тело обязательно содержит `expected_version` и может содержать
`reason` до 1000 символов. `win_by=2` пока зафиксирован для standalone и возвращается в response.

| Method | Path | Назначение |
|---|---|---|
| POST | `/matches` | Реализовано; создать `standalone/pending`, 201 |
| GET | `/matches` | Реализовано; матчи участника, filters `status`/`context`/`is_ranked`, `limit`/`offset` |
| GET | `/matches/{id}` | Реализовано; детали, участники, текущие sets и version |
| POST | `/matches/{id}/accept` | Принять приглашение |
| POST | `/matches/{id}/reject` | Отклонить приглашение |
| POST | `/matches/{id}/cancel` | Отменить pending или active standalone-матч без записанных sets |
| POST | `/matches/{id}/sets` | Добавить следующую партию; `expected_version` обязателен |
| PUT | `/matches/{id}/results` | Записать весь завершённый результат; идентичный retry идемпотентен |
| PATCH | `/matches/{id}/results` | Исправить completed-матч в 24-часовом окне; добавить audit revision |
| GET | `/matches/{id}/revisions` | Аудит исправлений |

## 5. Игровые сессии

| Method | Path | Назначение |
|---|---|---|
| POST | `/game-sessions` | Реализовано; создать pending-сессию, organizer добавляется участником; ответ 201 |
| GET | `/game-sessions` | Реализовано; история сессий пользователя, `limit`/`offset` |
| GET | `/game-sessions/{session_id}` | Реализовано; настройки, lifecycle и active participants |
| POST | `/game-sessions/{session_id}/join` | Реализовано; join в pending либо waiting tail в active; paused-сессия возобновляется вторым игроком |
| POST | `/game-sessions/{session_id}/leave` | Реализовано; waiting/pending participant; current player получает 409 |
| POST | `/game-sessions/{session_id}/start` | Реализовано; organizer запускает сессию и создаёт match №1 |
| POST | `/game-sessions/{session_id}/complete` | Реализовано; organizer завершает active-сессию и отменяет current match без поражения |
| POST | `/game-sessions/{session_id}/cancel` | Реализовано; organizer отменяет pending-сессию |
| GET | `/game-sessions/{session_id}/participants` | Реализовано; `active_only=true`, `limit`/`offset` |
| GET | `/game-sessions/{session_id}/queue` | Реализовано; current pair, waiting и `is_paused` |
| GET | `/game-sessions/{session_id}/matches` | Реализовано; история строго по `sequence_number`, включая cancelled |
| GET | `/game-sessions/{session_id}/statistics` | Реализовано; read-only лидерборд текущей сессии по завершённым матчам |
| GET | `/game-sessions/{session_id}/matches/current` | Реализовано; current active match либо 404 |
| POST | `/game-sessions/{session_id}/matches/current/sets` | Реализовано; добавить партию текущего матча |
| PUT | `/game-sessions/{session_id}/matches/current/result` | Реализовано; записать полный результат |
| POST | `/game-sessions/{session_id}/matches/current/cancel` | Реализовано; organizer/current player отменяет и получает successor |
| POST | `/game-sessions/{session_id}/matches/current/cancel-and-leave` | Реализовано; current player атомарно отменяет матч и выходит |
| POST | `/game-sessions/{session_id}/matches/current/skip` | Реализовано; отменить current match и переместить первого, второго либо обоих игроков в хвост |
| POST | `/game-sessions/ws-ticket` | Реализовано; Bearer JWT, одноразовый ticket для персонального списка, ответ 201 |
| WS | `/game-sessions/ws` | Реализовано; персональный канал invalidation списка «Мои игры» |
| POST | `/game-sessions/{session_id}/ws-ticket` | Реализовано; Bearer JWT, одноразовый session-scoped ticket с коротким TTL, ответ 201 |
| WS | `/game-sessions/{session_id}/ws` | Реализовано; authenticated invalidation channel без полных session payloads |

Create body: `SessionCreateRequest` с nullable `name`, положительными `points_to_win` и
`sets_to_win`, `is_ranked`. `SessionResponse` не раскрывает audit payload и содержит только
настройки, lifecycle/version и active participants с публичными профилями.

Каждая команда current match обязательно принимает server-issued `match_id` в body. Добавление
партии принимает `player1_score`, `player2_score`, обязательный `expected_version`; полный результат
принимает непустой `sets` и опциональный `expected_version`. `winner_id` клиент не передаёт.
Контекстный service под session/match locks сверяет `match_id`, применяет общий `ScoreValidator` и
возвращает `SessionMatchCommandResponse`: записанный `match` с server-derived winner,
`current_match`-successor и обновлённую `queue`. Идентичный retry завершённого матча использует
`game_session_processed_results`; stale/non-current ID возвращает 409.

Skip body содержит `match_id` и ровно один вариант: `skipped_player_id` текущего игрока либо
`skip_both=true`. Команда доступна organizer или одному из current players. Она возвращает тот же
`SessionMatchCommandResponse`: cancelled match без winner, один successor и обновлённую queue.
Повтор со старым `match_id` возвращает 409 и не создаёт дополнительный матч.

`SessionStatisticsResponse` содержит `items`, уже отсортированные по wins DESC, point difference
DESC, losses ASC и UUID ASC. Каждый item: публичный `user`, `wins`, `losses`, `points_for`,
`points_against`, `point_difference`. Источник — все исторические участники сессии и только её
валидные completed matches/sets; вышедшие участники сохраняются, участники без матчей получают нули.
Endpoint ничего не записывает, не возвращает глобальный рейтинг и доступен по тем же правилам, что
история матчей.

`SessionQueueResponse` возвращает `current_pair`, ordered `waiting` и `is_paused`. Для active-сессии
с одним оставшимся игроком обе коллекции пусты и `is_paused=true`; следующий join создаёт новую
пару/match. История доступна organizer и любому когда-либо участвовавшему пользователю, queue —
только текущему active participant. Все маршруты требуют Bearer JWT.

PS-007.5b подтверждает контракт реальными JWT/FastAPI/PostgreSQL E2E: полный flow A/B/C,
двухпользовательская очередь, active join/waiting leave, pause/resume, cancel/cancel-and-leave,
идемпотентный retry, stale match, запрет standalone correction и lifecycle/permission errors.
OpenAPI содержит все session paths, Bearer security, обязательный `match_id` и документированные
400/401/403/404/409/422 responses. Swagger UI доступен локально по `/docs`.

WebSocket не принимает access JWT в query string. Сначала клиент с обычным Bearer JWT вызывает
`POST /game-sessions/{session_id}/ws-ticket` и получает
`{"ticket":"opaque-value","expires_in":30}` с `Cache-Control: no-store`. Endpoint проверяет
существование сессии и участие пользователя. Затем browser открывает WebSocket, передавая два
subprotocol values: `pingstats.v1` и `pingstats.ticket.<opaque-value>`. Ticket находится в
`Sec-WebSocket-Protocol`, не в URL; сервер выбирает и возвращает только `pingstats.v1`.

Ticket привязан к user/session, хранится на сервере только как SHA-256 digest, истекает через
`WEBSOCKET_TICKET_TTL_SECONDS` и атомарно удаляется при первой попытке использования. Перед accept
сервер повторно проверяет доступ короткой SQLAlchemy session, которая закрывается до регистрации
socket. Close codes до accept: `4401` — отсутствующий/невалидный/истёкший/replayed ticket, `4403` —
нет доступа, `4404` — сессия исчезла. Сервер посылает heartbeat:

```json
{"type":"heartbeat","session_id":"uuid","timestamp":"2026-11-08T20:00:00Z"}
```

После успешного commit любой изменяющей команды игровой сессии сервер публикует один invalidation
event подписчикам этой сессии:

```json
{"type":"session.updated","session_id":"uuid","timestamp":"2026-11-08T20:00:00Z"}
```

После `session.updated` клиент обязан перечитать существующий REST API; полные session/queue/match
данные через socket не передаются. Событие публикуется для create/join/leave/start/complete/cancel,
записи сета или полного результата, отмены матча, cancel-and-leave и skip one/both. Ошибка,
rollback, конкурентный конфликт и идемпотентный повтор уже обработанного результата события не
создают. Доставка best-effort: пропущенное socket-событие восстанавливается последующим REST-чтением.

Для списка «Мои игры» клиент с Bearer JWT вызывает `POST /game-sessions/ws-ticket`, а затем открывает
`WS /game-sessions/ws` с теми же subprotocol values. Ticket имеет отдельный scope и не принимается
session-specific endpoint. Канал привязан к `user_id`; несколько соединений одного пользователя
разрешены. После committed create/join/leave/start/complete/cancel и cancel-and-leave сервер
отправляет разрешённым пользователям:

```json
{"type":"sessions.updated","timestamp":"2026-11-09T00:00:00Z"}
```

Текущий REST-список содержит только сессии, где пользователь когда-либо участвовал. Поэтому
аудитория события — исторические участники изменённой сессии, включая вышедших; глобальной рассылки
нет. Event не содержит session ID, состав или иные данные. Запись результата, cancel/skip текущего
матча не меняют отображаемые list fields и list event не создают. Rollback/conflict/idempotent no-op
также ничего не отправляют.

Frontend transport использует оба ticket endpoint через общий `ApiClient`, но не сохраняет ticket
и не добавляет credentials в URL. Каждое переподключение начинает новый HTTP
ticket exchange. Negotiated subprotocol обязан быть `pingstats.v1`; heartbeat не вызывает REST
refetch. При разрыве transport временно включает REST fallback и после нового open требует полную
синхронизацию. С PS-010.4.2 list channel подключён к `/games`: `sessions.updated`, reconnect и
fallback инвалидируют list cache и повторяют существующий REST-запрос текущей страницы без
reload. С PS-010.4.3 session-scoped channel подключён к detail и result routes: событие, reconnect и
fallback перечитывают активные REST-проекции session prefix без полного reload и без постоянного
polling.

Frontend PS-008.2b использует этот контракт без дополнительных endpoint: список и detail,
participants/queue/history/current match, lifecycle commands и full-result. Он передаёт
`expected_version`, после mutation инвалидирует session queries и получает внешние изменения через
session WebSocket с REST fallback. HTTP 409 считается stale state: повторная отправка блокируется до явного получения
актуального current match. Telegram `startapp=session_<UUID>` ведёт сначала на join, потому что
приглашённый пользователь ещё не имеет права читать закрытые детали сессии.

## 6. Турниры

| Method | Path | Назначение |
|---|---|---|
| POST | `/tournaments` | Создать draft; password только для closed |
| GET | `/tournaments` | Каталог/history с filters |
| GET | `/tournaments/{id}` | Детали турнира |
| PATCH | `/tournaments/{id}` | Настройки до старта |
| POST | `/tournaments/{id}/open-registration` | Открыть регистрацию |
| POST | `/tournaments/{id}/join` | `{password?}`; пароль не возвращается/не логируется |
| POST | `/tournaments/{id}/invitations` | Organizer приглашает игрока; создаёт notification event |
| POST | `/tournaments/{id}/leave` | Самостоятельно выйти только до старта |
| GET | `/tournaments/{id}/participants` | Участники/seeds |
| POST | `/tournaments/{id}/start` | Зафиксировать состав и построить сетку |
| GET | `/tournaments/{id}/bracket` | Раунды, узлы, матчи |
| POST | `/tournaments/{id}/matches/{match_id}/walkover` | Organizer фиксирует техническую победу без sets и rating input |
| POST | `/tournaments/{id}/cancel` | Отменить до/по policy |

Результат турнирного матча записывается общим endpoint матча. Записывать может участник этого матча или organizer. Запуск требует минимум двух участников, фиксирует rating/RD snapshot и rating seeds. После запуска самостоятельный leave запрещён. Открытый завершённый турнир видит любой авторизованный пользователь; закрытый турнир и его матчи — только organizer/participants, иначе 404 для сокрытия существования ресурса.

## 7. Ratings, statistics, leaderboard, notifications

| Method | Path | Назначение |
|---|---|---|
| GET | `/ratings/me` | Текущий rating/RD/volatility |
| GET | `/rating-periods` | Опубликованные периоды |
| POST | `/rating-periods/run-due` | Только development; вручную вызвать тот же идемпотентный catch-up, что scheduler; production 404 |
| GET | `/leaderboard` | Рейтинг с pagination |
| GET | `/statistics/me` | Моя статистика |
| GET | `/notifications` | Уведомления |
| POST | `/notifications/{id}/read` | Пометить прочитанным |
| GET | `/notification-preferences` | Настройки |
| PUT | `/notification-preferences` | Заменить настройки |

Production administrative endpoints закрытия/пересчёта периода не публикуются обычному клиенту. Автоматический scheduler закрывает сутки в 00:00 UTC и догоняет пропущенные периоды после рестарта. Development-only `run-due` не принимает границы/параметры периода и не позволяет пересчитать произвольный опубликованный период.

## 8. Permissions summary

- Список игроков, профили, rating, wins/losses, завершённые обычные матчи и открытые завершённые турниры: любой авторизованный пользователь; anonymous: 401.
- Закрытый турнир и его матчи: только organizer/participants; недоступному авторизованному пользователю возвращается 404.
- Session history: только organizer/participants до отдельного решения о публикации.
- Обычный result: только участник.
- Session result: любой active participant сессии.
- Tournament result: участник конкретного матча или organizer.
- Correction: участник до 24 часов после completion; session/tournament organizer без срока в своём контексте; reason необязателен, полный audit обязателен.
- Start/complete/cancel: organizer; запись результата доступна любому active participant и запускает автоматическую ротацию.
- Active-session join добавляет пользователя в waiting tail; waiting user может выйти, organizer — удалить. Current player сначала завершает или отменяет матч.
