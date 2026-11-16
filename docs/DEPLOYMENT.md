# PingStats deployment

Этот документ описывает ручную публикацию в существующую production-инфраструктуру. Команды на
production выполняются оператором; приложение не создаёт и не требует контейнеров.

## Production release PS-012

Ниже приведён обязательный порядок обновления production с Alembic revision
`20261107_0007` до `20261115_0011`. Команды выполняются из нового release-каталога оператором с
доступом к production `.env`. Значения `RELEASE_DIR`, `BACKUP_DIR`, PostgreSQL connection variables
и нижнюю границу backfill необходимо задать для фактической инфраструктуры. Пароль БД нельзя
помещать в командную строку или журнал; используйте защищённый `.pgpass`, `PGSERVICE` либо
интерактивно заданный `PGPASSWORD`.

### 1. Preflight и резервная копия

1. Открыть change window, запретить параллельный deployment и зафиксировать SHA release.
2. На текущем release до остановки приложения проверить health и revision:

```bash
cd "$RELEASE_DIR"
uv run alembic current
uv run alembic heads
```

`current` обязан показать `20261107_0007`, а единственный head нового release —
`20261115_0011`. Любое другое состояние требует остановки процедуры и отдельного анализа.

3. Остановить API, чтобы scheduler и новые записи не конкурировали с миграциями и первоначальным
   backfill:

```bash
sudo systemctl stop pingstats-api
sudo systemctl is-active pingstats-api
```

Последняя команда должна вернуть `inactive`.

4. Создать custom-format backup и проверить, что архив читается. Следующий пример предполагает,
   что `PGHOST`, `PGPORT`, `PGUSER`, `PGDATABASE` и безопасный способ передачи пароля уже настроены:

```bash
install -d -m 0700 "$BACKUP_DIR"
BACKUP_FILE="$BACKUP_DIR/pingstats_pre_ps012_$(date -u +%Y%m%dT%H%M%SZ).dump"
pg_dump --format=custom --no-owner --no-acl --file="$BACKUP_FILE"
pg_restore --list "$BACKUP_FILE" >/dev/null
sha256sum "$BACKUP_FILE" >"$BACKUP_FILE.sha256"
```

Не продолжать без сохранённого пути, checksum и проверенного restore runbook. По возможности до
change window восстановить этот backup в отдельную временную БД и выполнить smoke-чтение.

### 2. Dependencies и миграции

```bash
cd "$RELEASE_DIR"
uv sync --frozen --no-dev
uv run alembic upgrade head
uv run alembic current
uv run alembic heads
uv run alembic check
```

После upgrade `current` и `heads` обязаны показать `20261115_0011`, а `check` — отсутствие новых
schema operations. API пока не запускать. Миграции `0008`–`0011` добавляют rating/rebuild/voiding
структуры; destructive downgrade production не является способом rollback.

### 3. Dry-run и apply historical backfill

Зафиксировать один и тот же полуинтервал для обеих команд. Верхнюю границу следует получить один
раз после остановки API; она может находиться внутри текущего UTC-периода — такой период останется
`open` и не будет опубликован до 00:05 UTC следующего дня.

```bash
BACKFILL_FROM="2026-01-01T00:00:00Z"
BACKFILL_BEFORE="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

uv run python -m pingstats.modules.ratings.infrastructure.backfill \
  --dry-run \
  --from-completed-at "$BACKFILL_FROM" \
  --before-completed-at "$BACKFILL_BEFORE"
```

Сохранить JSON-отчёт. Продолжать можно только при `conflicted_matches=0`, ожидаемых
`eligible_matches`/`skipped_matches` и отсутствии active/failed rebuild barrier. Missing period до
уже опубликованного horizon, несовпадающий sealed input или barrier являются fail-closed
состояниями: apply не запускать и snapshots вручную не исправлять.

```bash
uv run python -m pingstats.modules.ratings.infrastructure.backfill \
  --apply \
  --from-completed-at "$BACKFILL_FROM" \
  --before-completed-at "$BACKFILL_BEFORE"

uv run python -m pingstats.modules.ratings.infrastructure.backfill \
  --dry-run \
  --from-completed-at "$BACKFILL_FROM" \
  --before-completed-at "$BACKFILL_BEFORE"
```

Повторный dry-run должен показать `processed_matches=0`: уже учтённые матчи становятся skipped.
Не расширять диапазон между dry-run и apply. Для большого объёма разрешено заранее разбить историю
на непересекающиеся хронологические полуинтервалы и выполнять каждый по этой же схеме.

### 4. Включение scheduler и smoke tests

Отдельной службы scheduler нет: он запускается lifespan FastAPI и защищён PostgreSQL advisory
locks. После успешного backfill запустить единственный production worker:

```bash
sudo systemctl start pingstats-api
sudo systemctl is-active pingstats-api
sudo journalctl -u pingstats-api --since "10 minutes ago" --no-pager
```

Проверить:

1. Health endpoint и Telegram auth/refresh возвращают ожидаемые статусы без утечки секретов.
2. `GET /api/v1/ratings/me`, rating history и leaderboard возвращают только published history;
   новый игрок без истории обрабатывается по контракту.
3. Текущий незакрытый UTC-период остаётся `open`; просроченные периоды последовательно доходят до
   `published`, без быстрого retry-loop в журнале.
4. Запись нового ranked результата создаёт ровно один input; unranked/cancelled/voided не влияют
   на рейтинг.
5. WebSocket handshake и существующие REST/game-session сценарии продолжают работать.
6. Повторный backfill dry-run с теми же границами остаётся идемпотентным.

### 5. Восстановление при ошибках

- Ошибка до apply: оставить API остановленным, исправить причину и безопасно повторить шаг. Dry-run
  не меняет данные.
- Ошибка migration: не выполнять downgrade. Восстановить backup в новую БД, переключить
  конфигурацию старого release на неё и только затем запустить старый backend.
- Ошибка intake внутри apply откатывает всю intake-транзакцию. После устранения инфраструктурной
  причины ту же команду можно повторить.
- Прерывание после intake, но во время workflow не требует ручной правки: повторный apply пропустит
  идентичные inputs и продолжит по durable completion markers.
- Conflict/barrier — бизнес-ошибка. Не применять интенсивный retry и не редактировать published
  snapshots; сначала завершить/восстановить rebuild либо согласовать отдельный import/rebuild.
- Если после apply обнаружено повреждение данных, полное rollback выполняется только как единая
  операция: остановить API, восстановить pre-release backup в отдельную новую БД, переключить на
  предыдущий release и эту БД, затем выполнить smoke tests. Не удалять новую БД и release до
  завершения анализа.

Пример безопасного restore в отдельную БД (имя должно быть новым и проверенным оператором):

```bash
RESTORE_DB="pingstats_restore_$(date -u +%Y%m%dT%H%M%SZ)"
createdb "$RESTORE_DB"
pg_restore --exit-on-error --no-owner --no-acl --dbname="$RESTORE_DB" "$BACKUP_FILE"
```

После restore проверить revision `20261107_0007`, основные counts и доступность данных, затем
изменить systemd environment на `RESTORE_DB`, выполнить `daemon-reload` и запустить предыдущий
release. Исходную production БД не удалять и не перезаписывать.

## Предварительные условия PS-011

- Backend работает одним Uvicorn worker через systemd unit `pingstats-api` (realtime manager хранит
  подключения in-memory).
- В production `.env` задан `JWT_REFRESH_TOKEN_EXPIRE_DAYS=30`. Значение можно изменить осознанно,
  но оно должно быть положительным. Существующие `APP_ENV=production`, `JWT_SECRET_KEY`, настройки
  PostgreSQL и `TELEGRAM_BOT_TOKEN` сохраняются.
- Миграции PostgreSQL для PS-011 не требуются.
- До переключения frontend необходимо проверить конфигурацию Nginx командой `nginx -t`.

## Backend

Рекомендуемый порядок публикации backend из подготовленного release-каталога:

```bash
uv sync --frozen --no-dev
uv run alembic current
uv run alembic heads
uv run alembic check
sudo systemctl restart pingstats-api
sudo systemctl is-active pingstats-api
```

После рестарта проверить health endpoint, Telegram login/refresh и WebSocket handshake через HTTPS.
Новый backend следует публиковать раньше frontend: старый frontend совместим с ним, а новый frontend
ожидает доступность `POST /api/v1/auth/refresh`.

## Атомарная публикация frontend

`npm run build` создаёт уникальный build ID, встраивает его в JS bundle и записывает тот же ID в
`frontend/dist/version.json`. `index.html` и `version.json` одной публикации нельзя смешивать с
другой сборкой.

Уже открытая Mini App может продолжать запрашивать assets предыдущей версии. Поэтому hashed assets
хранятся в общем append-only каталоге и не удаляются при публикации. Пример структуры:

```text
/var/www/pingstats/
├── assets/                    # общие hashed JS/CSS/fonts нескольких релизов
├── releases/
│   ├── <previous-build-id>/   # index.html, version.json и прочие root-файлы
│   └── <new-build-id>/
└── current -> releases/<new-build-id>
```

Порядок действий:

1. Локально выполнить проверки и `npm run build`.
2. Прочитать `build_id` из `frontend/dist/version.json` и создать новый release-каталог.
3. Сначала добавить содержимое `frontend/dist/assets/` в `/var/www/pingstats/assets/`. Не применять
   `--delete` к общему каталогу assets и не перезаписывать файл другим содержимым под тем же именем.
4. Скопировать root-файлы сборки (`index.html`, `version.json`, favicon и другие, кроме `assets/`) в
   новый release-каталог.
5. Проверить наличие всех путей `/assets/...`, перечисленных в новом `index.html`.
6. Атомарно переключить symlink `current` на новый release-каталог.
7. Не удалять предыдущий release и его assets во время публикации. Очистка выполняется отдельно по
   утверждённому retention-периоду после того, как старые клиенты гарантированно перестали работать.

## Nginx

Ниже приведены location-блоки для описанной структуры. Их необходимо объединить с существующими
`/api/` и WebSocket proxy locations, не заменяя их.

```nginx
root /var/www/pingstats/current;

location = / {
    try_files /index.html =404;
    add_header Cache-Control "no-store, no-cache, must-revalidate" always;
    add_header Expires "0" always;
}

location = /index.html {
    try_files /index.html =404;
    add_header Cache-Control "no-store, no-cache, must-revalidate" always;
    add_header Expires "0" always;
}

location = /version.json {
    try_files /version.json =404;
    default_type application/json;
    add_header Cache-Control "no-store, no-cache, must-revalidate" always;
    add_header Expires "0" always;
}

location ^~ /assets/ {
    alias /var/www/pingstats/assets/;
    add_header Cache-Control "public, max-age=31536000, immutable" always;
}

location / {
    try_files $uri $uri/ /index.html;
}
```

Если текущая структура каталогов отличается, пути следует адаптировать, сохранив три инварианта:
атомарное переключение root-файлов, `no-store` для HTML/manifest и сохранение старых hashed assets.
Перед reload выполнить `sudo nginx -t`, затем `sudo systemctl reload nginx`.

## Historical rating backfill

Backfill не запускается автоматически при deployment. Команду выполнять из активного backend
release с production `.env`, сначала обязательно в режиме dry-run и с явным полуинтервалом
`completed_at`:

```bash
uv run python -m pingstats.modules.ratings.infrastructure.backfill \
  --dry-run \
  --from-completed-at 2026-01-01T00:00:00Z \
  --before-completed-at 2026-11-01T00:00:00Z
```

JSON-отчёт содержит `scanned_matches`, `eligible_matches`, `processed_matches`, `skipped_matches`,
`conflicted_matches`, `periods`, `created_periods`, `published_periods` и `players`. Перед apply:

1. Создать обычную резервную копию PostgreSQL и зафиксировать выбранные границы.
2. Убедиться, что `conflicted_matches=0`, а active/failed rating rebuild отсутствует.
3. Сверить количество eligible/processed/skipped с ожидаемым набором импортированных матчей.
4. Запустить apply с абсолютно теми же границами:

```bash
uv run python -m pingstats.modules.ratings.infrastructure.backfill \
  --apply \
  --from-completed-at 2026-01-01T00:00:00Z \
  --before-completed-at 2026-11-01T00:00:00Z
```

Apply атомарно сохраняет inputs, затем запускает normal workflow только для выбранных непустых
периодов. Для первоначального PS-012 deployment использовать более строгую последовательность выше:
API остаётся остановленным до завершения apply. При последующих согласованных импортных операциях
PostgreSQL locks способны сериализовать publication, intake, correction и rebuild без остановки API,
но большая intake-транзакция задержит publication. Exit code `2` означает конфликт с
sealed/published history, `3` — active rebuild barrier; повторять apply без устранения причины
нельзя. Команда не переписывает published snapshots и не создаёт rebuild автоматически.

После apply повторить тот же dry-run: успешно учтённые inputs должны перейти в `skipped`, а
`processed_matches` стать нулём. Затем проверить current ratings, history и leaderboard через API.
При прерывании intake транзакция откатывается полностью; при прерывании последующего workflow
повторный apply пропускает inputs и продолжает существующие completion markers.

## Проверки после публикации

1. `systemctl is-active pingstats-api` возвращает `active`, health endpoint отвечает успешно.
2. `index.html` и `/version.json` возвращают `Cache-Control: no-store, no-cache, must-revalidate`.
3. Файл из `/assets/` возвращает `Cache-Control: public, max-age=31536000, immutable`.
4. `build_id` в `/version.json` совпадает с ID внутри загруженного JS bundle и отличается от
   предыдущей публикации.
5. Telegram login, автоматический refresh access JWT и защищённый REST-запрос работают.
6. WebSocket tickets, handshake и realtime-обновление списка/сессии работают через HTTPS.
7. Старая открытая Mini App продолжает загружать свой hashed bundle; после возвращения из фона она
   показывает banner новой версии, но не перезагружается автоматически.
8. После подтверждённого обновления черновик результата восстанавливается.

## Rollback

1. Атомарно вернуть symlink `current` на предыдущий frontend release. Общий каталог assets не
   изменять и Nginx reload для одного переключения symlink обычно не требуется.
2. Если требуется rollback backend, переключить backend release на предыдущий commit, выполнить
   `uv sync --frozen --no-dev`, перезапустить `pingstats-api` и проверить health/logs.
3. Для PS-011 downgrade Alembic не нужен. Для PS-012 не выполнять destructive downgrade: при
   необходимости вернуть предыдущую версию приложения вместе с восстановленной pre-PS-012 БД по
   runbook выше.
4. Повторить REST, refresh, WebSocket и frontend smoke checks.
5. Не удалять неудачный release до завершения анализа; он может понадобиться для диагностики.
