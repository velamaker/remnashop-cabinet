"""Кнопки разбора спорного платежа: что уедет в Telegram и кому они доступны.

ЗАЧЕМ. Сверка суммы не проводит платёж при недоплате, «протекции» или непринятом
переводе — и пишет владельцу «Решите вручную: вернуть деньги или выдать подписку».
Раньше на этом уведомление заканчивалось: владелец шёл искать счёт в админке.
Теперь под сообщением кнопки, и главное про них — две вещи, обе легко сломать
незаметно:

  • callback-данные Telegram обрезает на 64 байтах. Вылезли за предел — кнопка
    молча перестаёт работать у ВСЕХ, и узнать об этом можно только нажав;
  • нажатие выдаёт подписку, поэтому роль нажавшего проверяется отдельно от того,
    кому ушло сообщение: пересланное сообщение сохраняет кнопки.

Запуск — внутри образа бота:

  docker run --rm --env-file .env --network remnawave-network \
    -v /opt/remnashop/admin_src/tests:/tmp/tests:ro \
    remnashop-remnashop sh -c 'pip install -q --target /tmp/pylibs pytest pytest-asyncio \
      && PYTHONPATH=/tmp/pylibs python -m pytest /tmp/tests/test_payment_review.py \
         -v --asyncio-mode=auto'
"""

import importlib
from types import SimpleNamespace

from src.core.constants import USER_KEY
from src.core.enums import Role

review = importlib.import_module("src.telegram.routers.overlay_payment_review")

# Настоящий UUID счёта — самое длинное, что уезжает в callback.
PAYMENT_ID = "699015bf-11e3-4a5d-a775-8e5ea85da965"


def buttons():
    return [b for row in review.keyboard(PAYMENT_ID, "YOOMONEY").inline_keyboard for b in row]


def test_callback_data_fits_telegram_limit():
    """64 байта — жёсткий предел Telegram. Выйдем за него — кнопки умрут молча."""
    for b in buttons():
        assert len(b.callback_data.encode()) <= 64, f"{b.callback_data!r} длиннее предела"


def test_callback_carries_payment_and_gateway():
    """Оба значения нужны ProcessPayment; второй раз лезть в базу за ними незачем."""
    grant = next(b for b in buttons() if b.callback_data.startswith(f"{review.PREFIX}:grant:"))
    parts = grant.callback_data.split(":")
    assert parts[2] == PAYMENT_ID
    assert parts[3] == "YOOMONEY"


def test_both_actions_are_offered():
    """Выдать — и отметить разобранным. Второе нужно для возврата денег: подписку
    не выдали, но вопрос закрыт, и уведомление не должно висеть вечно."""
    data = [b.callback_data for b in buttons()]
    assert any(d.startswith(f"{review.PREFIX}:grant:") for d in data)
    assert any(d.startswith(f"{review.PREFIX}:done:") for d in data)


def test_only_staff_may_press():
    """Сообщение можно переслать, а callback прилетает от того, кто нажал."""
    assert Role.OWNER in review.ALLOWED_ROLES
    assert Role.ADMIN in review.ALLOWED_ROLES
    assert Role.USER not in review.ALLOWED_ROLES
    assert Role.PREVIEW not in review.ALLOWED_ROLES, (
        "«админ только для просмотра» не должен выдавать подписки"
    )


def test_actor_role_is_read_from_middleware_data():
    assert review._actor_role({USER_KEY: SimpleNamespace(role=Role.OWNER)}) is Role.OWNER
    assert review._actor_role({}) is None
    assert review._actor_role({USER_KEY: None}) is None


def test_prefix_is_unique_enough_not_to_clash():
    """Роутеры бота ловят callback по префиксу; совпадение увело бы чужие нажатия."""
    import src.telegram.routers.overlay_gift as gift

    assert review.PREFIX != getattr(gift, "_PREFIX", None)


# ── само действие кнопки ────────────────────────────────────────────────────
#
# Первая версия этой кнопки не выдавала подписку НИ РАЗУ: вызов шёл от имени
# нажавшего, а у ProcessPayment `required_permission = None`, и база отвечает
# отказом всем, кроме системного актора. Тесты этого не поймали, потому что
# проверяли длину callback-данных и состав кнопок — всё, кроме самого действия.

import pytest
from types import SimpleNamespace
from uuid import UUID

from src.core.enums import PaymentGatewayType, TransactionStatus

PAYMENT_UUID = UUID("699015bf-11e3-4a5d-a775-8e5ea85da965")


class FakeProcessPayment:
    """Сценарий проведения счёта. `system` — единственный допустимый вход."""

    def __init__(self, raises: Exception | None = None) -> None:
        self.raises = raises
        self.system_calls: list = []
        self.direct_calls: list = []

    @property
    def system(self):
        async def run(dto):
            self.system_calls.append(dto)
            if self.raises:
                raise self.raises
        return run

    async def __call__(self, actor, dto):
        self.direct_calls.append(dto)
        raise AssertionError("вызов от имени пользователя запрещён: база ответит отказом")


class FakeDao:
    def __init__(self, status, raises: bool = False) -> None:
        self.status, self.raises = status, raises

    async def get_by_payment_id(self, payment_id):
        if self.raises:
            raise RuntimeError("база недоступна")
        return SimpleNamespace(status=self.status) if self.status is not None else None


async def grant(process_payment, dao):
    return await review.grant_outcome(
        PAYMENT_UUID, PaymentGatewayType.YOOMONEY, process_payment, dao
    )


@pytest.mark.asyncio
async def test_grant_goes_through_the_system_actor():
    """Та самая поломка: от имени нажавшего база отказывает всегда."""
    pp = FakeProcessPayment()
    granted, text = await grant(pp, FakeDao(TransactionStatus.COMPLETED))
    assert granted is True and text == "Подписка выдана"
    assert len(pp.system_calls) == 1, "счёт должен проводиться системным актором"
    assert pp.direct_calls == []
    dto = pp.system_calls[0]
    assert dto.payment_id == PAYMENT_UUID
    assert dto.new_transaction_status == TransactionStatus.COMPLETED


@pytest.mark.asyncio
async def test_already_completed_invoice_is_not_reported_as_success():
    """`ProcessPayment` на неподходящем статусе молча выходит без ошибки.

    Если верить «не упало», владелец увидел бы «Подписка выдана» там, где не
    выдано ничего, и закрыл бы вопрос, которого никто не решил.
    """
    granted, text = await grant(FakeProcessPayment(), FakeDao(TransactionStatus.CANCELED))
    assert granted is False
    assert "НЕ выдана" in text and "CANCELED" in text


@pytest.mark.asyncio
async def test_failure_shows_the_reason():
    granted, text = await grant(
        FakeProcessPayment(raises=RuntimeError("нет денег на кошельке")),
        FakeDao(TransactionStatus.PENDING),
    )
    assert granted is False and "нет денег на кошельке" in text


@pytest.mark.asyncio
async def test_unverifiable_result_is_not_called_success():
    """База не ответила — говорим «проверьте сами», а не «готово»."""
    granted, text = await grant(FakeProcessPayment(), FakeDao(None, raises=True))
    assert granted is False and "проверить результат не удалось" in text


@pytest.mark.asyncio
async def test_missing_invoice_is_reported():
    granted, text = await grant(FakeProcessPayment(), FakeDao(None))
    assert granted is False and "не найден" in text
