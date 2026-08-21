"""Резервный доступ истёкшим подпискам: 1 ГБ на N дней (overlay).

Крон (почасовой): находит USER, у кого подписка истекла недавно (в пределах окна) и
кому резерв ещё не выдавали, через Remnawave SDK делает их ACTIVE на `window_days`
дней с лимитом `reserve_gb` ГБ (сброс использованного) — на текущем скваде или на
отдельном сквад-резерве (squad_uuid). Пишет строку в reserve_grants. Шлёт
goodwill-уведомление (Web Push).

ДЕДУП — НА ЦИКЛ ПОДПИСКИ, а не на человека: резерв положен на КАЖДОЕ истечение.
Пропускаем того, у кого резерв сейчас действует или уже выдавался за текущее истечение
(granted_at не раньше subscriptions.expire_at). Продлился — срок подписки уехал вперёд,
и следующее истечение снова даёт право на резерв. В схеме это держит частичный
уникальный индекс по user_id среди незакрытых строк (миграция 0005).

СКВАДЫ — почему им отдельная забота. Панель меняет сквады юзера ТОЛЬКО если в PATCH
пришло поле activeInternalSquads (users.service: `if (newActiveInternalSquadsUuids)`);
при пустом squad_uuid мы его не шлём вовсе. А подписка отдаёт клиенту серверы именно
через сквады: юзер без сквадов — ACTIVE, со сроком и лимитом, но в приложении ПУСТО.
Поэтому при пустом squad_uuid сначала смотрим, что у человека в панели, и если сквадов
нет — возвращаем те, что были у его подписки (subscriptions.internal_squads). Совсем
нечего вернуть — резерв НЕ выдаём и говорим об этом в логе: «активен, но подключиться
некуда» хуже честного отказа, потому что выглядит как рабочий резерв.

ПОРЯДОК ВЫЗОВОВ. Сброс трафика идёт ПЕРВЫМ, PATCH — вторым. Если между ними что-то
падает, человек остаётся просто истёкшим (безобидно). В обратном порядке сорвавшийся
сброс оставлял бы его ACTIVE с лимитом 1 ГБ поверх израсходованных десятков — панель
пометит LIMITED, и получится ровно та же пустая подписка, но уже незаметно для нас.

РЕЗУЛЬТАТ ПРОВЕРЯЕМ. update_user возвращает юзера целиком (статус, сквады) — сверяем
ответ панели и пишем в reserve_grants только то, что реально применилось. Иначе админка
показывает «резерв выдан» там, где у клиента ничего не работает.

ОБСЛУЖИВАНИЕ УЖЕ ВЫДАННОГО идёт каждым проходом и НЕ зависит от тумблера — эти люди на
резерве уже сидят: закрываем вышедшие окна (ended) и перепроверяем действующие резервы,
доводя до рабочего состояния те, что остались без сквадов (см. _broken_reserve — там же
объяснено, почему израсходованный гигабайт чинить нельзя). Без этого исправленная выдача
помогала бы только новым, а те, кому резерв уже «выдали» пустым, не получили бы его
никогда: строка в reserve_grants блокирует повторную выдачу навсегда.

Окончание НЕ требует отдельной логики: резерв ставит expireAt = now + window_days,
который сам истекает в конце окна → панель авто-помечает EXPIRED → «подписка
закончилась». (Панель ЗАПРЕЩАЕТ ставить expireAt в прошлое, так что ручное истечение
и невозможно, и не нужно.) Продлился раньше — покупка перезапишет срок/лимит/сквад.

Надпись приходит сама из customRemarks панели (уже по-русски): израсходовал 1 ГБ →
LIMITED «кончился трафик»; окно вышло → EXPIRED «подписка закончилась».

Конфиг assets/reserve.json (админка). Дефолт ВЫКЛ. Ядро/биллинг НЕ трогаем — только
меняем срок/лимит/сквад юзера в панели. Best-effort: ошибка по одному не роняет проход.
"""

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Optional

from dishka.integrations.taskiq import FromDishka, inject
from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.common import Remnawave
from src.infrastructure.services.overlay_push import notify_user_push
from src.infrastructure.services.overlay_reserve import load_config
from src.infrastructure.taskiq.broker import broker

_GB = 1024 ** 3

_MSG = {
    "ru": (
        "🛟 Резервный доступ включён",
        "Подписка закончилась. Мы оставили резервный доступ {gb} ГБ на {days} дн. — "
        "продлите в кабинете, чтобы не потерять сервис.",
    ),
    "en": (
        "🛟 Reserve access granted",
        "Your subscription ended. We left you {gb} GB reserve for {days} days — "
        "renew in the cabinet to keep your service.",
    ),
}


def _squad_uuids(user: object) -> list[str]:
    """UUID активных сквадов из ответа панели (элементы — объекты {uuid, name})."""
    out: list[str] = []
    for squad in getattr(user, "active_internal_squads", None) or []:
        uuid = getattr(squad, "uuid", None)
        if uuid is None and isinstance(squad, dict):
            uuid = squad.get("uuid")
        if uuid:
            out.append(str(uuid))
    return out


async def _plan_squads(session: AsyncSession, user_id: int) -> list[str]:
    """Сквады подписки человека — чтобы вернуть его на ЕГО ЖЕ серверы, а не в никуда.

    Колонка есть не во всех версиях базового образа, поэтому запрос защищённый: нет
    колонки — просто нет фолбэка, проход от этого не падает. Ошибка ломает транзакцию
    сессии, отсюда rollback.
    """
    try:
        raw = (
            await session.execute(
                text(
                    "SELECT s.internal_squads FROM users u "
                    "JOIN subscriptions s ON u.current_subscription_id = s.id "
                    "WHERE u.id = :u"
                ),
                {"u": user_id},
            )
        ).scalar()
    except Exception as exc:  # noqa: BLE001 — фолбэк опциональный
        await session.rollback()
        logger.debug(f"reserve: сквады подписки недоступны ({exc})")
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return []
    return [str(x) for x in (raw or []) if x]


def _not_applied(user: object) -> Optional[str]:
    """None — резерв реально применился; иначе причина, по которой он бесполезен.

    Формат ответа SDK неизвестен (база может обновиться) → отсутствие поля статуса
    считаем «проверить не смогли» и грант не блокируем: молча ломать рабочую установку
    хуже, чем не поймать один сбой.
    """
    status = getattr(user, "status", None)
    if status is None:
        return None
    if str(getattr(status, "value", status)) != "ACTIVE":
        return f"панель оставила статус {status}"
    if not _squad_uuids(user):
        return "у юзера нет активных сквадов — подписка отдаст пустой список серверов"
    return None


async def _apply_reserve(
    sdk: object,
    session: AsyncSession,
    user_id: int,
    uuid: object,
    expire_at: datetime,
    gb: int,
    squad: str,
) -> Optional[str]:
    """Ставит человеку резервный доступ в панели.

    None — резерв реально применился; строка — причина, по которой он бесполезен.
    Через этот же путь идёт и починка уже выданных резервов, поэтому срок передаётся
    снаружи: починка НЕ должна продлевать окно, она возвращает доступ в прежних рамках.
    """
    from remnapy.enums.users import UserStatus
    from remnapy.models import UpdateUserRequestDto

    uuid_s = str(uuid)

    # Сброс расхода — ПЕРВЫМ шагом: если следующий сорвётся, человек останется
    # просто истёкшим, а не «активным с лимитом 1 ГБ поверх израсходованных».
    await sdk.users.reset_user_traffic(uuid_s)

    # Сквад-резерв задан — переносим на него. Не задан — панель сквады не тронет,
    # и это правильно ровно до тех пор, пока сквады у человека ЕСТЬ.
    squads = [squad] if squad else []
    if not squads and not _squad_uuids(await sdk.users.get_user_by_uuid(uuid_s)):
        squads = await _plan_squads(session, user_id)
        if not squads:
            return (
                "у юзера нет активных сквадов, а сквад-резерв в настройках не задан — "
                "резерв дал бы «активную» подписку без единого сервера"
            )

    body = UpdateUserRequestDto(
        uuid=uuid,
        status=UserStatus.ACTIVE,
        expire_at=expire_at,
        traffic_limit_bytes=gb * _GB,
    )
    if squads:
        body.active_internal_squads = squads
    # Панель отвечает юзером целиком — сверяем, что получилось именно то, что нужно.
    return _not_applied(await sdk.users.update_user(body))


def _broken_reserve(user: object) -> Optional[str]:
    """Резерв ВЫДАН, но пользоваться им нельзя — то, что чинится повторной выдачей.

    Условие намеренно уже, чем у _not_applied: LIMITED здесь НЕ поломка, а штатный
    конец резерва («израсходовал гигабайт»). Чинить его повторной выдачей значило бы
    раздавать по свежему гигабайту каждый час — резерв перестал бы кончаться вовсе.
    Чиним ровно один случай: активен, но сквадов нет, то есть подписка пустая.
    """
    status = getattr(user, "status", None)
    if status is None:
        return None
    if str(getattr(status, "value", status)) != "ACTIVE":
        return None
    if not _squad_uuids(user):
        return "активен, но без сквадов — подписка отдаёт пустой список серверов"
    return None


async def _close_finished(session: AsyncSession) -> int:
    """Закрывает резервы, чьё окно вышло: колонка ended до сих пор не выставлялась.

    Сам доступ гасить не нужно — expireAt истекает в панели сам. Отметка нужна нам:
    по ней видно, кто на резерве СЕЙЧАС, и по ней же не перепроверяется то, что уже
    закончилось.
    """
    result = await session.execute(
        text("UPDATE reserve_grants SET ended = true WHERE ended = false AND reserve_expire_at < now()")
    )
    await session.commit()
    return result.rowcount or 0


async def _repair_active(session: AsyncSession, sdk: object, gb: int, squad: str) -> int:
    """Доводит до рабочего состояния резервы, которые уже выданы, но ничего не дают.

    Раньше строка в reserve_grants писалась по факту вызова панели, без проверки
    результата, — и человек мог остаться ACTIVE без сквадов (в приложении пусто).
    Такая строка блокирует повторную выдачу навсегда, поэтому чинить их обязательно:
    иначе исправленная выдача помогает только новым, а уже пострадавшим — никогда.

    Идёт и при ВЫКЛЮЧЕННОЙ фиче: у этих людей резерв уже есть, и бросать их на
    полпути нельзя (тот же принцип, что у авто-возобновления пауз в freeze.py).
    """
    rows = (
        await session.execute(
            text(
                # s.expire_at < now() — обязательное условие, а не украшение: резерв
                # НЕ двигает локальный срок подписки, поэтому «в прошлом» означает «ещё
                # не продлился». Без этой проверки человек, который уже оплатил, но
                # оказался в панели без сквадов (например, у тарифа их не задали), был
                # бы «починен» до резервного гигабайта — то есть у оплатившего отобрали
                # бы подписку.
                "SELECT r.user_id, r.remna_uuid, r.reserve_expire_at "
                "FROM reserve_grants r "
                "JOIN users u ON u.id = r.user_id "
                "JOIN subscriptions s ON u.current_subscription_id = s.id "
                "WHERE r.ended = false AND r.reserve_expire_at > now() "
                "AND s.expire_at < now()"
            )
        )
    ).all()

    fixed = 0
    for uid, uuid, expire_at in rows:
        try:
            problem = _broken_reserve(await sdk.users.get_user_by_uuid(str(uuid)))
        except Exception as exc:  # noqa: BLE001 — один недоступный юзер не роняет проход
            logger.warning(f"reserve: проверка user_id={uid} ({uuid}) не удалась: {exc}")
            continue
        if not problem:
            continue

        logger.warning(f"reserve: у user_id={uid} ({uuid}) резерв не работает ({problem}) — чиню")
        try:
            still = await _apply_reserve(sdk, session, uid, uuid, expire_at, gb, squad)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"reserve: починка user_id={uid} ({uuid}) не удалась: {exc}")
            continue
        if still:
            logger.warning(f"reserve: user_id={uid} ({uuid}) починить не вышло: {still}")
        else:
            fixed += 1
    return fixed


@broker.task(schedule=[{"cron": "27 * * * *"}], retry_on_error=False)
@inject(patch_module=True)
async def run_reserve(
    session: FromDishka[AsyncSession],
    remnawave: FromDishka[Remnawave],
) -> None:
    cfg = load_config()
    gb = cfg["reserve_gb"]
    window = cfg["window_days"]
    squad = cfg["squad_uuid"]

    # Обслуживание уже выданных резервов — ДО проверки тумблера: эти люди на резерве
    # уже сидят, и выключенная фича не повод оставлять их в поломанном состоянии.
    closed = await _close_finished(session)
    if closed:
        logger.info(f"reserve: закрыто окон резерва {closed}")

    sdk = getattr(remnawave, "sdk", None)
    if sdk is None:
        logger.warning("reserve: Remnawave SDK недоступен — пропуск")
        return

    fixed = await _repair_active(session, sdk, gb, squad)
    if fixed:
        logger.info(f"reserve: починено нерабочих резервов {fixed}")

    if not cfg["enabled"]:
        return

    rows = (
        await session.execute(
            text(
                # Дедуп — НА ЦИКЛ ПОДПИСКИ, а не на человека: резерв положен на каждое
                # истечение. Пропускаем, если резерв сейчас действует (ended = false)
                # либо уже выдавался за ТЕКУЩЕЕ истечение (granted_at не раньше
                # s.expire_at). После продления s.expire_at уезжает вперёд, и старая
                # выдача перестаёт закрывать дорогу следующей.
                "SELECT u.id, s.user_remna_id, lower(u.language::text) "
                "FROM users u "
                "JOIN subscriptions s ON u.current_subscription_id = s.id "
                "WHERE u.role = 'USER' AND s.user_remna_id IS NOT NULL "
                "AND s.expire_at < now() "
                "AND s.expire_at > now() - make_interval(days => :w) "
                "AND NOT EXISTS ("
                "  SELECT 1 FROM reserve_grants r "
                "  WHERE r.user_id = u.id "
                "    AND (r.ended = false OR r.granted_at >= s.expire_at)"
                ")"
            ),
            {"w": window},
        )
    ).all()
    if not rows:
        return

    reserve_expire = datetime.now(timezone.utc) + timedelta(days=window)
    granted = 0
    for uid, uuid, lang in rows:
        try:
            problem = await _apply_reserve(sdk, session, uid, uuid, reserve_expire, gb, squad)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"reserve: выдача user_id={uid} ({uuid}) не удалась: {e}")
            continue

        # Не применилось — строку НЕ пишем: следующий проход попробует снова, а владелец
        # видит причину в логе, а не «резерв выдан» при неработающем доступе.
        if problem:
            logger.warning(f"reserve: user_id={uid} ({uuid}) — резерв не выдан: {problem}")
            continue

        try:
            await session.execute(
                text(
                    # Конфликт ловим по частичному уникальному индексу (миграция 0005):
                    # действующая выдача у человека может быть только одна, закрытые
                    # копятся историей. Страхует от гонки, если проходов вдруг два.
                    "INSERT INTO reserve_grants (user_id, remna_uuid, granted_at, reserve_expire_at, ended) "
                    "VALUES (:u, :ru, now(), :re, false) "
                    "ON CONFLICT (user_id) WHERE ended = false DO NOTHING"
                ),
                {"u": uid, "ru": str(uuid), "re": reserve_expire},
            )
            await session.commit()
        except Exception as e:  # noqa: BLE001
            # Воркер и планировщик миграции не гоняют — их накатывает контейнер
            # приложения. Если он не поднялся, индекса из 0005 может не быть, и без
            # этой защиты падал бы весь проход, а не одна строка. Резерв в панели уже
            # применён, человек им пользуется; следующий проход допишет запись.
            await session.rollback()
            logger.warning(
                f"reserve: user_id={uid} ({uuid}) — резерв применён в панели, но запись "
                f"не сохранилась: {e}"
            )
            continue
        granted += 1
        await notify_user_push(
            session,
            SimpleNamespace(id=uid, language=lang),
            _MSG,
            url="/billing",
            tag="reserve",
            gb=gb,
            days=window,
        )

    if granted:
        logger.info(f"reserve: выдано резервов {granted} (по {gb} ГБ / {window} дн.)")
