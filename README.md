# PingStats

Монорепозиторий backend и Telegram Mini App PingStats.

## Требования

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- Node.js 20+ и npm

## Запуск

```shell
uv sync
uv run uvicorn pingstats.main:app --reload
```

После запуска доступны:

- Swagger UI: <http://127.0.0.1:8000/docs>
- OpenAPI: <http://127.0.0.1:8000/openapi.json>
- Health-check: <http://127.0.0.1:8000/api/v1/health>
- DB health-check (только development): <http://127.0.0.1:8000/api/v1/health/db>

## React Mini App

Frontend находится в `frontend/` и запускается отдельно от FastAPI:

```shell
cd frontend
npm install
npm run dev
```

Vite откроет <http://127.0.0.1:5173> и проксирует относительные `/api` запросы в
`http://127.0.0.1:8000`. В production Mini App открывается через
<https://t.me/PongStatsBot/play>; официальный Telegram SDK передаёт raw `initData`, backend
обменивает его на JWT, после чего frontend запрашивает `/api/v1/users/me`.

JWT хранится только в памяти приложения. `initData`, JWT и bot token не логируются и не
сохраняются в `localStorage`/`sessionStorage`. В development при открытии вне Telegram доступна
кнопка фиксированного backend `dev-login`; этот код удаляется из production bundle.

Frontend-проверки:

```shell
cd frontend
npm run build
npm run lint
npm run test
```

Игровые сессии доступны из вкладки **Игры**: создание и список, join по UUID или Telegram
`startapp`-приглашению, live-очередь, участники, история, lifecycle actions и запись полного
результата. Frontend использует только существующий `/api/v1/game-sessions` contract. Победитель,
ротация и следующий матч всегда определяются backend; stale result с HTTP 409 требует явного
обновления формы. Active-сессия опрашивается каждые четыре секунды, а успешные команды сразу
инвалидируют связанные запросы.

## Локальная PostgreSQL

PingStats использует локальный PostgreSQL без контейнеров. Создание базы и изменение
системной конфигурации приложение не выполняет. Укажите существующую базу в `.env`:

```dotenv
DB_HOST=127.0.0.1
DB_PORT=5432
DB_NAME=pingstats
DB_USER=pingstats_user
DB_PASSWORD=your-local-password
DB_ECHO=false
DB_POOL_SIZE=5
DB_MAX_OVERFLOW=10
JWT_SECRET_KEY=replace-with-a-random-secret-of-at-least-32-characters
JWT_ALGORITHM=HS256
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=60
TELEGRAM_BOT_TOKEN=
TELEGRAM_INIT_DATA_MAX_AGE_SECONDS=300
DEV_USER_ID=00000000-0000-4000-8000-000000000001
```

Имена с дефисами поддерживаются: URL формируется через `SQLAlchemy URL.create()`.
Пароль не добавляйте в `.env.example` или Git.

Проверка доступности сервера и текущих таблиц:

```shell
pg_isready -h 127.0.0.1 -p 5432
psql -h 127.0.0.1 -p 5432 -U <user> -d <database> -c '\dt'
```

## Миграции

Применить миграции к базе из `.env`:

```shell
uv run alembic upgrade head
uv run alembic current
uv run alembic heads
```

Первая revision является пустым baseline и создаёт только служебную таблицу
`alembic_version`. Приложение не вызывает `Base.metadata.create_all()`.

## Authentication

Production-вход Mini App выполняется через `POST /api/v1/auth/telegram`. Frontend передаёт
неизменённую строку `window.Telegram.WebApp.initData`; backend проверяет подпись официальным
HMAC-SHA256 алгоритмом Telegram, срок `auth_date`, извлекает профиль только из подписанных данных,
атомарно создаёт/обновляет пользователя и возвращает JWT:

```text
Telegram Mini App → initData → FastAPI validation → users upsert → access JWT
```

`TELEGRAM_BOT_TOKEN` хранится только в environment и требуется при вызове Telegram-auth. Пустое
значение не мешает запуску приложения и локальным тестам, но endpoint безопасно отвечает 503.
`TELEGRAM_INIT_DATA_MAX_AGE_SECONDS` по умолчанию равен 300. Нельзя логировать или сохранять
`initData`, bot token и JWT.

Пример формы запроса (значение должно прийти непосредственно из Telegram Mini App):

```shell
curl -X POST http://127.0.0.1:8000/api/v1/auth/telegram \
  -H 'Content-Type: application/json' \
  -d '{"init_data":"<window.Telegram.WebApp.initData>"}'
```

### Development login

`POST /api/v1/auth/dev-login` публикуется только при `APP_ENV=development`. Endpoint не
принимает body или идентификатор пользователя: он атомарно находит либо создаёт одну обычную
запись `users` с фиксированным `DEV_USER_ID` и возвращает короткоживущий JWT. Для локального
`.env` задайте случайный `JWT_SECRET_KEY` длиной не менее 32 символов; секрет нельзя добавлять
в Git или переносить в production.

Получить токен:

```shell
curl -X POST http://127.0.0.1:8000/api/v1/auth/dev-login
```

В Swagger UI откройте <http://127.0.0.1:8000/docs>, выполните `dev-login`, скопируйте
`access_token`, нажмите **Authorize** и вставьте только значение токена. После этого доступны
`GET /api/v1/users/me`, каталог, поиск и публичный профиль. Вне development маршрут
`dev-login` отсутствует и возвращает 404. JWT от Telegram-auth и dev-login совместимы с единым
`get_current_user`.

Авторизованный пользователь может работать с `/api/v1/friends`: отправлять и просматривать
заявки, принимать/отклонять/отменять их, получать список друзей и удалять взаимную связь.
Swagger показывает Bearer-защиту и схемы ответов с публичным профилем второго пользователя.

`/api/v1/matches` реализует standalone-матчи: создание и принятие/отклонение
приглашения, отмену, пошаговую и полную запись счёта, исправление в течение 24 часов
и журнал ревизий. Все write-операции валидируют роль участника, а result-команды
защищены row lock и `version`.

## Игровые сессии

JWT-защищённый API `/api/v1/game-sessions` реализует полный локальный flow свободной игры:

1. Organizer создаёт pending-сессию через `POST /game-sessions`.
2. Остальные пользователи вызывают `POST /{id}/join`.
3. Organizer запускает её через `POST /{id}/start`; сервер создаёт match №1.
4. Current match читается через `GET /{id}/matches/current`, очередь — через
   `GET /{id}/queue`.
5. Полный результат отправляется в `PUT /{id}/matches/current/result`; body обязательно содержит
   выданный сервером `match_id` и список партий. Победитель, ротация и следующий match определяются
   сервером атомарно.
6. Organizer завершает сессию через `POST /{id}/complete`; незавершённый current match отменяется
   без поражения, завершённая история сохраняется.

Обычная отмена current match создаёт successor с новым `sequence_number`.
`cancel-and-leave` атомарно отменяет матч и выводит играющего; если остаётся один игрок, active-
сессия приостанавливается без фиктивного матча и возобновляется после следующего join. Waiting
participant может выйти обычным `/leave`, current player — только через завершение матча или
`cancel-and-leave`. Session match нельзя исправить через standalone API: безопасный correction
workflow, рейтинг и statistics остаются ограничениями MVP текущего этапа.

Полный список request/response схем и ошибок приведён в `docs/API.md`. Swagger UI показывает
Bearer JWT security и все 15 session paths.

Типовые ошибки:

- `database ... does not exist` — проверьте точный регистр и значение `DB_NAME`;
- `password authentication failed` — исправьте только локальный `.env`;
- `connection refused` — проверьте, что локальный PostgreSQL запущен и слушает `DB_PORT`;
- HTTP 503 от `/api/v1/health/db` — приложение работает, но выполнить `SELECT 1` не удалось.

## Проверки

```shell
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv lock --check
uv run alembic check
```

PostgreSQL integration-тесты запускаются явно только против выбранной локальной мигрированной
БД. Обычные repository-тесты откатывают внешнюю транзакцию. Отдельный concurrency-тест на
нескольких соединениях создаёт UUID-уникальные строки и в `finally` удаляет только их:

```shell
RUN_POSTGRES_INTEGRATION=1 uv run pytest tests/integration
RUN_POSTGRES_INTEGRATION=1 RUN_POSTGRES_CONCURRENCY=1 uv run pytest \
  tests/integration
```

HTTP acceptance игровых сессий можно запустить отдельно:

```shell
RUN_POSTGRES_INTEGRATION=1 RUN_POSTGRES_CONCURRENCY=1 uv run pytest \
  tests/integration/test_game_sessions_api.py
```

Базовый `/api/v1/health` и запуск приложения не требуют доступной PostgreSQL или настроенного
Telegram bot token.
