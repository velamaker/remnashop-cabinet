from typing import Any, get_type_hints

from adaptix import Retort
from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.common.dao import PaymentGatewayDao
from src.application.use_cases.gateways.commands.payment import CreateTestPayment

from ._common import AdminUser

router = APIRouter(prefix="/gateways", tags=["Admin - Gateways"])

# Поля настроек, не относящиеся к учётным данным (их через форму ключей не правим).
_NON_CRED_FIELDS = {"display_name"}


def _gateway_fields(g: Any) -> list[dict[str, Any]]:
    """Список полей-ключей шлюза: имя, секрет ли, заполнено ли (без значений)."""
    st = getattr(g, "settings", None)
    if st is None:
        return []
    out: list[dict[str, Any]] = []
    for name, typ in get_type_hints(type(st)).items():
        if name in _NON_CRED_FIELDS:
            continue
        val = getattr(st, name, None)
        # Подсказка «что введено»: последние 4 символа (для секретов — тоже только
        # хвост, остальное скрыто). Короткие значения (≤4) показываем целиком.
        hint = None
        if val is not None:
            s = val.get_secret_value() if hasattr(val, "get_secret_value") else str(val)
            hint = s if len(s) <= 4 else "…" + s[-4:]
        out.append(
            {
                "name": name,
                "secret": "SecretStr" in str(typ),
                "is_set": val is not None,
                "hint": hint,
            }
        )
    return out


def _gateway_to_dict(g: Any) -> dict[str, Any]:
    gtype = g.type.value if hasattr(g.type, "value") else str(g.type)
    currency = g.currency.value if hasattr(g.currency, "value") else str(g.currency)
    is_configured = g.settings.is_configured if g.settings else False
    return {
        "id": g.id,
        "type": gtype,
        "currency": currency,
        "is_active": g.is_active,
        "is_configured": is_configured,
        "order_index": g.order_index,
        "display_name": g.settings.display_name if g.settings else None,
    }


@router.get("")
@inject
async def list_gateways(
    _admin: AdminUser,
    gateway_dao: FromDishka[PaymentGatewayDao],
) -> dict[str, Any]:
    gateways = await gateway_dao.get_all(only_active=False, sorted=True)
    return {"items": [_gateway_to_dict(g) for g in gateways], "total": len(gateways)}


class ToggleGatewayRequest(BaseModel):
    is_active: bool


@router.put("/{gateway_id}/toggle")
@inject
async def toggle_gateway(
    gateway_id: int,
    body: ToggleGatewayRequest,
    _admin: AdminUser,
    gateway_dao: FromDishka[PaymentGatewayDao],
    session: FromDishka[AsyncSession],
) -> dict[str, Any]:
    gateway = await gateway_dao.get_by_id(gateway_id)
    if not gateway:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gateway not found")

    if body.is_active and gateway.settings and not gateway.settings.is_configured:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Gateway is not configured. Set up credentials first.",
        )

    await gateway_dao.set_active_status(gateway.type, body.is_active)
    await session.commit()
    return {"id": gateway_id, "is_active": body.is_active}


@router.get("/{gateway_id}/fields")
@inject
async def gateway_fields(
    gateway_id: int,
    _admin: AdminUser,
    gateway_dao: FromDishka[PaymentGatewayDao],
) -> dict[str, Any]:
    gateway = await gateway_dao.get_by_id(gateway_id)
    if not gateway:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gateway not found")
    return {"fields": _gateway_fields(gateway)}


@router.post("/{gateway_id}/test")
@inject
async def test_gateway(
    gateway_id: int,
    admin: AdminUser,
    gateway_dao: FromDishka[PaymentGatewayDao],
    create_test_payment: FromDishka[CreateTestPayment],
) -> dict[str, Any]:
    """Боевой тест-платёж (~2₽) через реальный use-case бота — единственный способ
    убедиться, что введённые кредлы РАБОЧИЕ (а не просто «поля заполнены»).

    Транзакция помечается is_test=True и привязывается к текущему админу. Ошибку
    шлюза (кривой ключ, недоступность) возвращаем текстом — ради этого всё и затевалось.

    Права: доступ к этому запросу уже проверен в `_common._get_admin_user`
    (раздел `gateways` + can_write через грант/enum). Поэтому вызываем `_execute`
    напрямую в обход enum-проверки интерактора (`REMNASHOP_GATEWAYS` есть только у
    OWNER/DEV, но НЕ у гранулярного админа Role.USER + full_access грант). Actor
    остаётся реальным админом — корректная привязка транзакции, аудит и логи.
    """
    gateway = await gateway_dao.get_by_id(gateway_id)
    if not gateway:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gateway not found")

    if gateway.settings and not gateway.settings.is_configured:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Шлюз не настроен — сначала введите ключи.",
        )

    try:
        payment = await create_test_payment._execute(admin, gateway.type)
    except HTTPException:
        raise
    except Exception as e:
        # Кривые кредлы / недоступность шлюза — показываем причину админу.
        detail = str(e).strip() or e.__class__.__name__
        if len(detail) > 300:
            detail = detail[:300] + "…"
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Тест-платёж не создан: {detail}",
        )

    if not payment.url:
        # Например, Telegram Stars: инвойс без внешней ссылки — из кабинета не оплатить.
        return {
            "ok": True,
            "payment_id": str(payment.id),
            "url": None,
            "message": (
                "Тест-платёж создан, но у этого шлюза нет ссылки оплаты "
                "(например, Telegram Stars) — проверить оплату из кабинета нельзя."
            ),
        }

    return {"ok": True, "payment_id": str(payment.id), "url": payment.url}


class SetFieldRequest(BaseModel):
    value: str  # пустая строка → очистить поле


@router.put("/{gateway_id}/fields/{field_name}")
@inject
async def set_gateway_field(
    gateway_id: int,
    field_name: str,
    body: SetFieldRequest,
    _admin: AdminUser,
    gateway_dao: FromDishka[PaymentGatewayDao],
    retort: FromDishka[Retort],
    session: FromDishka[AsyncSession],
) -> dict[str, Any]:
    gateway = await gateway_dao.get_by_id(gateway_id)
    if not gateway or not gateway.settings:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gateway not configured")

    hints = get_type_hints(type(gateway.settings))
    if field_name in _NON_CRED_FIELDS or field_name not in hints:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown field")

    value = body.value.strip()
    if value == "":
        new_value = None  # очистка поля
    else:
        try:
            # Грузим строку в тип поля так же, как штатный use-case бота.
            new_value = retort.load(value, hints[field_name])
        except Exception:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid value")

    setattr(gateway.settings, field_name, new_value)
    await gateway_dao.update(gateway)
    await session.commit()

    return {"ok": True, "is_configured": bool(getattr(gateway.settings, "is_configured", False))}
