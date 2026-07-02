# ---- Build stage ----
FROM node:22-alpine AS build
WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm ci

COPY . .

# Передаём имя telegram-бота на этапе сборки (Vite встраивает VITE_* переменные в бандл)
ARG VITE_TELEGRAM_BOT_USERNAME
ENV VITE_TELEGRAM_BOT_USERNAME=${VITE_TELEGRAM_BOT_USERNAME}

RUN npm run build

# ---- Serve stage ----
FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html

# nginx.conf — это envsubst-ШАБЛОН. Кладём в templates/, чтобы официальный
# entrypoint nginx подставил ${API_*} при старте контейнера (см. nginx.conf).
COPY nginx.conf /etc/nginx/templates/default.conf.template

# Подстановка бренда в index.html при старте (превью ссылок в мессенджерах без JS).
# Запускается официальным entrypoint'ом nginx из /docker-entrypoint.d/.
COPY docker-entrypoint.d/40-brand-title.sh /docker-entrypoint.d/40-brand-title.sh
RUN chmod +x /docker-entrypoint.d/40-brand-title.sh

# Подставляем ТОЛЬКО переменные с префиксом API_ — рантайм-переменные nginx
# ($host, $remote_addr, $binary_remote_addr, $request_uri, …) остаются нетронутыми.
ENV NGINX_ENVSUBST_FILTER=^API_
# Дефолты для одно-серверной установки (бот в той же docker-сети по http, Host=$host).
# Переопределяются через env в compose / install.sh. Для сайта на отдельном сервере:
#   API_UPSTREAM=bot.example.com:443  API_SCHEME=https  API_HOST_HEADER=bot.example.com
# API_HOST_HEADER по умолчанию = литерал $host (\$ экранирует подстановку Docker'ом).
ENV API_UPSTREAM=remnashop:5000
ENV API_SCHEME=http
ENV API_HOST_HEADER=\$host

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
