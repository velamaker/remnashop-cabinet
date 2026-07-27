# Базовый образ ЗАПИННЕН на точную версию ради воспроизводимости прод-сборок:
# новый релиз base не «прилетит» на rebuild и не сломает overlay молча.
# Тег базы вынесен в ARG BASE_TAG (дефолт — пин ниже). Обновление базы — осознанно:
#   ./update.sh --base <тег>   (прогонит ./check-update.sh и пересоберёт overlay)
# что выставляет BASE_TAG в .env; Dockerfile/git при этом НЕ меняются.
ARG BASE_TAG=v0.8.2
FROM ghcr.io/snoups/remnashop:${BASE_TAG}

# Экспорт в Excel (.xlsx с автофильтром) — лёгкая write-only библиотека.
# ВАЖНО: приложение крутится из venv /opt/remnashop/.venv (uv-managed, без pip),
# поэтому ставим системным pip через --target прямо в site-packages venv
# (XlsxWriter — чистый python, --target безопасен). Отдельным слоем — кэшируется.
RUN pip install --no-cache-dir --target=/opt/remnashop/.venv/lib/python3.12/site-packages XlsxWriter==3.2.0

# Web Push (браузерные push PWA): pywebpush тянет py-vapid + http-ece; cryptography
# уже есть в базе. Тот же приём --target в site-packages venv. Отдельным слоем.
RUN pip install --no-cache-dir --target=/opt/remnashop/.venv/lib/python3.12/site-packages pywebpush==2.3.0

# Security: апгрейд уязвимых транзитивных пакетов базы до пропатченных версий (H-03).
# Апгрейд IN-PLACE в uv-venv через системный pip с --python (корректный uninstall+install,
# без дублей dist-info). Версии зафиксированы и совместимы с пинами базы:
#   aiohttp<3.14 (aiogram); cryptography<47 (remnapy); starlette≥0.46 (fastapi 0.140).
# starlette-CVE чинятся только в 1.x → согласованный bump fastapi 0.127→0.140 + starlette
# →1.3.1 (fastapi 0.140 снял верхнюю границу starlette; др. пакеты fastapi/starlette не пинят).
# cryptography упёрта в 46.x (remnapy<47) — остаток = OpenSSL в wheel (OS-слой). Кэш-слой.
RUN pip --python /opt/remnashop/.venv/bin/python install --no-cache-dir --upgrade \
      "fastapi==0.140.0" \
      "starlette==1.3.1" \
      "python-multipart==0.0.32" \
      "pillow==12.3.0" \
      "orjson==3.11.9" \
      "Mako==1.3.12" \
      "click==8.3.3" \
      "idna==3.18" \
      "python-dotenv==1.2.2"

# aiohttp/cryptography упирались в КОНСЕРВАТИВНЫЕ пины (aiogram<3.14, remnapy<47).
# Проверено в рантайме: aiogram 3.25 работает с aiohttp 3.14, remnapy — с cryptography 49
# (импорты + aiohttp-клиент + app-фабрика + доставка сообщений/Remnawave-вызовы ок).
# Ставим НОВЕЙШИЕ (0 CVE), обходя пины через --no-deps (их sub-deps уже удовлетворены).
# pip печатает warning о нарушении пина — это ожидаемо, установка успешна.
RUN pip --python /opt/remnashop/.venv/bin/python install --no-cache-dir --no-deps --upgrade \
      "aiohttp==3.14.3" \
      "cryptography==49.0.0"

# Совместимость с Remnawave 2.8+: панель сменила контракты (hwid userUuid→userId,
# host tag→tags и регистр xHttpExtraParams) → remnapy 2.7.0 роняет ValidationError
# на меню «Устройства» и «Статус сервиса». Патчим модели IN-PLACE в venv (fail-closed:
# если блок не найден — билд падает). Отдельным слоем. Подробности: scripts/patch-remnapy-2.8.py.
COPY scripts/patch-remnapy-2.8.py /opt/remnashop/scripts/patch-remnapy-2.8.py
RUN /opt/remnashop/.venv/bin/python /opt/remnashop/scripts/patch-remnapy-2.8.py

# Overlay admin API files on top of the base image
COPY admin_src/src/ /opt/remnashop/src/

# Версия форка (её читает планировщик для уведомлений об обновлении). Это версия,
# на которой собран образ — ровно то, что сейчас работает.
COPY VERSION /opt/remnashop/VERSION

# Курируемый список изменений — его читает лента обновлений в админке.
COPY CHANGELOG.md /opt/remnashop/CHANGELOG.md

# Точку входа uvicorn переключаем на overlay-обёртку (src/overlay_app.py),
# которая вызывает базовый application() и добавляет admin/public-роуты + таблицы
# поддержки. Если строка точки входа в base изменится и sed не сматчится —
# grep уронит билд сразу (а не молча в рантайме).
RUN sed -i 's#src\.__main__:application#src.overlay_app:application#g' docker-entrypoint.sh \
 && grep -q 'src.overlay_app:application' docker-entrypoint.sh
