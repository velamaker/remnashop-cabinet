"""Точка подключения overlay-правок — раньше любого кода приложения.

Питон импортирует `sitecustomize` сам, при инициализации `site`, если находит его
на пути. Это единственное общее место для всех наших процессов: бот и веб идут
через `src.overlay_app`, а taskiq-воркер и планировщик импортируют модули базы
напрямую и про overlay не знают. Файл кладётся в site-packages venv Dockerfile'ом
и НЕ является частью исходников бота.

`/opt/remnashop` добавляется в путь явно: на момент инициализации `site` текущий
каталог в `sys.path` ещё не факт что есть, а пакет правок лежит именно там.

Молчим при отсутствии пакета (этим питоном пользуются pip и alembic вне приложения)
и никогда не роняем интерпретатор — о неудачах кричит сам `overlay_patches`.
"""

import sys

_ROOT = "/opt/remnashop"

if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

try:
    import overlay_patches
except ImportError:
    pass
else:
    overlay_patches.install()
