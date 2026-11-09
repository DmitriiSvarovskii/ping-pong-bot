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

На поддерживаемых iOS/Android-клиентах Telegram Mini App один раз отключает вертикальный жест
сворачивания и запрашивает fullscreen при bootstrap; старые клиенты и Telegram Desktop используют
обычный `expand()`. Централизованный viewport controller отслеживает viewport, safe area и content
safe area, обновляет CSS variables и очищает подписки при unmount. Layout учитывает также browser
`env(safe-area-inset-*)`, не суммирует одинаковые insets и сохраняет нативную iOS-прокрутку. Жест по
собственной верхней панели Telegram по-прежнему может закрыть или свернуть Mini App. На экране
записи результата системная Telegram Back Button возвращает к игровой сессии, не сохраняя счёт и
не отменяя матч; на странице сессии та же кнопка возвращает в «Мои игры». Bridge-handler не
дублируется при React rerender и удаляется при смене маршрута.

Интерфейс использует локальные Montserrat WOFF2 subsets для кириллицы и латиницы с weights
400–700 и `font-display: swap`. Файлы собираются Vite из `src/assets/fonts`; запросов к Google Fonts
или другому font CDN во время работы приложения нет. Лицензия SIL OFL хранится рядом со шрифтами.

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

## Realtime client

`src/realtime` содержит общую транспортную инфраструктуру. Один
`RealtimeClient` поддерживает list- и session-scoped каналы, получает новый one-use ticket через
существующий `ApiClient` и передаёт его только как WebSocket subprotocol. JWT/ticket не попадают в
URL или persistent storage. Клиент проверяет negotiated `pingstats.v1`, игнорирует heartbeat и
неизвестные сообщения, а валидные invalidations передаёт consumer callback.

При разрыве используется exponential backoff с jitter и новым ticket для каждой попытки. Пока
socket нездоров, запускается один настраиваемый REST fallback interval (по умолчанию 15 секунд); при
восстановлении выполняется полная синхронизация. Hidden/offline состояние приостанавливает socket,
polling и ticket requests, visible/online — создаёт новое соединение. `AbortSignal` позволяет
consumer отменить текущий refetch при logout/unmount. `useRealtimeChannel` безопасен для React
Strict Mode и хранит актуальные callbacks без переподключения на каждый render. Постоянные
401/403, session-ticket 404 и WebSocket 4401/4403/4404 прекращают reconnect/fallback до нового
lifecycle resume или consumer.

Страница `/games` подключает персональный list channel только после готовности авторизации.
`sessions.updated`, успешное подключение/reconnect и REST fallback помечают устаревшим кеш списка и
повторно запрашивают только текущую страницу; одновременные сигналы объединяются в один refetch. Текущая
пагинация и кешированные карточки сохраняются, поэтому фоновое обновление не показывает
полноэкранный loader и не блокирует интерфейс.

Маршруты `/games/:sessionId` и `/games/:sessionId/result` используют один общий
`useSessionRealtime` каждый — одновременно монтируется только один route consumer. `session.updated`,
reconnect и fallback помечают stale весь session-prefix и перечитывают только активные detail,
participants, queue, current match, history и statistics queries. Постоянный четырёхсекундный
polling удалён. На форме результата refetch того же `match.id/version` сохраняет введённый счёт;
смена матча или версии атомарно создаёт чистый редактор и не переносит устаревший черновик.

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
