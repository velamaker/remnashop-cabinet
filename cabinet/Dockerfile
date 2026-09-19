# ---- Build stage ----
FROM node:22-alpine AS build
WORKDIR /app

COPY package.json package-lock.json* ./

# Установка зависимостей — место, где чаще всего падает обновление у людей. И почти
# всегда по чужой причине: npm 10.9 прячет ЛЮБУЮ свою беду за одним и тем же текстом
# «Exit handler never called!». Проверено здесь же: тот же текст выдаётся и при
# нехватке памяти, и при недоступном registry.npmjs.org — то есть по сообщению
# человек (и мы) причину не узнаёт вовсе.
#
# Поэтому шаг делает три вещи:
#   1) печатает, сколько памяти и диска есть НА САМОМ ДЕЛЕ — это половина диагноза;
#   2) пробует поставить три раза, каждый раз мягче: меньше параллельных загрузок,
#      ниже потолок памяти, между попытками — чистка кеша;
#   3) если не вышло — показывает ХВОСТ ЖУРНАЛА npm прямо в выводе сборки, где и
#      написана настоящая причина (ETIMEDOUT, ENOTFOUND, ENOSPC, убитый процесс).
#      Без этого человек видит только «ошибка npm» и идёт искать её в нашем коде.
#
# Таймауты сети короткие НАМЕРЕННО: с длинными одна попытка висела восемь минут, и
# три попытки складывались в двадцать четыре — человек за это время решает, что всё
# повисло. Три повтора по полминуты переживают моргнувшую сеть и быстро сдаются,
# если сети нет вовсе.
#
# `--dns-result-order=ipv4first` — не украшение: на VPS со сломанным IPv6 npm уходит в
# AAAA-маршрут и висит до таймаута, а выглядит это ровно как та же ошибка npm.
RUN set -eu; \
    echo "== перед установкой: память $(free -m 2>/dev/null | awk '/^Mem:/{print $7}' || echo '?') МБ, диск $(df -Pm /app | awk 'NR==2{print $4}') МБ, npm $(npm -v)"; \
    npm config set fetch-retries 3 fetch-retry-maxtimeout 30000 fetch-timeout 120000 audit false fund false progress false; \
    attempt=1; \
    while [ "$attempt" -le 3 ]; do \
      case "$attempt" in \
        1) sockets=4; heap=512 ;; \
        2) sockets=2; heap=384; npm cache clean --force >/dev/null 2>&1 || true ;; \
        *) sockets=1; heap=256 ;; \
      esac; \
      echo "== попытка $attempt: maxsockets=$sockets, потолок памяти ${heap} МБ"; \
      if NODE_OPTIONS="--max-old-space-size=$heap --dns-result-order=ipv4first" \
         nice -n 19 npm ci --no-audit --no-fund --maxsockets "$sockets"; then \
        echo "== зависимости установлены"; break; \
      fi; \
      echo "== попытка $attempt не удалась. Настоящая причина — в журнале npm:"; \
      log="$(ls -t /root/.npm/_logs/*-debug-0.log 2>/dev/null | head -n 1 || true)"; \
      if [ -n "$log" ]; then \
        grep -Ei "error|ERR!|ENOSPC|ETIMEDOUT|ENOTFOUND|ECONN|killed" "$log" | tail -n 12 | sed 's/^/   npm| /' || true; \
      fi; \
      if [ "$attempt" = 3 ]; then \
        echo "== установка не прошла три раза."; \
        echo "== Чаще всего это: нет доступа к registry.npmjs.org (сеть, DNS, IPv6),"; \
        echo "==                нехватка памяти (нужен swap) или места на диске."; \
        exit 1; \
      fi; \
      attempt=$((attempt + 1)); \
    done

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
