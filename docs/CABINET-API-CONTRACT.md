# Контракт API кабинета (заморожен для адаптации к другим ботам)

Что кабинет требует от бэкенда. Собрано из `cabinet/src/api/*.ts` и `cabinet/src/types/api.ts`.
Документ — фундамент Этапа 1 адаптации к боту «Бедолага»: любой альтернативный бэкенд
должен закрывать эти пути с этими полями, иначе кабинет сломается.

Всего эндпоинтов: **112**.

## Авторизация

Только куки, никаких токенов в JS. Базовый путь в сети — /api (константа API_BASE в cabinet/src/api/client.ts); nginx кабинета переписывает /api/* → бэкенд /api/v1/public/*, а /api/admin/* → /api/v1/admin/* (cabinet/nginx.conf), в деве vite-прокси срезает /api и шлёт на localhost:5000. Каждый запрос идёт с credentials:"include", тело JSON + Content-Type: application/json только когда есть body; никаких Authorization-заголовков клиент не ставит. Вход: POST /api/auth/login (email+пароль), POST /api/auth/telegram (виджет: id/auth_date/hash), POST /api/auth/telegram/webapp (init_data внутри Mini App) или переход браузера на GET /api/auth/telegram/oidc/start (OIDC, бэкенд сам обрабатывает callback и ставит сессию). Ответ AuthResponse содержит ТОЛЬКО expires_at и refresh_expires_at — сам токен клиент не видит и нигде не хранит (ни localStorage, ни память): бэкенд обязан выставить HttpOnly Secure куки access_token и refresh_token на path=/ (подтверждается admin_src/src/web/endpoints/public/sessions.py и account.py, где они удаляются через delete_cookie). Refresh: в apiFetch любой ответ 401 (кроме самого /auth/refresh и вызовов с skipAuthRetry — login/register/telegram/сброс пароля) один раз триггерит POST /api/auth/refresh с credentials:"include"; параллельные 401 дедуплицируются через единый refreshPromise; если refresh вернул ok — исходный запрос повторяется теми же init, иначе ошибка отдаётся наверх. От бэкенда требуется: /auth/refresh должен по refresh-куке выдать новый access_token тоже кукой (тело ответа не читается) и вернуть 2xx. Определение состояния сессии на старте: AuthContext делает GET /auth/me, при 401/403 считает пользователя гостем (fail-closed), затем GET /auth/whoami для прав админки — при любой ошибке whoami все админ-флаги сбрасываются в false. Выход: POST /auth/logout (бэкенд гасит обе куки), POST /sessions/logout-all — завершить все сессии. Отдельный контур у админки: cabinet/src/api/admin.ts использует собственный adminFetch с ADMIN_BASE="/api/admin", тоже cookie-only, но БЕЗ авто-refresh при 401 (401 сразу превращается в ошибку); ответ 403 с detail=="2fa_required" порождает window-событие "admin-2fa-required" — модалка просит TOTP-код и вызывает POST /api/admin/2fa/unlock, после чего бэкенд ставит куку admin_2fa (HttpOnly, secure, 12ч). Формат ошибок — FastAPI: {"detail": "..."} строкой или массивом (клиент склеивает msg через "; "); HTML/текст парсится как fallback; 204 и пустое тело → undefined. Всё это требует same-origin (кабинет и /api на одном домене) — CORS-схема не поддерживается, кросс-доменные куки клиент не настраивает. Multipart-загрузки (логотип, импорт 3x-ui) идут отдельными fetch без Content-Type, тоже с credentials:"include"; XLSX-экспорты качаются как blob на той же cookie-аутентификации.

## Без чего кабинет не откроется

- GET /api/appearance — оформление; грузится в BrandingContext до отрисовки, даёт бренд, акцент, логотип, флаги тех-работ и список языков (доступен без авторизации)
- GET /api/auth/me — профиль; AuthContext на старте, без него пользователь считается гостем и кабинет уходит на логин
- GET /api/auth/whoami — права; при ошибке всё fail-closed, админка недоступна
- POST /api/auth/refresh — без него любая протухшая сессия = разлогин на первом же 401
- POST /api/auth/login (и/или POST /api/auth/telegram, POST /api/auth/telegram/webapp, GET /api/auth/telegram/oidc/start) — иначе войти нечем
- GET /api/subscription/current — центральная сущность кабинета (hook useSubscription): статус, срок, лимиты и url подписки для QR и подключения
- GET /api/subscription/offers — витрина покупки/продления; без неё нельзя купить подписку

## Оформление

| Метод | Путь | Зачем | Ключевые поля |
|---|---|---|---|
| GET | `/api/appearance` | Оформление кабинета — грузится в BrandingContext при старте приложения, до любого экрана: бренд, акцент, фон, логотип, тумблеры (sub-ссылка, тех-работы, языки) | brand_name, accent, background, background_dark, background_light, support_username, logo_url, telegram_oidc_enabled, sub_link_enabled, maintenance, maintenance_message, maintenance_block_login/registration/payments, ena |
| GET, PUT, POST (multipart), DELETE | `/api/admin/appearance, /appearance/logo` | Редактирование оформления кабинета и загрузка/удаление логотипа (логотип шлётся FormData отдельным fetch) | AdminAppearance{brand_name(null=авто), brand_name_resolved, accent, background_dark/light, logo_url, sub_link_enabled, crypto_links_enabled, maintenance_*, enabled_languages} |

## Авторизация

| Метод | Путь | Зачем | Ключевые поля |
|---|---|---|---|
| GET | `/api/auth/me` | Профиль текущего пользователя — AuthContext вызывает при загрузке; 401/403 = гость | telegram_id, auth_type (telegram/email/google/yandex/vk), email, is_email_verified, pending_email, name, username, language, role |
| GET | `/api/auth/whoami` | Права: пускать ли в админку, какие разделы, можно ли писать, есть ли пароль (fail-closed: при ошибке всё false) | role, is_admin, is_readonly_admin, can_access_admin, is_owner, full_access, can_write, sections[], grant_expires_at, has_password |
| POST | `/api/auth/refresh` | Обновление access-токена по refresh-куке. Клиент дергает автоматически один раз при 401 и повторяет исходный запрос; параллельные вызовы дедуплицируются | тело не читается — важен только res.ok и новые Set-Cookie |
| POST | `/api/auth/login` | Вход по email+password (skipAuthRetry — без refresh-ретрая) | запрос {email,password} → AuthResponse {expires_at, refresh_expires_at} + куки |
| POST | `/api/auth/register` | Регистрация по email; referral_code — реф-ссылка/рекламный код | запрос {email,password,name?,referral_code?} → {expires_at, refresh_expires_at} |
| POST | `/api/auth/logout` | Выход — бэкенд должен погасить куки access_token/refresh_token | {success} |
| POST | `/api/auth/telegram` | Вход через классический Telegram Login Widget (проверка hash на бэкенде) | запрос {id, first_name, last_name?, username?, photo_url?, auth_date, hash} → AuthResponse |
| POST | `/api/auth/telegram/webapp` | Автовход внутри Telegram Mini App по initData | запрос {init_data} → AuthResponse |
| POST | `/api/auth/telegram/link` | Привязка Telegram к существующему email-аккаунту (в настройках) | → MeResponse |
| GET (переход браузера) | `/api/auth/telegram/oidc/start` | Вход через Telegram по OIDC: window.location.href на этот URL со страницы логина; с ?mode=link — привязка из настроек. Бэкенд редиректит на oauth.telegram.org и обрабатывает callback сам | 302-редирект, ставит tx-куку, по возврату — куки сессии |
| POST | `/api/auth/change-password` | Смена пароля вошедшим | {current_password,new_password} → {success} |
| POST | `/api/auth/password/set` | Установка пароля для тех, кто вошёл через Telegram и пароля не имеет | {password} → {success, has_password} |
| POST | `/api/auth/password/reset/request` | «Забыл пароль»: запрос кода на почту (для невошедших, ответ всегда success) | {email} → {success} |
| POST | `/api/auth/password/reset/confirm` | «Забыл пароль»: подтверждение кода и установка нового пароля | {email, code, password} → {success} |
| POST | `/api/auth/email/change` | Смена/добавление email (уходит в pending до подтверждения) | {email} → {success, pending_email} |
| POST | `/api/auth/email/request-verification` | Отправка кода подтверждения на почту | {email?} → {success, target_email, expires_at} |
| POST | `/api/auth/email/confirm` | Подтверждение почты кодом | {code} → {success, email} |
| DELETE | `/api/auth/email` | Отвязать почту (доступно только при наличии Telegram-входа) | {success} |
| GET, PUT | `/api/admin/auth-settings` | Настройка Telegram-OIDC входа из админки (client_id/secret, тумблер, готовый redirect_uri) | telegram_oidc_client_id, has_secret, telegram_oidc_enabled_setting, telegram_oidc_active, redirect_uri |

## Подписка и устройства

| Метод | Путь | Зачем | Ключевые поля |
|---|---|---|---|
| GET | `/api/subscription/current` | Главная сущность кабинета (hook useSubscription): статус подписки, срок, лимиты, ссылка подписки для QR/приложений. Может вернуть null | user_remna_id, status (ACTIVE/EXPIRED/DISABLED), is_trial, traffic_limit, device_limit, traffic_limit_strategy, expire_at, url, plan_name, plan_duration_days, used_traffic_bytes, lifetime_used_traffic_bytes, online_at |
| GET | `/api/subscription/offers` | Витрина покупки/продления: тарифы, сроки, цены по шлюзам, скидки | gateways[{gateway_type,currency,currency_symbol}], plans[{id,public_code,name,description,traffic_limit,device_limit,type,recommended_purchase_type,durations[{days,prices[{gateway_type,currency,currency_symbol,original_a |
| POST | `/api/subscription/purchase` | Покупка подписки — инициализация платежа через шлюз | {plan_code,duration_days,gateway_type} → {payment_id, payment_url, purchase_type, status, is_free, final_amount, currency} |
| POST | `/api/subscription/extend` | Продление текущей подписки | {duration_days,gateway_type} → PaymentInitResponse (те же поля) |
| POST | `/api/subscription/pay-with-balance` | Оплата покупки внутренним балансом кабинета | {plan_code,duration_days,gateway_type} → {success, purchase_type, spent, balance} |
| POST | `/api/subscription/trial` | Активация пробного периода с главной | {success} |
| GET | `/api/subscription/trial-info` | Условия триала для кнопки/баннера | available, days, traffic_gb, devices |
| GET | `/api/subscription/devices` | Список подключённых устройств (страница «Устройства», диагностика) | devices[{hwid,platform,device_model,os_version,user_agent,created_at,updated_at}], current_count, max_count |
| DELETE | `/api/subscription/devices/{hwid}` | Отключить одно устройство | {deleted} |
| DELETE | `/api/subscription/devices` | Сбросить все устройства | {success} |
| POST | `/api/subscription/reissue` | Перевыпуск ссылки подписки (в т.ч. из мастера диагностики) | {success} |
| GET | `/api/subscription/server-stats` | Статистика трафика по нодам пользователя (любимый сервер) на главной | favorite{name,country_code,total}/null, nodes[{name,country_code,total}] |
| GET | `/api/subscription/traffic-history` | График расхода трафика по дням (TrafficChart) | days[{date,total}] |
| GET | `/api/subscription/service-status` | Быстрая проверка «всё ли работает» в мастере диагностики | nodes[{name,country_code,online}], all_operational |
| GET | `/api/subscription/servers` | Серверы вошедшего пользователя (ServerStatusCard) — с адресом host для замера пинга из браузера | nodes[{name,country_code,online,host,uptime_30d,history[{date,uptime}]}], all_operational, total, online, enabled |
| GET | `/api/subscription/freeze-status` | Состояние заморозки подписки | enabled, frozen, can_freeze, remaining_days, max_days, days_left |
| POST | `/api/subscription/freeze` | Поставить подписку на паузу | {frozen, remaining_days} |
| POST | `/api/subscription/unfreeze` | Снять с паузы | {frozen} |
| GET | `/api/admin/subscriptions/user/{id}` | Текущая подписка и история по пользователю | current/history[{id,user_id,status,is_trial,plan_name,expire_at,traffic_limit,device_limit,internal_squads,external_squad,url,created_at}] |
| POST | `/api/admin/subscriptions/user/{id}/{extend|disable|delete|grant|reset-trial|reset-traffic|reissue|referral-reset|traffic-limit|device-limit|squad-toggle|sync|message|points|balance|devices/delete}` | Все ручные операции над подпиской и аккаунтом из карточки: продлить, выдать/отключить, сбросить триал/трафик, перевыпустить, менять лимиты и сквады, синхронизация с Remnawave, сообщение в Telegram, начисление баллов/баланса, удаление устройства | {success, ...}; sendMessage дополнительно {delivered, reason: no_telegram/send_failed} |
| GET | `/api/admin/subscriptions/user/{id}/devices | /transactions?limit` | Устройства и платежи пользователя в карточке | devices[{hwid,platform,device_model,os_version,user_agent,...}], count; items[AdminUserTx] |
| GET, PUT, POST | `/api/admin/subscription-app, /subscription-app/routing/default` | Настройки подписки в приложении (Remnawave): заголовок профиля, интервал обновления, Happ announce/routing | profile_title, support_link, profile_update_interval, is_profile_webpage_url_enabled, happ_announce, happ_routing, custom_response_headers, limits{announce,title} |

## Публичное (без входа)

| Метод | Путь | Зачем | Ключевые поля |
|---|---|---|---|
| GET | `/api/status` | Публичный статус сервиса (лендинг и страница «Статус», без авторизации; host не отдаётся) | nodes[{name,country_code,online,uptime_30d,history}], all_operational, total, online |
| GET | `/api/plans/public` | Тарифы для публичного лендинга и страницы цен (без авторизации) | plans[{public_code,name,description,traffic_limit,device_limit,monthly_from_rub,max_duration_days,max_duration_price_rub}] |

## Прочее

| Метод | Путь | Зачем | Ключевые поля |
|---|---|---|---|
| GET | `/api/info` | Тексты страницы «Информация» (FAQ, правила, оферта, политика, расшифровка статусов) в markdown | faq[{q,a}], rules, privacy, offer, statuses |
| GET | `/api/apps` | Конфиг подключения (ConnectGuide): какие приложения показывать, приоритетное, ссылки установки и deep-link с {sub} | priority, enabled[], custom[{id,name,desc,platforms,deep_link,install_url}], link_overrides, link_meta, link_missing, manual_links, links_updated_at |
| GET | `/api/balance` | Кошелёк и баллы в кабинете | balance, points, point_value_rub, total_spent, total_purchases, autopay_enabled |
| GET | `/api/balance/transactions?limit&offset` | История платежей пользователя | total, limit, offset, items[{payment_id,status,gateway_type,gateway_display_name,purchase_type,plan_name,original_amount,discount_percent,final_amount,currency,is_free,is_test,created_at}] |
| GET | `/api/balance/topup/config` | Условия пополнения баланса: лимиты, бонус, пресеты, доступные шлюзы | enabled, bonus_percent, min_amount, max_amount, presets[], gateways[{gateway_type,name,currency_symbol}] |
| POST | `/api/balance/topup` | Создать платёж на пополнение | {amount,gateway_type} → {payment_id, payment_url, amount, bonus, total} |
| POST | `/api/balance/spend-on-renewal` | Продлить подписку за счёт баланса | {duration_days} → {success, days_added, spent, balance, expire_at} |
| POST | `/api/balance/convert-points` | Конвертация реферальных баллов в рубли кошелька | {points} → {success, converted_points, credited_rub, balance, points} |
| POST | `/api/balance/autopay` | Тумблер автопродления с баланса | {enabled} → {success, autopay_enabled} |
| GET | `/api/referral/program` | Реферальная программа: код для ссылки, счётчики, уровни вознаграждений (главная + страница рефералов) | enabled, referral_code, invited_count, invited_with_payment_count, reward_type, reward_strategy, accrual_strategy, max_level, reward_levels[{level,value}] |
| GET | `/api/referral/earnings` | Сколько заработано по рефералке | earned, rewards_count |
| POST | `/api/gift/create` | Купить подарочную подписку (с баланса — код сразу, через шлюз — после оплаты) | {plan_code,duration_days,gateway_type?} → {paid_by: balance/gateway, code?, payment_id?, payment_url?, plan_name, duration_days, price} |
| GET | `/api/gift/my` | История купленных подарков и выданные коды | items[{payment_id,plan_name,duration_days,price,code,issued,created_at}] |
| POST | `/api/promocode/activate` | Активация промокода из карточки на главной | {code} → {success, code, reward_type, reward} |
| GET | `/api/promo-banner` | Промо-баннер в кабинете (аудитория/расписание решаются на бэкенде) | active, title, text, cta_text, cta_url, color, dismissible, version |
| GET | `/api/trial-discount` | Персональная скидка на первую покупку — баннер с таймером | active, percent, expires_at |
| GET | `/api/notifications?limit=` | Колокольчик: список уведомлений пользователя | unread, items[{id,title,body,url,is_read,created_at}] |
| GET | `/api/notifications/unread-count` | Счётчик непрочитанных для бейджа | {unread} |
| POST | `/api/notifications/read` | Отметить одно ({id}) или все (пустое тело) как прочитанные | {ok} |
| DELETE | `/api/notifications` | Очистить уведомления | {ok} |
| GET | `/api/push/vapid-key` | Публичный VAPID-ключ для подписки на web-push | {public_key} |
| GET | `/api/push/status` | Включён ли push и сколько устройств подписано | enabled, devices |
| POST | `/api/push/subscribe` | Регистрация push-подписки браузера | {endpoint, keys{p256dh,auth}} → {ok} |
| POST | `/api/push/unsubscribe` | Отписка по endpoint | {endpoint} → {ok} |
| POST | `/api/push/test` | Тестовый push себе | {ok, delivered} |
| GET | `/api/sessions` | История входов в карточке безопасности | items[{ip,user_agent,method,created_at}] |
| POST | `/api/sessions/logout-all` | Завершить все сессии (бэкенд гасит куки текущей тоже) | {ok} |
| GET | `/api/support/tickets` | Список тикетов пользователя | items[{id,subject,status: open/answered/closed,created_at,updated_at,messages_count}] |
| POST | `/api/support/tickets` | Создать тикет (в т.ч. из мастера диагностики) | {subject,message} → {id} |
| GET | `/api/support/tickets/{id}` | Переписка по тикету | id, subject, status, created_at, updated_at, messages[{id,sender: user/admin,body,created_at}] |
| POST | `/api/support/tickets/{id}/messages` | Ответ пользователя в тикет | {body} → {success} |
| POST | `/api/support/tickets/{id}/close` | Закрыть тикет | {success} |
| GET | `/api/account/export` | Экспорт своих данных одним JSON (профиль, подписка, платежи, входы, тикеты) | произвольный JSON-объект |
| POST | `/api/account/delete` | Самоудаление аккаунта по фразе подтверждения; бэкенд гасит куки | {confirm} → {deleted} |
| GET | `/api/admin/statistics/overview` | Админ-дашборд: сводка по пользователям, транзакциям, подпискам | users{total,active,blocked,new_today/week/month,with_active_subscription,...}, transactions{total,completed,gateways[]}, subscriptions{...} |
| GET | `/api/admin/statistics/sales | /statistics/daily?days | /statistics/cohorts?months | /statistics/metrics | /statistics/transactions` | Графики и метрики админки: продажи по периодам, регистрации/выручка по дням, когорты удержания, MRR/ARPU/churn | periods[], series[{date,registrations,revenue}], cohorts[{cohort,size,retention[]}], mrr/arpu/arppu/conversion/churn/top_plans/top_gateways |
| GET | `/api/admin/users?limit&offset&search&blocked&role&sort&order&expiring_days` | Список пользователей с фильтрами и поиском | total, limit, offset, items[AdminUser: id, telegram_id, auth_type, email, name, username, role, language, is_blocked, is_bot_blocked, is_trial_available, personal_discount, purchase_discount, points, cabinet_balance, ref |
| GET | `/api/admin/users/{id}` | Карточка пользователя | user, current_subscription{status,is_trial,plan_name,expire_at,traffic_limit,device_limit}, subscriptions_count, logins{total,distinct_ips,last_login_at}, transactions[] |
| GET | `/api/admin/users/{id}/logins | /referrals | /traffic-by-node?days` | История входов, дерево рефералов, трафик по нодам в карточке | items[{ip,user_agent,method,created_at}]; referrer/referrals/second_level/counts; nodes[{name,country_code,total}] |
| PUT | `/api/admin/users/{id}/block | /trial | /role | /discount` | Блокировка, доступность триала, смена роли, персональные скидки | {success, ...изменённое поле} |
| POST | `/api/admin/users/bulk-action` | Массовые действия по текущим фильтрам (баллы, скидка, блок/разблок) | {action,value,search,blocked,role,expiring_days} → {matched, applied} |
| GET | `/api/admin/users/export.xlsx | /api/admin/transactions/export.xlsx` | Выгрузка Excel по текущим фильтрам — качается blob-ом на cookie-авторизации (на iOS открывается новой вкладкой) | бинарный .xlsx |
| GET | `/api/admin/transactions?limit&offset&status&gateway&date_from&date_to` | Реестр платежей | total/limit/offset/items[{payment_id,user_id,user_name,user_email,status,gateway_type,purchase_type,is_test,amount,currency,plan_name,plan_duration,created_at,updated_at}] |
| GET | `/api/admin/transactions/{payment_id}` | Детали платежа | payment_id, status, is_test, purchase_type, gateway_type, gateway_display_name, payment_method, currency, pricing, plan_snapshot, user{id,name,email,username} |
| GET, POST, PUT, DELETE | `/api/admin/plans, /plans/{id}, /plans/{id}/toggle, /plans/meta/squads` | Редактор тарифов: список/создание/правка/включение/удаление + список сквадов Remnawave | AdminPlan{id,public_code,name,description,tag,type,availability,traffic_limit_strategy,traffic_limit,device_limit,allowed_telegram_ids,allowed_emails,internal_squads,external_squad,order_index,is_active,is_trial,duration |
| GET, POST, PUT, DELETE | `/api/admin/promocodes, /promocodes/{id}, /promocodes/{id}/toggle, /promocodes/{id}/stats` | Промокоды: список, создание, включение, статистика, удаление | AdminPromocode{id,code,is_active,reward_type,reward,plan_snapshot,availability,is_reusable,max_activations,expires_at,total_activations} |
| GET, POST | `/api/admin/broadcasts, /broadcasts/{task_id}, /broadcasts/audience-counts` | Рассылки по каналам Telegram/Email и их статус | items[{task_id,status,audience,total_count,success_count,failed_count,created_at}]; создание → {telegram[],email[]} |
| GET, PUT, POST | `/api/admin/gateways, /gateways/{id}/toggle, /gateways/{id}/fields, /gateways/{id}/fields/{field}, /gateways/{id}/test` | Платёжные шлюзы: включение, ввод секретов (по одному полю), тестовый платёж | items[{id,type,currency,is_active,is_configured,order_index,display_name}]; fields[{name,secret,is_set,hint}]; test → {ok,payment_id,url,message} |
| GET, PUT | `/api/admin/settings` | Общие настройки бота/сервиса из админки | default_currency, access{mode,registration_allowed,payments_allowed}, requirements{...}, referral{...}, backup{...}, extra{device_single_reset,device_all_reset,link_reset,trial_channel_guard,mini_app_reserve}, notificati |
| GET, PUT | `/api/admin/{cashback|topup|trial-discount|reserve|promo-banner|winback|digest|traffic-alert|new-device|login-alert|email-gate|freeze|morning-summary|admin-ip}` | Отдельные блоки настроек: кэшбэк, пополнение, скидка триальщикам, резервный доступ, промо-баннер, win-back, дайджест, алерты трафика/устройств/входов, обязательная верификация email, заморозка, утренняя сводка,白ый список IP админки | у каждого свой плоский конфиг (enabled + параметры), например reserve{enabled,reserve_gb,window_days,squad_uuid}, admin-ip{enabled,allowed_ips[],your_ip} |
| GET, PUT | `/api/admin/server-status, /server-status/nodes` | Настройка блока «Статус сервиса» в кабинете и выбор видимых нод | {enabled,bind_to_subscription,guest_visible,visible_nodes[],service_keywords[]}; nodes[{uuid,name,country_code,online,disabled}] |
| GET, PUT | `/api/admin/info` | Редактирование текстов страницы «Информация» | faq[], rules, privacy, offer, statuses |
| GET, PUT, POST | `/api/admin/apps, /apps/refresh-links` | Управление приложениями подключения и обновление ссылок установки резолверами | AppsConfig; refresh → {ok,count,updated_at,apps[],degraded[],missing[]} |
| GET, PUT | `/api/admin/menu, /menu/buttons` | Состав/порядок/подписи и цвета кнопок главного меню бота | MenuConfig{cabinet_miniapp,cabinet_url,connect_miniapp,connect_url,remna_sub,gift,order[],texts,colors,defaults}; buttons[{index,text,type,is_active,color}] |
| GET, PUT, POST | `/api/admin/email-settings, /email-settings/test` | SMTP/Brevo: параметры отправки почты и тестовое письмо | enabled, provider, host, port, use_tls, use_ssl, username, from_email, from_name, has_password, has_brevo_key, is_enabled, presets{} |
| GET, PUT, POST | `/api/admin/email-template, /email-template/test` | Текст письма с кодом подтверждения ({brand},{code},{minutes}) и тест | subject, heading, intro, expire_note, ignore_note |
| GET, POST | `/api/admin/2fa/status | /2fa/setup | /2fa/enable | /2fa/unlock | /2fa/disable` | TOTP-двухфакторка админа: включение (secret+otpauth для QR), разблокировка сессии по коду, отключение | {enabled}; setup → {secret, otpauth}; unlock → {unlocked} + кука admin_2fa |
| GET, PUT, DELETE | `/api/admin/grants/catalog, /grants/{userId}` | Гранулярные права админов: каталог разделов/пресетов, выдача и отзыв доступа | sections[{key,label}], presets[]; UserGrant{user_id,role,has_grant,full_access,can_write,sections[],expires_at,granted_by,effective{...}} |
| GET | `/api/admin/abuse/trials?min_accounts&only_trial` | Детект абьюза триалов — кластеры аккаунтов по IP/HWID/email/рефералу | clusters[{signal,key,severity,accounts[{id,name,email,telegram_id,username,created_at,is_blocked,is_trial_available,trial_used,young_tg}]}], total |
| GET, POST, PUT, DELETE | `/api/admin/ad-links, /ad-links/{id}, /ad-links/{id}/stats` | Рекламные ссылки и их конверсия | {id,name,code,is_active,stats{registrations,trials,buyers,trial_buyers,revenue,reg_to_buy_rate,trial_to_buy_rate}} |
| GET | `/api/admin/audit?limit&actor&method&path&date_from&date_to` | Журнал действий в админке | items[{id,actor,method,path,status,created_at}] |
| GET, DELETE, PUT | `/api/admin/notifications?limit, /notifications, /notifications/settings` | История уведомлений админам, очистка и тумблер admin push | items[{id,title,body,url,created_at}]; {admin_push_enabled} |
| GET | `/api/admin/updates?force=1` | Лента обновлений продукта и наличие новой версии | current, latest, update_available, repo, items[{version,name,date,notes,url,installed}] |
| GET, POST | `/api/admin/settings-io/export, /settings-io/import` | Экспорт/импорт бандла настроек инсталляции | {version,exported_at,assets{}}; импорт → {restored[],skipped[],count} |
| GET, POST (xui — multipart) | `/api/admin/import/status, /import/squads, /import/sync-panel, /import/sync-bot, /import/xui` | Импорт/синхронизация пользователей из панели, бота и файла 3x-ui | {panel,bot,xui}; squads[]; {success,synced}; xui → {success,found,started} |
| GET | `/api/admin/remnawave/system | /nodes | /hosts | /inbounds` | Живые данные панели Remnawave в админке (страница RemnaWave) | system{metadata.version, stats{cpu,memory,uptime,users,online_stats,nodes}}; nodes[{uuid,name,address,port,is_connected,is_disabled,users_online,traffic_used_bytes,xray_uptime,country_code,cpu_model,total_ram,last_status |
| POST | `/api/admin/remnawave/nodes/{uuid}/{restart|enable|disable}, /remnawave/nodes/restart-all` | Управление нодами из админки | тело ответа не используется — после вызова перезапрашиваются ноды |

## Как проверить бэкенд на соответствие этому документу

Документ заморожен, но проверять по нему руками нечего: расхождения почти никогда
не выглядят как «нет ручки». Выглядят они как 200 с трафиком в байтах вместо
гигабайт, ценой в копейках вместо рублей или `null` вместо 401 — то есть кабинет
не падает, а врёт. Для этого есть два инструмента, оба на голой стандартной
библиотеке и оба только на чтение (ни одной мутирующей ручки — безопасно на боевой
установке).

**Контрактные тесты — `tests/contract/run_contract.py`.** Гоняются против ЛЮБОГО
бэкенда по адресу из аргумента, проверяют обязательные поля, типы, ЕДИНИЦЫ
ИЗМЕРЕНИЯ и формы ошибок (401 без сессии, 501 «раздела нет»), плюс сквозные
сверки на стыке ручек (цена месяца в `/plans/public` против `/subscription/offers`,
лимит трафика против расхода, счётчик колокольчика против ленты).

```
# адаптер поверх чужого бота (нужна тестовая учётка)
python3 tests/contract/run_contract.py --base http://127.0.0.1:8090 \
    --email … --password …
# наш бэкенд без учётки: публичные пути и форма 401
python3 tests/contract/run_contract.py --base http://127.0.0.1:5000
```

Отсутствие раздела провалом НЕ считается: честный 501 или выключенный признак в
`appearance.features` — это спрятанный экран, кабинет так и задуман. Провал —
«умеет, но отвечает не по контракту». Сам контракт в машинном виде лежит в
`tests/contract/contract_spec.py`: правки туда идут ТОЛЬКО вслед за этим
документом.

**Канарейка на дрейф чужого API — `tests/contract/canary_bedolaga.py`.** Сравнивает
сегодняшний API «Бедолаги» с зафиксированным снимком
(`tests/contract/bedolaga-api-snapshot.json`) и показывает, что у них
переименовали или убрали из того, чем пользуется адаптер.

Единицы измерения, которые ломаются чаще всего:

| Поле | Единица | Чем грозит промах |
|---|---|---|
| `traffic_limit` (подписка, тарифы, витрина) | **гигабайты**, 0 = безлимит | кабинет переводит ГБ в байты сам — байты на входе дают терабайты на экране |
| `used_traffic_bytes`, `lifetime_used_traffic_bytes`, `traffic-history.days[].total`, `server-stats…total` | **байты** | соседнее поле в том же ответе — в ГБ; перепутать проще всего именно тут |
| `balance`, `total_spent`, `earned`, `min_amount`/`max_amount` | **рубли** числом | у «Бедолаги» деньги в копейках — промах ровно в 100 раз |
| `monthly_from_rub`, `max_duration_price_rub`, `original_amount`, `final_amount` | **рубли строкой** | кабинет их печатает, а не считает (`cabinet/src/types/api.ts`) |
