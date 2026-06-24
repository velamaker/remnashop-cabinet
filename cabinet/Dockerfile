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

# Подставляем ТОЛЬКО переменные с префиксом API_ — рантайм-переменные nginx
# ($host, $remote_addr, $binary_remote_addr, $request_uri, …) остаются нетронутыми.
ENV NGINX_ENVSUBST_FILTER=^API_
# Дефолт для одно-серверной установки (бот в той же docker-сети). Переопределяется
# через env API_UPSTREAM в compose (напр. на отдельном сервере — IP/host бота:5000).
ENV API_UPSTREAM=remnashop:5000

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
