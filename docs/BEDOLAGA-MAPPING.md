# Сверка контрактов: наш кабинет ↔ бот «Бедолага»

Их API снят со стенда (`/openapi.json`): **639** путей, из них **374** не админские.
Наш контракт: **110** путей (`docs/CABINET-API-CONTRACT.md`).

## Состояние адаптера (проверено на стенде)

- **18** путей проходят простой трансляцией по `adapter/route_map.json`;
- **11** адаптер собирает сам (`adapter/compose.py`) — там, где у них нет ручки
  один-к-одному или отличается форма ответа;
- **45** ещё не закрыто: адаптер отвечает 501 с текстом, чего именно не хватает.

Собираем сами: `/api/appearance` (+ логотип) — из шести их публичных ручек
(`/branding`, `/branding/colors`, `/branding/telegram-widget`, `/info/support-config`),
кэш 60 с; `/api/auth/me` и `/api/auth/whoami` — форма и права (fail-closed: без
подтверждённого признака админа доступа не даём); `/api/subscription/current`,
`/trial-info`, `/devices`, `/servers`, `/api/status`, `/api/balance`,
`/api/balance/transactions` — пересчёт единиц (у них гигабайты и копейки, у нас
байты и рубли) и страниц в limit/offset.

### Похоже по имени, но по смыслу другое — не мапим

Машинная карта подобрала эти пары по созвучию; каждая сделала бы не то, что просит
кабинет, поэтому они переведены в 501 (см. `mismatched` в `route_map.json`):

| Наш путь | Что подобралось | Почему нет |
|---|---|---|
| `/api/gift/create` | `/cabinet/referral/withdrawal/create` | это заявка на **вывод денег**, а не подарок (подарки у них — `/cabinet/gift/purchase`) |
| `/api/account/export` | `/cabinet/admin/rbac/audit-log/export` | админский аудит-лог, а не выгрузка своих данных |
| `/api/info` | `/cabinet/subscription/info` | у них про подписку, у нас — FAQ/правила (`/cabinet/info-pages`) |
| `/api/subscription/offers` | `/cabinet/promo/offers` | у них персональные промо-скидки, нам нужны тарифы с ценами (`/cabinet/subscription/purchase-options`) |

### Чего у них нет вовсе

Пинг и аптайм нод (адреса серверов наружу не отдаются), публичный статус сервиса
без авторизации, расход трафика за всё время, счётчики «всего потрачено/покупок»,
баллы рефералки (у них начисление сразу рублями). Эти поля отдаём пустыми или
честным 501 — не подставляем правдоподобные нули туда, где их можно принять за факт.

## Совпадают как есть — 16

- `/api/auth/me`
- `/api/auth/refresh`
- `/api/auth/logout`
- `/api/auth/telegram`
- `/api/auth/email/change`
- `/api/subscription/purchase`
- `/api/subscription/trial`
- `/api/subscription/devices`
- `/api/subscription/devices/{hwid}`
- `/api/subscription/devices`
- `/api/balance`
- `/api/balance/topup`
- `/api/referral/earnings`
- `/api/promocode/activate`
- `/api/admin/users/{id}`
- `/api/admin/settings`

## Есть, но под другим именем — 41 (адаптер переименовывает)

- `/api/admin/appearance, /appearance/logo` → `/branding/logo`
- `/api/auth/login` → `/auth/email/login`
- `/api/auth/register` → `/auth/email/register`
- `/api/auth/password/reset/request` → `/auth/deeplink/request`
- `/api/subscription/offers` → `/promo/offers`
- `/api/subscription/servers` → `/admin/servers, /admin/remnawave/sync/servers`
- `/api/admin/subscriptions/user/{id}` → `/auth/account/unlink/{x}, /auth/merge/{x}, /subscription/devices/{x}, /subscript`
- `/api/admin/subscriptions/user/{id}/{extend|disable|delete|grant|reset-trial|reset-traffic|reissue|referral-reset|traffic-limit|device-limit|squad-toggle|sync|message|points|balance|devices/delete}` → `/auth/account/unlink/{x}, /auth/merge/{x}, /subscription/devices/{x}, /subscript`
- `/api/status` → `/auth/email/change/status, /referral/partner/status, /admin/tickets/{x}/status, `
- `/api/info` → `/subscription/info`
- `/api/balance/topup/config` → `/wheel/config, /gift/config, /admin/wheel/config, /admin/apps/remnawave/config`
- `/api/balance/autopay` → `/subscription/autopay`
- `/api/gift/create` → `/referral/withdrawal/create`
- `/api/notifications/unread-count` → `/tickets/notifications/unread-count, /admin/tickets/notifications/unread-count`
- `/api/notifications/read` → `/tickets/notifications/{x}/read, /tickets/notifications/ticket/{x}/read, /admin/`
- `/api/notifications` → `/tickets/notifications, /notifications, /admin/tickets/notifications`
- `/api/push/status` → `/auth/email/change/status, /referral/partner/status, /admin/tickets/{x}/status, `
- `/api/push/test` → `/notifications/test, /admin/email-templates/{x}/test`
- `/api/support/tickets` → `/tickets, /admin/tickets`
- `/api/support/tickets` → `/tickets, /admin/tickets`
- `/api/support/tickets/{id}` → `/auth/account/unlink/{x}, /auth/merge/{x}, /subscription/devices/{x}, /subscript`
- `/api/support/tickets/{id}/messages` → `/tickets/{x}/messages`
- `/api/account/export` → `/admin/rbac/audit-log/export`
- `/api/admin/statistics/overview` → `/admin/campaigns/overview, /admin/remnawave/nodes/overview`
- `/api/admin/statistics/sales | /statistics/daily?days | /statistics/cohorts?months | /statistics/metrics | /statistics/transactions` → `/balance/transactions, /admin/apple-iap/transactions, /admin/users/{x}/transacti`
- `/api/admin/transactions/{payment_id}` → `/auth/account/unlink/{x}, /auth/merge/{x}, /subscription/devices/{x}, /subscript`
- `/api/admin/plans, /plans/{id}, /plans/{id}/toggle, /plans/meta/squads` → `/admin/remnawave/squads`
- `/api/admin/promocodes, /promocodes/{id}, /promocodes/{id}/toggle, /promocodes/{id}/stats` → `/referral/partner/campaigns/{x}/stats, /admin/tickets/stats, /admin/tariffs/{x}/`
- `/api/admin/gateways, /gateways/{id}/toggle, /gateways/{id}/fields, /gateways/{id}/fields/{field}, /gateways/{id}/test` → `/notifications/test, /admin/email-templates/{x}/test`
- `/api/admin/{cashback|topup|trial-discount|reserve|promo-banner|winback|digest|traffic-alert|new-device|login-alert|email-gate|freeze|morning-summary|admin-ip}` → `/auth/account/unlink/{x}, /auth/merge/{x}, /subscription/devices/{x}, /subscript`
- `/api/admin/server-status, /server-status/nodes` → `/admin/stats/nodes, /admin/ban-system/nodes, /admin/remnawave/nodes`
- `/api/admin/info` → `/subscription/info`
- `/api/admin/menu, /menu/buttons` → `/admin/broadcasts/buttons`
- `/api/admin/email-settings, /email-settings/test` → `/notifications/test, /admin/email-templates/{x}/test`
- `/api/admin/email-template, /email-template/test` → `/notifications/test, /admin/email-templates/{x}/test`
- `/api/admin/2fa/status | /2fa/setup | /2fa/enable | /2fa/unlock | /2fa/disable` → `/admin/users/{x}/disable`
- `/api/admin/grants/catalog, /grants/{userId}` → `/auth/account/unlink/{x}, /auth/merge/{x}, /subscription/devices/{x}, /subscript`
- `/api/admin/ad-links, /ad-links/{id}, /ad-links/{id}/stats` → `/referral/partner/campaigns/{x}/stats, /admin/tickets/stats, /admin/tariffs/{x}/`
- `/api/admin/notifications?limit, /notifications, /notifications/settings` → `/admin/tickets/settings, /admin/settings, /admin/ban-system/settings, /admin/par`
- `/api/admin/remnawave/system | /nodes | /hosts | /inbounds` → `/admin/remnawave/inbounds`
- `/api/admin/remnawave/nodes/{uuid}/{restart|enable|disable}, /remnawave/nodes/restart-all` → `/admin/remnawave/nodes/restart-all`

## Нет у них — 53 (закрывать самим)

- `/api/appearance`
- `/api/auth/whoami`
- `/api/auth/telegram/webapp`
- `/api/auth/telegram/link`
- `/api/auth/change-password`
- `/api/auth/password/set`
- `/api/auth/password/reset/confirm`
- `/api/auth/email/request-verification`
- `/api/auth/email/confirm`
- `/api/auth/email`
- `/api/admin/auth-settings`
- `/api/subscription/current`
- `/api/subscription/extend`
- `/api/subscription/pay-with-balance`
- `/api/subscription/trial-info`
- `/api/subscription/reissue`
- `/api/subscription/server-stats`
- `/api/subscription/traffic-history`
- `/api/subscription/service-status`
- `/api/subscription/freeze-status`
- `/api/subscription/freeze`
- `/api/subscription/unfreeze`
- `/api/admin/subscriptions/user/{id}/devices | /transactions?limit`
- `/api/admin/subscription-app, /subscription-app/routing/default`
- `/api/plans/public`
- `/api/apps`
- `/api/balance/transactions?limit&offset`
- `/api/balance/spend-on-renewal`
- `/api/balance/convert-points`
- `/api/referral/program`
- `/api/gift/my`
- `/api/promo-banner`
- `/api/trial-discount`
- `/api/notifications?limit=`
- `/api/push/vapid-key`
- `/api/push/subscribe`
- `/api/push/unsubscribe`
- `/api/sessions`
- `/api/sessions/logout-all`
- `/api/support/tickets/{id}/close`
- `/api/account/delete`
- `/api/admin/users?limit&offset&search&blocked&role&sort&order&expiring_days`
- `/api/admin/users/{id}/logins | /referrals | /traffic-by-node?days`
- `/api/admin/users/{id}/block | /trial | /role | /discount`
- `/api/admin/users/bulk-action`
- `/api/admin/users/export.xlsx | /api/admin/transactions/export.xlsx`
- `/api/admin/transactions?limit&offset&status&gateway&date_from&date_to`
- `/api/admin/broadcasts, /broadcasts/{task_id}, /broadcasts/audience-counts`
- `/api/admin/apps, /apps/refresh-links`
- `/api/admin/abuse/trials?min_accounts&only_trial`
- `/api/admin/audit?limit&actor&method&path&date_from&date_to`
- `/api/admin/updates?force=1`
- `/api/admin/settings-io/export, /settings-io/import`
