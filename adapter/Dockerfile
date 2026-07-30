# Адаптер «кабинет ↔ чужой бот». Слой зависимостей отдельно от кода: правка
# обработчиков не тянет переустановку пакетов.
FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir "fastapi>=0.110" "uvicorn[standard]>=0.29" "httpx>=0.27"

COPY main.py compose.py state.py route_map.json selfcheck.py ./

# Своё состояние (паузы подписок) — в томе: слой образа не переживёт обновления.
ENV ADAPTER_STATE=/data/state.db
VOLUME ["/data"]

EXPOSE 8090

# Один воркер намеренно: переходы «заморожено → активно» защищены проверкой в
# SQLite, а не блокировкой между процессами.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8090", "--workers", "1"]
