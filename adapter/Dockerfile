# Адаптер «кабинет ↔ чужой бот». Слой зависимостей отдельно от кода: правка
# обработчиков не тянет переустановку пакетов.
FROM python:3.12-slim

# Непривилегированный пользователь: адаптер держит админский токен чужого бота и
# ходит в сеть — процессу под root в контейнере делать нечего. UID фиксирован,
# потому что от него зависят права на том состояния (см. ниже).
RUN groupadd -g 10001 adapter \
 && useradd -u 10001 -g 10001 -M -d /app -s /usr/sbin/nologin adapter

WORKDIR /app

# Версии запинены целиком, включая транзитивные: без пинов один мажорный релиз
# любого пакета ломает установку у всех, кто ставит кабинет сегодня, а собрать
# вчерашний образ заново уже нечем. Список снят с рабочего контейнера
# (`docker exec cabinet-adapter pip freeze`). Первые три — свои зависимости,
# остальное они тянут за собой. Обновлять осознанно: поднять версии — прогнать
# `/adapter/health` и `adapter/selfcheck.py`.
RUN pip install --no-cache-dir \
      fastapi==0.141.1 \
      "uvicorn[standard]==0.52.0" \
      httpx==0.28.1 \
      annotated-doc==0.0.5 \
      annotated-types==0.8.0 \
      anyio==4.14.2 \
      certifi==2026.7.22 \
      click==8.4.2 \
      h11==0.16.0 \
      httpcore==1.0.9 \
      httptools==0.8.0 \
      idna==3.18 \
      pydantic==2.13.4 \
      pydantic-core==2.46.4 \
      python-dotenv==1.2.2 \
      PyYAML==6.0.3 \
      starlette==1.3.1 \
      typing-extensions==4.16.0 \
      typing-inspection==0.4.2 \
      uvloop==0.22.1 \
      watchfiles==1.2.0 \
      websockets==17.0

COPY main.py compose.py admin.py state.py route_map.json selfcheck.py ./

# Своё состояние (паузы подписок) — в томе: слой образа не переживёт обновления.
ENV ADAPTER_STATE=/data/state.db
# Каталог заводим В ОБРАЗЕ и сразу с нужным владельцем: docker создаёт новый том
# по образцу этого каталога вместе с правами. Иначе процесс без root не сможет
# записать паузу, а молча потерянная заморозка — это человек, который так и
# остался на паузе. Если /data подменяют bind-mount'ом, каталог на хосте должен
# быть доступен на запись UID 10001.
RUN mkdir -p /data && chown 10001:10001 /data
VOLUME ["/data"]

USER adapter

EXPOSE 8090

# Живость проверяет сам адаптер: /adapter/health отвечает, только когда поднялся
# FastAPI и открылась своя база. curl/wget в slim-образе нет — берём stdlib.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/adapter/health', timeout=3)"]

# Один воркер намеренно: переходы «заморожено → активно» защищены проверкой в
# SQLite, а не блокировкой между процессами.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8090", "--workers", "1"]
