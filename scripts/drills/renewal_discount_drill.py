#!/usr/bin/env python3
"""Проверка скидки на продление на ВОССТАНОВЛЕННОЙ копии базы — до включения на бою.

ЗАЧЕМ. Тесты гоняют правила и порядок записи на подделках, а настоящий SQL выдачи
и погашения можно проверить только на настоящей схеме с настоящими данными.
Включать фичу на бою, не прогнав её хоть раз на копии, — значит проверять её на
живых людях.

ЧТО ДЕЛАЕТ (всё — на копии, агрегатами, без имён и id):
  1) предпросмотр на «сейчас»: сколько получили бы скидку и почему остальные нет;
  2) прогон крона с ВКЛЮЧЁННОЙ фичей и отправщиками-списками: выдано должно
     совпасть с предпросмотром, отправщики — получить ровно по вызову на выдачу;
  3) повторный прогон: ноль новых выдач и ноль сообщений;
  4) время сдвигается за срок последней скидки: погашение снимает скидки, выдачи
     становятся expired.

ПОЧЕМУ ЖИВЫМ ЛЮДЯМ НИЧЕГО НЕ УЙДЁТ. Отправщики — списки в памяти, Notifier и push
не создаются вовсе. Сверх того запуск идёт из db-restore-verify.sh в сети
--internal с фальшивым BOT_TOKEN: даже ошибка здесь не дотянулась бы до Telegram.

Запуск:
  BACKUP_DIR=/opt/remnashop-backups DRILL_HOOK=scripts/drills/renewal_discount_drill.py \\
      scripts/db-restore-verify.sh

Коды возврата: 0 — всё сошлось; 1 — расхождение (в выводе — какое).
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


async def _scalar(session: Any, sql: str) -> int:
    return int((await session.execute(text(sql))).scalar_one() or 0)


async def run() -> int:
    from src.core.config import AppConfig
    from src.infrastructure.services import overlay_renewal_discount as rd
    from src.infrastructure.taskiq.tasks.renewal_discount import run_once

    engine = create_async_engine(AppConfig.get().database.dsn)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    problems: list[str] = []

    tg_calls: list[int] = []
    push_calls: list[int] = []

    async def send_tg(user_id: int, payload: Any) -> str:
        tg_calls.append(user_id)
        return rd.TG_SENT

    async def send_push(user_id: int, lang: str, messages: dict) -> int:
        push_calls.append(user_id)
        return 0

    async def no_sleep(_: float) -> None:
        return None

    cfg = rd._normalize({**rd.DEFAULT_CONFIG, "enabled": True})
    env = rd.Env.current()
    now = datetime.now(timezone.utc)

    try:
        async with Session() as session:
            discount_before = await _scalar(session, "SELECT count(*) FROM users WHERE purchase_discount > 0")
            grants_before = await _scalar(session, "SELECT count(*) FROM renewal_discount_grants")

            pv = await rd.preview(session, cfg, now, 0, env, hide_ids=True)
            print(f"конфиг: {cfg}")
            print(f"предпросмотр: осмотрено {pv['examined']}, выдали бы {pv['would_grant']}, отказы {pv['skipped']}")

            first = await run_once(
                session, send_tg=send_tg, send_push=send_push, now=now, cfg=cfg, env=env, sleep=no_sleep
            )
            print(
                f"прогон 1: выдано {first['granted']}, гонок {first['lost_race']}, ошибок {first['errors']}, "
                f"telegram {dict(first['tg'])}, отказы {dict(first['skipped'])}"
            )
            expected = min(pv["would_grant"], 50)
            if first["granted"] != expected:
                problems.append(f"выдано {first['granted']}, а предпросмотр обещал {expected}")
            with_tg = sum(v for k, v in first["tg"].items() if k == rd.TG_SENT)
            if with_tg != len(tg_calls):
                problems.append(f"в Telegram «отправлено» {with_tg}, а вызовов отправщика {len(tg_calls)}")
            if len(push_calls) != first["granted"]:
                problems.append(f"push-вызовов {len(push_calls)} при {first['granted']} выдачах")
            if first["errors"]:
                problems.append(f"ошибок в прогоне: {first['errors']}")

            grants_after = await _scalar(session, "SELECT count(*) FROM renewal_discount_grants")
            discount_after = await _scalar(session, "SELECT count(*) FROM users WHERE purchase_discount > 0")
            by_status = (
                await session.execute(
                    text("SELECT status, tg_status, count(*) FROM renewal_discount_grants GROUP BY 1, 2 ORDER BY 1, 2")
                )
            ).all()
            print(f"выдач в таблице: было {grants_before}, стало {grants_after}; по статусам {[tuple(r) for r in by_status]}")
            print(f"людей со скидкой: было {discount_before}, стало {discount_after}")
            if grants_after - grants_before != first["granted"]:
                problems.append("строк выдач прибавилось не столько, сколько выдано")
            if discount_after - discount_before != first["granted"]:
                problems.append("скидок на людях прибавилось не столько, сколько выдано")

            calls_before = (len(tg_calls), len(push_calls))
            second = await run_once(
                session, send_tg=send_tg, send_push=send_push, now=now, cfg=cfg, env=env, sleep=no_sleep
            )
            print(f"прогон 2: выдано {second['granted']}, досылка {second['resent']}")
            if second["granted"] or second["resent"] or (len(tg_calls), len(push_calls)) != calls_before:
                problems.append("повторный прогон выдал или отправил что-то ещё раз")

            last = (
                await session.execute(
                    text("SELECT max(expires_at) FROM renewal_discount_grants WHERE status = 'active'")
                )
            ).scalar_one()
            if last is not None:
                later = last + timedelta(minutes=1)
                # Выключено: погашение идёт всегда, а новых выдач в сдвинутом окне не будет.
                third = await run_once(
                    session,
                    send_tg=send_tg,
                    send_push=send_push,
                    now=later,
                    cfg={**cfg, "enabled": False},
                    env=env,
                    sleep=no_sleep,
                )
                still_active = await _scalar(
                    session, "SELECT count(*) FROM renewal_discount_grants WHERE status = 'active'"
                )
                discount_final = await _scalar(session, "SELECT count(*) FROM users WHERE purchase_discount > 0")
                print(
                    f"погашение на {later.isoformat()}: сгорело {third['expired']}, воспользовались {third['used']}, "
                    f"открытых осталось {still_active}, людей со скидкой {discount_final}"
                )
                if still_active:
                    problems.append(f"после срока открытых выдач осталось {still_active}")
                if discount_final > discount_before:
                    problems.append("погашение не сняло выданные скидки")
    finally:
        await engine.dispose()

    if problems:
        for p in problems:
            print(f"РАСХОЖДЕНИЕ: {p}", file=sys.stderr)
        return 1
    print("renewal_discount drill: всё сошлось")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
