"""Семейные профили: сверка с подпиской владельца, каждые 5 минут (overlay).

ЧЕТЫРЕ ДЕЛА ЗА ПРОХОД:
  1. Семьи, у которых что-то поменялось после последней сверки (подписка владельца,
     сам владелец, условия тарифа), — привести профили к равенству: срок и лимиты,
     приостановка и возобновление, удаление по истечении отсрочки.
  2. Раз в час (проход в начале часа) — то же для ВСЕХ семей: страховка от того,
     что изменение прошло мимо отметок времени (правка базы сырым SQL и т. п.).
  3. Брошенные посередине «создаю» и «удаляю» — довести: профиль, созданный в
     панели перед падением процесса, находится по имени `rs_fam_<id>`.
  4. Раз в час — сироты: пользователи панели `rs_fam_*`, за которыми у нас нет
     живой строки. Одно сообщение владельцу бота на новую сироту.

Хук оплаты (overlay_patches/gateway_payment.py) делает п. 1 сразу после продления
или смены тарифа; крон — страховка на случай, если хук не дошёл, и единственный
путь для продлений мимо шлюза (с баланса, автопродление, выдача админом).

Тумблер функции крон НЕ слушает: уже выданные профили обязаны приостановиться
вместе с владельцем, а не жить бесплатно. Пока профилей нет вовсе (функцию не
включали), проход заканчивается одним запросом.

Задача обнаруживается taskiq по глобу tasks/*.py — регистрировать её негде.
"""

from typing import Any

from dishka.integrations.taskiq import FromDishka, inject
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.common import Remnawave
from src.application.common.dao import SubscriptionDao
from src.infrastructure.services import overlay_family as family
from src.infrastructure.services.overlay_cron_guard import (
    cron_failed,
    cron_guard,
    cron_skipped,
    send_to_owner,
)
from src.infrastructure.taskiq.broker import broker

# Сколько семей сверяем за проход. Крон каждые 5 минут — хвост уйдёт следующим.
MAX_OWNERS_PER_RUN = 300
# Полный проход — в первые минуты часа: крон */5 попадает туда ровно один раз.
FULL_PASS_MINUTE = 5


async def run_pass(
    session: AsyncSession, sdk: Any, *, subscription_dao: Any, full: bool
) -> dict:
    """Один проход без обвязки taskiq — его же зовут тесты."""
    config = family.load_config()
    now = family.now_utc()
    summary: dict[str, Any] = {"owners": 0, "errors": 0, "orphans": []}
    for owner_id in await family.owners_to_reconcile(session, full=full, limit=MAX_OWNERS_PER_RUN):
        try:
            out = await family.reconcile_owner(
                session, sdk, owner_id, actor="cron", config=config, now=now
            )
        except family.FamilyBusy:
            # Семью прямо сейчас меняет человек или хук оплаты — сверим следующим
            # проходом; ждать очередь крону незачем.
            summary["busy"] = summary.get("busy", 0) + 1
            continue
        except Exception as exc:  # noqa: BLE001 — одна семья не мешает остальным
            await session.rollback()
            summary["errors"] += 1
            logger.warning(f"family: сверка семьи владельца {owner_id} не прошла: {exc}")
            continue
        summary["owners"] += 1
        if out.get("errors"):
            summary["errors"] += len(out["errors"])
    summary["pending"] = await family.sweep_pending(session, sdk, subscription_dao=subscription_dao)
    if full:
        try:
            found = await family.orphan_scan(session, sdk)
        except Exception as exc:  # noqa: BLE001 — поиск сирот не отменяет сверку
            await session.rollback()
            logger.warning(f"family: поиск сирот в панели не прошёл: {exc}")
            return summary
        # Список «уже называли» переписывается и пустым: сирота, которую удалили и
        # которая потом появилась снова, — новая новость.
        fresh = family.orphans_to_report(found)
        summary["orphans"] = fresh
        if fresh:
            try:
                await send_to_owner(family.orphans_text(fresh))
            except Exception as exc:  # noqa: BLE001 — сообщение не роняет проход
                logger.warning(f"family: владельцу бота о сиротах не сообщено: {exc}")
    return summary


@broker.task(schedule=[{"cron": "*/5 * * * *"}], retry_on_error=False)
@inject(patch_module=True)
@cron_guard("family", "Семейные профили: сверка с подпиской владельца")
async def run_family_tick(
    session: FromDishka[AsyncSession],
    remnawave: FromDishka[Remnawave],
    subscription_dao: FromDishka[SubscriptionDao],
) -> None:
    if not await family.has_any_profiles(session):
        await session.rollback()
        cron_skipped()
        return
    await session.rollback()
    sdk = getattr(remnawave, "sdk", None)
    if sdk is None:
        logger.warning("family: панель недоступна — проход пропущен")
        cron_failed("панель недоступна — проход пропущен")
        return
    full = family.now_utc().minute < FULL_PASS_MINUTE
    summary = await run_pass(session, sdk, subscription_dao=subscription_dao, full=full)
    if summary["owners"] or summary["errors"] or summary["orphans"]:
        logger.info(f"family: {'полная ' if full else ''}сверка — {summary}")
