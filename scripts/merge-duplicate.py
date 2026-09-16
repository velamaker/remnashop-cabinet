#!/usr/bin/env python3
"""Слить две записи одного человека: подписку — к телеграм-аккаунту, двойника удалить.

КОГДА НУЖНО. Синхрон панель→бот создаёт безымянную запись (`telegram_id = NULL`)
для панельного юзера без телеграма и вешает на неё подписку. Если Telegram ID
проставить в панели ПОЗЖЕ, подписка к телеграм-аккаунту не переедет: человек жмёт
/start, получает вторую запись и видит «Нет подписки». Сторож
(`tasks/account_duplicates.py`) находит такие пары сам и присылает готовую команду.

ПОЧЕМУ СКРИПТ, А НЕ КНОПКА В АДМИНКЕ. Слияние необратимо и трогает два десятка
внешних ключей. Прогон без `--apply` печатает ПОЛНЫЙ список того, что переедет, —
и решение принимается по нему, а не по вере в кнопку.

ПОЧЕМУ СПИСОК ТАБЛИЦ НЕ ЗАШИТ. Он читается из схемы на каждом прогоне. Зашитый
список устаревает молча: добавят таблицу со ссылкой на users — и её строки уедут в
каскадное удаление вместе с двойником, без единой строчки в выводе.

ПОРЯДОК ОБЯЗАТЕЛЕН. Все ссылки — ON DELETE CASCADE, поэтому удалять двойника можно
только ПОСЛЕ переноса. `users.current_subscription_id` у двойника гасим заранее:
иначе перенос подписки оставит у него ссылку на чужую строку.

Запуск — внутри образа бота (там есть и драйвер, и конфиг):

  docker compose run --rm --entrypoint python remnashop \
      /opt/remnashop/scripts/merge-duplicate.py --from 5001 --to 5002
  # и тем же, с --apply, когда список устраивает

Коды возврата: 0 — успех или сухой прогон; 1 — отказ проверок; 2 — ошибка запуска.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# users трогаем отдельно: её строки не переносятся, а удаляются.
SKIP_TABLES = {"users"}

FK_QUERY = """
SELECT tc.table_name AS t, kcu.column_name AS c
FROM information_schema.table_constraints tc
JOIN information_schema.key_column_usage kcu
  ON kcu.constraint_name = tc.constraint_name
JOIN information_schema.constraint_column_usage ccu
  ON ccu.constraint_name = tc.constraint_name
WHERE tc.constraint_type = 'FOREIGN KEY'
  AND ccu.table_name = 'users' AND ccu.column_name = 'id'
ORDER BY 1, 2
"""


async def describe(session: Any, uid: int) -> dict[str, Any] | None:
    row = (
        await session.execute(
            text(
                "SELECT id, telegram_id, name, role::text AS role, email, "
                "       current_subscription_id "
                "FROM users WHERE id = :id"
            ),
            {"id": uid},
        )
    ).mappings().first()
    return dict(row) if row else None


async def run(src_id: int, dst_id: int, apply: bool) -> int:
    from src.core.config import AppConfig

    engine = create_async_engine(AppConfig.get().database.dsn)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with Session() as session:
            src = await describe(session, src_id)
            dst = await describe(session, dst_id)
            if not src or not dst:
                print(f"Нет такой записи: {src_id if not src else dst_id}", file=sys.stderr)
                return 1

            print(f"ДВОЙНИК  #{src['id']}: telegram={src['telegram_id']} "
                  f"имя={src['name']!r} роль={src['role']}")
            print(f"ЖИВАЯ    #{dst['id']}: telegram={dst['telegram_id']} "
                  f"имя={dst['name']!r} роль={dst['role']}")
            print()

            # Предохранители. Отказаться дешевле, чем чинить последствия.
            if src["telegram_id"] is not None:
                print("ОТКАЗ: у двойника есть Telegram ID — это не служебная запись, а\n"
                      "       живой человек. Слияние двух настоящих аккаунтов этим\n"
                      "       скриптом не делается.", file=sys.stderr)
                return 1
            if dst["telegram_id"] is None:
                print("ОТКАЗ: у целевой записи нет Telegram ID — переносить некуда.",
                      file=sys.stderr)
                return 1
            if str(src["role"]) != "USER":
                print(f"ОТКАЗ: у двойника роль {src['role']}, а не USER.", file=sys.stderr)
                return 1

            fks = [(r["t"], r["c"]) for r in (await session.execute(text(FK_QUERY))).mappings()
                   if r["t"] not in SKIP_TABLES]

            moved: list[tuple[str, str, int]] = []
            for table, column in fks:
                n = (await session.execute(
                    text(f'SELECT count(*) FROM "{table}" WHERE "{column}" = :id'),
                    {"id": src_id},
                )).scalar() or 0
                if n:
                    moved.append((table, column, int(n)))

            if not moved:
                print("На двойнике нет ни одной строки — переносить нечего, он просто удалится.")
            else:
                print(f"Переедет на #{dst_id} — таблиц: {len(moved)}")
                for table, column, n in moved:
                    # Сколько уже есть у целевой записи: если колонка уникальна
                    # (одна строка на пользователя), перенос упрётся в ограничение,
                    # и лучше увидеть это ДО, а не в середине транзакции.
                    have = (await session.execute(
                        text(f'SELECT count(*) FROM "{table}" WHERE "{column}" = :id'),
                        {"id": dst_id},
                    )).scalar() or 0
                    mark = "  ⚠ у целевой записи уже есть строки" if have else ""
                    print(f"    {table}.{column}: {n}{mark}")

            if not apply:
                # Сухой прогон обязан показывать и ПОТЕРИ, а не только переезды:
                # колонки самой записи двойника (почта, пароль, способ входа,
                # реф-код) никуда не переносятся — запись удаляется вместе с ними.
                # Для служебной записи из синхрона там обычно пусто, но увидеть это
                # надо ДО удаления, а не после.
                lost = [k for k in ("email", "name") if src.get(k)]
                if lost:
                    print()
                    print("Будет удалено вместе с двойником (не переносится):")
                    for k in lost:
                        print(f"    users.{k}: {src[k]!r}")
                print()
                print("Сухой прогон. Ничего не изменено. Повторите с --apply, "
                      "если список выше устраивает.")
                return 0

            # Транзакцию НЕ открываем явно: сессия уже начала её на чтениях выше,
            # и `session.begin()` здесь падает с «transaction is already begun».
            # Всё, что ниже, идёт одной транзакцией — до commit. Любая ошибка
            # откатывает её целиком: половина слияния хуже, чем ни одной.
            try:
                # 1) Гасим у двойника ссылку на подписку ДО переноса: иначе она
                #    останется указывать на строку, уехавшую к другому человеку.
                await session.execute(
                    text("UPDATE users SET current_subscription_id = NULL WHERE id = :id"),
                    {"id": src_id},
                )
                # 2) Переносим всё, что ссылалось на двойника.
                for table, column, _n in moved:
                    await session.execute(
                        text(f'UPDATE "{table}" SET "{column}" = :dst WHERE "{column}" = :src'),
                        {"dst": dst_id, "src": src_id},
                    )
                # 3) Если у живой записи текущей подписки не было — ставим
                #    перенесённую, иначе человек так и не увидит её в боте.
                await session.execute(
                    text(
                        "UPDATE users SET current_subscription_id = ("
                        "  SELECT id FROM subscriptions WHERE user_id = :dst "
                        "  ORDER BY expire_at DESC NULLS LAST LIMIT 1) "
                        "WHERE id = :dst AND current_subscription_id IS NULL"
                    ),
                    {"dst": dst_id},
                )
                # 4) Только теперь удаляем двойника: все ссылки каскадные, и до
                #    переноса удаление утащило бы с собой подписку.
                await session.execute(
                    text("DELETE FROM users WHERE id = :id"), {"id": src_id}
                )
                await session.commit()
            except Exception as exc:  # noqa: BLE001 — показываем причину как есть
                await session.rollback()
                print(f"\nНЕ СЛИТО, всё откачено: {exc}", file=sys.stderr)
                return 1

            # Проверяем РЕЗУЛЬТАТ, а не код возврата: однажды слияние «прошло»,
            # не изменив ничего (SQL ушёл в никуда), и заметила это только сверка.
            left = await describe(session, src_id)
            subs = (await session.execute(
                text("SELECT count(*) FROM subscriptions WHERE user_id = :id"), {"id": dst_id}
            )).scalar()
            print()
            print(f"Готово. Двойник #{src_id}: {'УДАЛЁН' if left is None else 'ВСЁ ЕЩЁ НА МЕСТЕ — разберитесь!'}")
            print(f"Подписок у #{dst_id}: {subs}")
            return 0 if left is None else 1
    finally:
        await engine.dispose()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="src", type=int, required=True, help="двойник (будет удалён)")
    ap.add_argument("--to", dest="dst", type=int, required=True, help="живая запись человека")
    ap.add_argument("--apply", action="store_true", help="выполнить (без него — сухой прогон)")
    args = ap.parse_args()
    if args.src == args.dst:
        print("Это одна и та же запись.", file=sys.stderr)
        return 1
    return asyncio.run(run(args.src, args.dst, args.apply))


if __name__ == "__main__":
    sys.exit(main())
