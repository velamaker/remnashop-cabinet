"""Один RS_PG_DSN на все opt-in тесты с настоящим Postgres.

ЗАЧЕМ. Половина файлов подключается через asyncpg (`postgresql://…`), половина —
через SQLAlchemy (`postgresql+asyncpg://…`). Переменная при этом ОДНА, и запуск
всего каталога одной командой ронял 9 тестов и 14 фикстур с «invalid DSN: scheme is
expected to be…». Со стороны это выглядит как сломанный продукт, а на самом деле —
разные ожидания к одной строке. Здесь строка приводится к нужному виду, и обе
половины живут от любого написания.
"""

import os

RAW = os.environ.get("RS_PG_DSN")
SKIP_REASON = "нужен RS_PG_DSN (одноразовый Postgres)"


def asyncpg_dsn(raw=None):
    """DSN для asyncpg.connect: без драйверного суффикса."""
    dsn = RAW if raw is None else raw
    return dsn.replace("postgresql+asyncpg://", "postgresql://", 1) if dsn else dsn


def sqlalchemy_dsn(raw=None):
    """DSN для create_async_engine: с драйвером asyncpg."""
    dsn = RAW if raw is None else raw
    if not dsn or dsn.startswith("postgresql+"):
        return dsn
    return dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
