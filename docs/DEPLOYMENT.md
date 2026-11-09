# PingStats deployment

Этот документ описывает ручную публикацию в существующую production-инфраструктуру. Команды на
production выполняются оператором; приложение не создаёт и не требует контейнеров.

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
3. Downgrade Alembic для PS-011 не нужен: миграций нет.
4. Повторить REST, refresh, WebSocket и frontend smoke checks.
5. Не удалять неудачный release до завершения анализа; он может понадобиться для диагностики.
