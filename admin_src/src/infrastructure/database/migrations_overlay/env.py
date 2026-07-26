"""Alembic-окружение для OVERLAY-схемы (независимое от base).

Ключевое отличие от base env.py: своя version-таблица `alembic_version_overlay`.
Поэтому overlay-миграции версионируются ОТДЕЛЬНО и НЕ конфликтуют с линейкой base
(именно из-за конфликта overlay-таблицы когда-то вынесли из alembic — см.
overlay_bootstrap.py). Теперь у них своя честная история.

target_metadata=None: autogenerate не используем (миграции пишем руками), поэтому
модели импортировать не нужно — env остаётся лёгким (важно: запускается субпроцессом
на старте контейнера).

Конкурентность: primary (remnashop) и HA-копия (remnashop-web) стартуют одновременно
и оба гонят `upgrade head`. Advisory-lock сериализует их — второй ждёт первого и видит
версию уже на head (no-op). Без лока была бы гонка на вставке в version-таблицу.
"""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from src.core.config import AppConfig

config = context.config
app_config = AppConfig.get()
db_config = app_config.database

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Отдельная version-таблица overlay — НЕ трогает base `alembic_version`.
VERSION_TABLE = "alembic_version_overlay"
# Ключ advisory-lock: любое фикс-число, лишь бы совпадало у всех процессов overlay.
_LOCK_KEY = 728411


def _configure(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=None,
        version_table=VERSION_TABLE,
        transaction_per_migration=True,
        compare_type=False,
    )


def do_run_migrations(connection: Connection) -> None:
    _configure(connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable: AsyncEngine = create_async_engine(url=db_config.dsn)
    try:
        async with connectable.connect() as connection:
            await connection.run_sync(do_run_migrations)
    finally:
        await connectable.dispose()


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=None,
        version_table=VERSION_TABLE,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
