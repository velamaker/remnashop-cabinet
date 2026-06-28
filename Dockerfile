# Базовый образ ЗАПИННЕН на точную версию ради воспроизводимости прод-сборок:
# новый релиз base не «прилетит» на rebuild и не сломает overlay молча.
# Тег базы вынесен в ARG BASE_TAG (дефолт — пин ниже). Обновление базы — осознанно:
#   ./update.sh --base <тег>   (прогонит ./check-update.sh и пересоберёт overlay)
# что выставляет BASE_TAG в .env; Dockerfile/git при этом НЕ меняются.
ARG BASE_TAG=v0.8.2
FROM ghcr.io/snoups/remnashop:${BASE_TAG}

# Overlay admin API files on top of the base image
COPY admin_src/src/ /opt/remnashop/src/

# Версия форка (её читает планировщик для уведомлений об обновлении). Это версия,
# на которой собран образ — ровно то, что сейчас работает.
COPY VERSION /opt/remnashop/VERSION

# Точку входа uvicorn переключаем на overlay-обёртку (src/overlay_app.py),
# которая вызывает базовый application() и добавляет admin/public-роуты + таблицы
# поддержки. Если строка точки входа в base изменится и sed не сматчится —
# grep уронит билд сразу (а не молча в рантайме).
RUN sed -i 's#src\.__main__:application#src.overlay_app:application#g' docker-entrypoint.sh \
 && grep -q 'src.overlay_app:application' docker-entrypoint.sh
