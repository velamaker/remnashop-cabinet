"""Заработок пользователя по реферальной программе — для дашборда в кабинете.

Базовый `/referral/program` (в base-образе) отдаёт код/кол-во приглашённых/уровни,
но НЕ сумму заработка. Достраиваем её overlay-ручкой поверх таблицы referral_rewards
(её пишет базовый AssignReferralRewards): `amount` там — величина награды в единицах
типа (для POINTS/PERCENT это РУБЛИ платёж×%, для EXTRA_DAYS — дни). Суммируем только
уже выданные (is_issued=true). Тип награды фронт уже знает из /referral/program и сам
форматирует (₽ / дни / ≈баллы).
"""

from datetime import timedelta
from typing import Any

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.use_cases.referral.commands.attachment import (
    AttachReferral,
    AttachReferralDto,
)
from src.core.utils.time import datetime_now
from src.web.endpoints.public._common import CurrentUser

# Насколько «свежим» должен быть аккаунт, чтобы принять реф-код. Смысл окна —
# отсечь ретро-привязку: без него любой давний пользователь мог бы в любой момент
# записаться под чужой код и приносить тому награды. Час взят с запасом на
# небыстрые сценарии (OIDC-редирект через oauth.telegram.org, ввод почты, 2FA).
ATTACH_WINDOW = timedelta(hours=1)

router = APIRouter(prefix="/referral", tags=["Public - Referral"])


@router.get("/earnings")
@inject
async def get_referral_earnings(
    user: CurrentUser,
    session: FromDishka[AsyncSession],
) -> dict[str, Any]:
    row = (
        await session.execute(
            text(
                "SELECT COALESCE(SUM(amount), 0) AS earned, COUNT(*) AS rewards "
                "FROM referral_rewards WHERE user_id = :uid AND is_issued = true"
            ),
            {"uid": user.id},
        )
    ).one()
    return {"earned": int(row.earned or 0), "rewards_count": int(row.rewards or 0)}


class AttachReferralBody(BaseModel):
    code: str = Field(min_length=1, max_length=64)


async def decide_attach(user: Any, code: str, attach_referral: AttachReferral) -> bool:
    """Само решение, вынесенное из ручки: её `@inject` не даёт позвать себя в тесте.

    Возвращает True, только если приглашение действительно записано.
    """
    created_at = getattr(user, "created_at", None)
    if created_at is None or datetime_now() - created_at > ATTACH_WINDOW:
        logger.info(f"{user.log} реф-код после входа отклонён: аккаунт не новый")
        return False

    referrer = await attach_referral.system(
        AttachReferralDto(user_id=user.id, referral_code=code.strip())
    )
    if referrer is None:
        return False

    logger.info(f"{user.log} приглашение засчитано по коду '{code.strip()}' после входа")
    return True


@router.post("/attach")
@inject
async def attach_referral_after_signup(
    body: AttachReferralBody,
    user: CurrentUser,
    attach_referral: FromDishka[AttachReferral],
) -> dict[str, bool]:
    """Досчитать приглашение тому, кто вошёл через Telegram.

    ЗАЧЕМ. При почтовой регистрации реф-код едет в теле запроса и привязывается
    сразу. У входа через Telegram такого поля нет вовсе — ни в схеме, ни в
    `_get_or_create_telegram_user`, который зовёт `RegisterWebUserDto(user=...)`
    без кода. В итоге приглашённый регистрировался, а связь не создавалась, молча:
    пригласивший не видел ни приглашения, ни награды. При 1069 телеграм-аккаунтах
    из 1090 это означало, что реферальная программа кабинета не работала вовсе.

    ПОЧЕМУ ОТДЕЛЬНЫМ ШАГОМ, А НЕ ПОЛЕМ В ФОРМЕ ВХОДА. Протащить код через вход
    пришлось бы правкой схемы запроса, ручки и сценария авторизации — три чужих
    файла на пути, по которому ходят все входы в кабинет. Здесь то же самое
    делается после того, как человек уже вошёл, и вход остаётся нетронутым.

    ЧЕМ ЭТО БЕЗОПАСНО. Решение принимает базовый `AttachReferral`: он сам
    отказывает, если рефералка выключена, код чужой, код свой собственный или у
    человека уже есть пригласивший. Нам остаётся закрыть единственное, чего он не
    знает, — что вызов пришёл не из формы регистрации: принимаем код только у
    аккаунта не старше ATTACH_WINDOW. Ответ всегда один и тот же, без подробностей:
    по нему нельзя проверить чужой код на существование.
    """
    return {"success": await decide_attach(user, body.code, attach_referral)}
