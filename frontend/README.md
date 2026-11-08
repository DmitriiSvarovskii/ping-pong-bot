# PingStats Mini App

React frontend для Telegram Mini App. Backend запускается отдельно на `127.0.0.1:8000`.

```shell
npm install
npm run dev
```

Vite откроет приложение на <http://127.0.0.1:5173> и проксирует `/api` в локальный FastAPI.
Внутри Telegram приложение отправляет только raw `Telegram.WebApp.initData` в
`POST /api/v1/auth/telegram`. JWT хранится исключительно в памяти до перезагрузки страницы.

При открытии локального development build вне Telegram появляется кнопка фиксированного
`dev-login`. Она не принимает user ID и отсутствует в production build.

На поддерживаемых iOS/Android-клиентах Telegram Mini App один раз запрашивает fullscreen при
bootstrap; старые клиенты и Telegram Desktop используют обычный `expand()`. Layout учитывает
Telegram safe area и content safe area. На экране записи результата системная Telegram Back
Button возвращает к игровой сессии, не сохраняя счёт и не отменяя матч.

## Игровые сессии

После входа раздел `/games` позволяет создать сессию, открыть историю своих сессий или
присоединиться по UUID. Экран сессии показывает участников, текущую пару, упорядоченную очередь,
следующего игрока и историю матчей по `sequence_number`. Для active-сессии данные обновляются
каждые четыре секунды; после команд связанные TanStack Query cache entries инвалидируются сразу.
Кнопка «Пропустить» открывает выбор первого, второго или обоих current players. После успешной
атомарной команды modal закрывается, а current match и queue обновляются без перезагрузки страницы.

Результат вводится по партиям на отдельном мобильном экране. Frontend отправляет выданные
backend `match_id` и `version`, но не вычисляет победителя и не выполняет ротацию. При HTTP 409
форма блокируется до явной загрузки актуального матча, поэтому старый счёт нельзя случайно
отправить в successor match.

Приглашение имеет формат
`https://t.me/PongStatsBot/play?startapp=session_<UUID>`. После успешной Telegram-авторизации
подписанный `start_param` переводит пользователя на экран приглашения; join выполняется до чтения
закрытых деталей сессии. В Telegram используется системное меню отправки ссылки, вне Telegram —
clipboard fallback.

Основные маршруты:

- `/games` — список и история;
- `/games/create` — создание;
- `/games/join` — присоединение по UUID;
- `/games/:sessionId` — live-состояние и lifecycle actions;
- `/games/:sessionId/result` — полный результат текущего матча;
- `/games/:sessionId/invite` — безопасное принятие приглашения.

Проверки:

```shell
npm run build
npm run lint
npm run test
```
