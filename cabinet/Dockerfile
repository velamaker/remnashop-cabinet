# ---- Build stage ----
FROM node:22-alpine AS build
WORKDIR /app

COPY package.json package-lock.json* ./

# Установка зависимостей — вторая по тяжести операция после самой сборки, и именно на
# ней падают маленькие машины: npm пишет «Exit handler never called!» и выходит с
# ошибкой (жалоба 19 сентября при обновлении на 1.4.1). Это не поломка проекта, а
# признак нехватки памяти: npm распаковывает пакеты в несколько потоков, ядро убивает
# процесс, и npm рапортует своей внутренней ошибкой.
#   nice -n 19      — ставим установку в конец очереди процессора, как и сборку;
#   --maxsockets 4  — меньше параллельных загрузок и распаковок = ниже пик памяти;
#   --no-audit --no-fund — два лишних сетевых шага, которые на слабой сети только
#                    удлиняют установку;
#   max-old-space-size=512 — потолок кучи самого npm.
# Вторая попытка после чистки кеша — не суеверие: «Exit handler never called!» на
# маленьких машинах приходит и от битого кеша npm, и от убитого ядром процесса. Первый
# проход мог оставить половину распакованного пакета; чистка делает вторую попытку
# осмысленной, а не повтором той же ошибки.
RUN NODE_OPTIONS=--max-old-space-size=512 \
    nice -n 19 npm ci --no-audit --no-fund --maxsockets 4 \
 || (echo "npm ci упал — чищу кеш и пробую ещё раз (обычно это нехватка памяти)" \
     && npm cache clean --force \
     && NODE_OPTIONS=--max-old-space-size=384 \
        nice -n 19 npm ci --no-audit --no-fund --maxsockets 2)

COPY . .

# Передаём имя telegram-бота на этапе сборки (Vite встраивает VITE_* переменные в бандл)
ARG VITE_TELEGRAM_BOT_USERNAME
ENV VITE_TELEGRAM_BOT_USERNAME=${VITE_TELEGRAM_BOT_USERNAME}

# Сборка фронтенда — самая тяжёлая операция всей установки, и на VPS с одним-двумя
# ядрами она забирает процессор целиком: машина перестаёт отвечать по SSH, а на
# 1 ГБ памяти Node уходит в своп и установка «зависает» (жалоба пользователя,
# 9 августа).
#   nice -n 19   — сборка отдаёт процессор всему остальному; она станет дольше,
#                  но сервер останется живым;
#   max-old-space-size=1024 — потолок кучи Node. Без него сборщик разрастается,
#                  сколько дадут, и на маленькой машине упирается в OOM/своп
#                  вместо того, чтобы честно собраться медленнее.
ENV NODE_OPTIONS=--max-old-space-size=1024
RUN nice -n 19 npm run build

# ---- Serve stage ----
FROM nginx:1.27-alpine
# imagemagick — генерация PWA-иконок из логотипа бренда при старте (45-pwa-icons.sh).
RUN apk add --no-cache imagemagick
COPY --from=build /app/dist /usr/share/nginx/html

# nginx.conf — это envsubst-ШАБЛОН. Кладём в templates/, чтобы официальный
# entrypoint nginx подставил ${API_*} при старте контейнера (см. nginx.conf).
COPY nginx.conf /etc/nginx/templates/default.conf.template

# Подстановка бренда в index.html при старте (превью ссылок в мессенджерах без JS).
# Запускается официальным entrypoint'ом nginx из /docker-entrypoint.d/.
COPY docker-entrypoint.d/40-brand-title.sh /docker-entrypoint.d/40-brand-title.sh
# Генерация PWA-иконок бренда из логотипа (после 40-brand-title.sh).
COPY docker-entrypoint.d/45-pwa-icons.sh /docker-entrypoint.d/45-pwa-icons.sh
RUN chmod +x /docker-entrypoint.d/40-brand-title.sh /docker-entrypoint.d/45-pwa-icons.sh

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
# Префикс путей бэкенда. Дефолт обязателен: envsubst подставляет только те
# переменные, что есть в окружении, а без подстановки в конфиг попадает литерал
# ${API_PATH_PREFIX} — nginx падает с «unknown api_path_prefix variable» и
# кабинет не стартует вовсе. Значение по умолчанию = наш бот.
ENV API_PATH_PREFIX=/api/v1/public/

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
