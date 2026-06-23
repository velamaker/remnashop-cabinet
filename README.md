# RемнаShop

Telegram-бот + веб-кабинет + админ-панель для VPN-сервиса на базе
[Remnawave](https://remna.st/). Поверх официального образа
`ghcr.io/snoups/remnashop` добавлены веб-кабинет (React) и расширенная
админка/API (FastAPI).

## Возможности

- **Telegram-бот** — подписки, оплата, подключение устройств.
- **Веб-кабинет** — подписка, устройства, рефералы, поддержка, оплата;
  вход через Telegram или по email с подтверждением.
- **Админка** — пользователи, подписки (выдать/продлить/удалить вручную),
  тарифы, шлюзы, промокоды, транзакции, рассылки, статистика, тикеты,
  состояние нод Remnawave в реальном времени.

## Установка одной командой

Нужны установленные **Docker** и **Docker Compose**.

```bash
git clone https://github.com/alexdsndr161rus2015-maker/remnashop-cabinet.git
cd remnashop-cabinet
./install.sh
```

Установщик сам сгенерирует все секреты и спросит **только** то, что нельзя
сгенерировать:

- токен бота (`@BotFather`), username бота, ваш Telegram ID, username поддержки;
- домен бота и публичный URL кабинета;
- хост и токен Remnawave API;
- (необязательно) настройки email для регистрации по почте.

После этого он создаст `.env`, поднимет docker-сеть, соберёт и запустит
контейнеры.

## После установки

Сервисы слушают локально:

| Сервис   | Адрес              | Назначение                         |
|----------|--------------------|------------------------------------|
| Бот/API  | `127.0.0.1:5000`   | вебхуки Telegram, публичный API     |
| Кабинет  | `127.0.0.1:5002`   | веб-кабинет (nginx)                 |

Направьте домены на сервер и настройте reverse-proxy с TLS (Caddy/nginx)
на порты `5000` (API/вебхуки) и `5002` (кабинет).

Полезные команды:

```bash
docker compose -f docker-compose.yml -f cabinet/docker-compose.cabinet.yml ps
docker compose -f docker-compose.yml -f cabinet/docker-compose.cabinet.yml logs -f
```

## Конфигурация

Все переменные описаны в [`.env.example`](.env.example). Изменили `.env` —
перезапустите:

```bash
docker compose -f docker-compose.yml -f cabinet/docker-compose.cabinet.yml up -d
```

> `.env` содержит секреты и **не коммитится** (см. `.gitignore`).
