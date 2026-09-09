"""Резервный доступ истёкшим: сервер для входа в Telegram на N дней (overlay).

Кому кончилась подписка и кто не успел продлить — оставляем не «немного интернета», а
дорогу обратно к боту: сажаем человека на сквад-резерв (сервер, пускающий только в
Telegram), чтобы он дошёл до бота и продлил подписку.

Крон (почасовой): находит USER, у кого подписка истекла недавно (в пределах окна) и
кому резерв за это истечение ещё не выдавали, через Remnawave SDK делает их ACTIVE на
`window_days` дней с лимитом `reserve_gb` ГБ (сброс использованного) НА СКВАД-РЕЗЕРВЕ
(squad_uuid). Пишет строку в reserve_grants. Шлёт уведомление (Web Push).

ДЕДУП — НА ЦИКЛ ПОДПИСКИ, а не на человека: резерв положен на КАЖДОЕ истечение.
Пропускаем того, у кого резерв сейчас действует или уже выдавался за текущее истечение
(granted_at не раньше subscriptions.expire_at). Продлился — срок подписки уехал вперёд,
и следующее истечение снова даёт право на резерв. В схеме это держит частичный
уникальный индекс по user_id среди незакрытых строк (миграция 0005).

СКВАД-РЕЗЕРВ ОБЯЗАТЕЛЕН, и это главное в фиче. Смысл резерва — не «немного интернета
на прощание», а СЕРВЕР ДЛЯ ВХОДА В TELEGRAM, чтобы человек дошёл до бота и продлил
подписку. Такой сервер — это отдельный сквад, чьи ноды пускают только в Telegram;
маршрутизацию задаёт панель, кабинет её не контролирует и контролировать не может.
Наше дело — посадить человека на ЭТОТ сквад и убедиться, что он там оказался.

Поэтому squad_uuid не имеет разумного значения по умолчанию: оставить человеку его
прежние сквады значит выдать полный доступ ко всем серверам, то есть ровно то, за что
он больше не платит. Пусто — резерв НЕ выдаём и пишем причину в лог.

Панель меняет сквады ТОЛЬКО если в PATCH пришло поле activeInternalSquads
(users.service: `if (newActiveInternalSquadsUuids)`) — поэтому шлём его всегда.

ПОРЯДОК ВЫЗОВОВ. Сброс трафика идёт ПЕРВЫМ, PATCH — вторым. Если между ними что-то
падает, человек остаётся просто истёкшим (безобидно). В обратном порядке сорвавшийся
сброс оставлял бы его ACTIVE с лимитом 1 ГБ поверх израсходованных десятков — панель
пометит LIMITED, и получится ровно та же пустая подписка, но уже незаметно для нас.

РЕЗУЛЬТАТ ПРОВЕРЯЕМ. update_user возвращает юзера целиком (статус, сквады) — сверяем
ответ панели и пишем в reserve_grants только то, что реально применилось. Иначе админка
показывает «резерв выдан» там, где у клиента ничего не работает.

ОБСЛУЖИВАНИЕ УЖЕ ВЫДАННОГО идёт каждым проходом и НЕ зависит от тумблера — эти люди на
резерве уже сидят: закрываем вышедшие окна (ended) и перепроверяем действующие резервы,
доводя до рабочего состояния те, что остались без сквадов ИЛИ на чужих сквадах (см.
_broken_reserve — там же объяснено, почему израсходованный гигабайт чинить нельзя). Без этого исправленная выдача
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
        "🛟 Оставили доступ к Telegram",
        "Подписка закончилась. На {days} дн. оставили сервер для входа в Telegram "
        "({gb} ГБ) — продлите подписку, чтобы вернуть остальные серверы.",
    ),
    "en": (
        "🛟 Telegram access kept",
        "Your subscription ended. For {days} days we left a server that reaches "
        "Telegram ({gb} GB) — renew to get the rest back.",
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


def _not_applied(user: object, squad: str) -> Optional[str]:
    """None — резерв реально применился; иначе причина, по которой он бесполезен.

    Проверяем не только «активен», но и «сидит именно на сквад-резерве»: человек с
    прежними сквадами получил бы полный доступ вместо входа в Telegram, а человек без
    сквадов — пустую подписку. И то и другое означает, что резерв не сработал.

    Формат ответа SDK неизвестен (база может обновиться) → отсутствие поля статуса
    считаем «проверить не смогли» и грант не блокируем: молча ломать рабочую установку
    хуже, чем не поймать один сбой.
    """
    status = getattr(user, "status", None)
    if status is None:
        return None
    if str(getattr(status, "value", status)) != "ACTIVE":
        return f"панель оставила статус {status}"
    squads = _squad_uuids(user)
    if not squads:
        return "у юзера нет активных сквадов — подписка отдаст пустой список серверов"
    if squad and [s.lower() for s in squads] != [squad.lower()]:
        return f"юзер не на сквад-резерве, а на {squads} — это не вход в Telegram"
    return None


async def _apply_reserve(
    sdk: object,
    uuid: object,
    expire_at: datetime,
    gb: int,
    squad: str,
) -> Optional[str]:
    """Сажает человека на сквад-резерв — сервер для входа в Telegram.

    None — резерв реально применился; строка — причина, по которой он бесполезен.
    Через этот же путь идёт и починка уже выданных резервов, поэтому срок передаётся
    снаружи: починка НЕ должна продлевать окно, она возвращает доступ в прежних рамках.
    """
    from remnapy.enums.users import UserStatus
    from remnapy.models import UpdateUserRequestDto

    if not squad:
        return (
            "не задан сквад-резерв: без него человек остался бы на прежних серверах "
            "(полный доступ) или вовсе без сквадов — задайте его в админке"
        )

    uuid_s = str(uuid)

    # Сброс расхода — ПЕРВЫМ шагом: если следующий сорвётся, человек останется
    # просто истёкшим, а не «активным с лимитом 1 ГБ поверх израсходованных».
    await sdk.users.reset_user_traffic(uuid_s)

    body = UpdateUserRequestDto(
        uuid=uuid,
        status=UserStatus.ACTIVE,
        expire_at=expire_at,
        traffic_limit_bytes=gb * _GB,
    )
    # Шлём сквад ВСЕГДА: без этого поля панель оставит прежние сквады, то есть полный
    # доступ к сервису, за который человек уже не платит.
    body.active_internal_squads = [squad]
    # Панель отвечает юзером целиком — сверяем, что получилось именно то, что нужно.
    return _not_applied(await sdk.users.update_user(body), squad)


def _broken_reserve(user: object, squad: str) -> Optional[str]:
    """Резерв ВЫДАН, но пользоваться им нельзя — то, что чинится повторной выдачей.

    Условие намеренно уже, чем у _not_applied: LIMITED здесь НЕ поломка, а штатный
    конец резерва («израсходовал гигабайт»). Чинить его повторной выдачей значило бы
    раздавать по свежему гигабайту каждый час — резерв перестал бы кончаться вовсе.

    Чиним два случая, и оба — про сквады. Сквадов нет: подписка пустая, в приложении
    ничего. Сквады чужие: человек сидит на обычных серверах, то есть пользуется полным
    сервисом бесплатно — это и не резерв, и убыток. Второй случай не теоретический:
    до появления обязательного сквад-резерва крон возвращал людям их прежние сквады.
    """
    status = getattr(user, "status", None)
    if status is None:
        return None
    if str(getattr(status, "value", status)) != "ACTIVE":
        return None
    squads = _squad_uuids(user)
    if not squads:
        return "активен, но без сквадов — подписка отдаёт пустой список серверов"
    if squad and [s.lower() for s in squads] != [squad.lower()]:
        return f"активен на чужих сквадах {squads} вместо сквад-резерва — это полный доступ"
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
            problem = _broken_reserve(await sdk.users.get_user_by_uuid(str(uuid)), squad)
        except Exception as exc:  # noqa: BLE001 — один недоступный юзер не роняет проход
            logger.warning(f"reserve: проверка user_id={uid} ({uuid}) не удалась: {exc}")
            continue
        if not problem:
            continue

        logger.warning(f"reserve: у user_id={uid} ({uuid}) резерв не работает ({problem}) — чиню")
        try:
            still = await _apply_reserve(sdk, uuid, expire_at, gb, squad)
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
                # ПРАВИЛО ВЛАДЕЛЬЦА: повторный резерв — ТОЛЬКО после покупки.
                #
                # Раньше дедуп сравнивал granted_at с s.expire_at («выдавали ли уже за
                # это истечение»), и это не работало никогда: выдача резерва САМА
                # двигает s.expire_at на окно вперёд (панель не принимает дату в
                # прошлом). Через окно подписка снова выглядела истёкшей, старая выдача
                # оказывалась раньше нового срока — и резерв выписывался заново, по
                # кругу. К 09.09.2026 так набралось 163 выдачи на 89 человек: пятнадцать
                # получили по четыре подряд, до 34 дней непрерывного доступа, и почти
                # все — ни разу не заплатив.
                #
                # Теперь ворота — сама оплата, а не срок: её резерв подделать не может.
                # Условие одно: после ПОСЛЕДНЕЙ выдачи (а если выдач не было — то вообще
                # когда-либо) была успешная НЕтестовая оплата. Оно же закрывает и триал:
                # бесплатная подписка транзакции не создаёт, значит после пробного
                # периода резерва нет — он там и не нужен. Купил → резерв; после резерва
                # купил снова → резерв снова, и так по кругу. Действующую выдачу, как и
                # раньше, не перевыдаём.
                "SELECT u.id, s.user_remna_id, lower(u.language::text) "
                "FROM users u "
                "JOIN subscriptions s ON u.current_subscription_id = s.id "
                "WHERE u.role = 'USER' AND s.user_remna_id IS NOT NULL "
                "AND s.expire_at < now() "
                "AND s.expire_at > now() - make_interval(days => :w) "
                "AND NOT EXISTS ("
                "  SELECT 1 FROM reserve_grants r "
                "  WHERE r.user_id = u.id AND r.ended = false"
                ") "
                "AND EXISTS ("
                "  SELECT 1 FROM transactions t "
                "  WHERE t.user_id = u.id AND t.status = 'COMPLETED' "
                "    AND t.is_test = false "
                # COALESCE с -infinity: выдач не было — значит годится любая оплата.
                "    AND t.created_at > COALESCE("
                "      (SELECT max(r.granted_at) FROM reserve_grants r WHERE r.user_id = u.id),"
                "      '-infinity'::timestamptz"
                "    )"
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
            problem = await _apply_reserve(sdk, uuid, reserve_expire, gb, squad)
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
