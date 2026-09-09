"""Сверка суммы платежа ЮMoney: кого проводим, кого нет.

ЗАЧЕМ. `YoomoneyGateway.handle_webhook` в базе проверяет подпись и возвращает
COMPLETED, НЕ глядя на сумму: `withdraw_amount` используется только при расчёте
HMAC и дальше выбрасывается, а `ProcessPayment` читает `pricing.final_amount`
исключительно для событий. То есть оплата на любую сумму по существующему `label`
проводила счёт целиком — на шлюзе, через который прошло 65 успешных платежей из 89.

ЧЕМ ЭТОТ ТЕСТ ВАЖЕН. Ошибка в сверке дороже самой дыры: слишком строгое сравнение
начнёт отвергать НАСТОЯЩИЕ деньги, и человек заплатит, а подписки не получит.
Поэтому здесь заперты обе стороны — и что недоплату не проводим, и что честный
платёж проходит, включая случай, ради которого выбран `withdraw_amount`, а не
`amount`: комиссия всегда делает `amount` меньше счёта.

Данные взяты с боевых вебхуков: счёт 929 → списано 929.00, дошло 901.13;
счёт 449 → списано 449.00, дошло 435.53.

Запуск — внутри образа бота (pytest в образе нет, ставим рядом):

  docker run --rm --env-file .env --network remnawave-network \
    -v /opt/remnashop/admin_src/overlay_patches:/opt/remnashop/overlay_patches:ro \
    -v /opt/remnashop/admin_src/tests:/tmp/tests:ro \
    remnashop-remnashop sh -c 'pip install -q --target /tmp/pylibs pytest \
      && PYTHONPATH=/tmp/pylibs python -m pytest /tmp/tests/test_yoomoney_amount.py -v'
"""

import importlib
from decimal import Decimal

check = importlib.import_module("overlay_patches.yoomoney_amount_check")

D = Decimal


def hook(withdraw: str = "929.00", amount: str = "901.13", **extra) -> dict:
    """Вебхук ЮMoney в том виде, в каком он приходит на самом деле."""
    return {
        "notification_type": "card-incoming",
        "amount": amount,  # дошло до кошелька, за вычетом комиссии
        "withdraw_amount": withdraw,  # списано с плательщика — с этим и сверяем
        "codepro": "false",
        "unaccepted": "false",
        **extra,
    }


def test_exact_payment_goes_through() -> None:
    """Обычная оплата: списано ровно по счёту. «929.00» против «929» — то же число."""
    allow, message = check.verdict(hook(), D("929.00"), D("929"))
    assert allow is True
    assert message is None, "по честному платежу владельца не дёргаем"


def test_commission_does_not_reject_honest_payment() -> None:
    """Главная ловушка: сверять с `amount` нельзя — комиссия его занижает.

    Если бы сравнивали с 901.13 (дошло), этот платёж был бы отвергнут как
    недоплата, и человек остался бы без подписки, заплатив полностью.
    """
    data = hook(withdraw="929.00", amount="901.13")
    allow, _ = check.verdict(data, check._to_decimal(data["withdraw_amount"]), D("929"))
    assert allow is True

    # А вот так выглядела бы ошибка, которую тест и стережёт:
    allow_wrong, _ = check.verdict(data, check._to_decimal(data["amount"]), D("929"))
    assert allow_wrong is False, "сверка с amount отвергает честные деньги — так нельзя"


def test_underpayment_is_rejected() -> None:
    """Та самая дыра: рубль по счёту на 929."""
    allow, message = check.verdict(hook(withdraw="1.00"), D("1.00"), D("929"))
    assert allow is False
    assert message and "Недоплата" in message
    assert "1.00" in message and "929" in message, "владелец должен видеть обе суммы"


def test_overpayment_is_allowed_but_reported() -> None:
    """Переплата — не повод отказывать: деньги пришли, подписку выдаём."""
    allow, message = check.verdict(hook(withdraw="1000.00"), D("1000.00"), D("929"))
    assert allow is True
    assert message and "Переплата" in message


def test_protected_transfer_is_rejected() -> None:
    """codepro=true: отправитель может отозвать деньги, пока их не приняли."""
    allow, message = check.verdict(hook(codepro="true"), D("929.00"), D("929"))
    assert allow is False
    assert message and "протекцией" in message


def test_unaccepted_transfer_is_rejected() -> None:
    """unaccepted=true: перевод не принят, денег ещё нет."""
    allow, message = check.verdict(hook(unaccepted="true"), D("929.00"), D("929"))
    assert allow is False
    assert message and "не принят" in message


def test_flags_are_case_insensitive() -> None:
    """ЮMoney шлёт строки; регистр не должен решать судьбу денег."""
    allow, _ = check.verdict(hook(codepro="True"), D("929.00"), D("929"))
    assert allow is False


def test_unreadable_amount_does_not_block_payment() -> None:
    """Не смогли прочитать сумму — пропускаем.

    Отказ по незнанию хуже пропуска: подпись базой уже проверена, деньги списаны.
    Лучше провести и разобраться потом, чем оставить человека без оплаченной услуги.
    """
    assert check.verdict(hook(), None, D("929")) == (True, None)
    assert check.verdict(hook(), D("929.00"), None) == (True, None)


def test_missing_invoice_does_not_block() -> None:
    """Счёта в базе нет (например, чужой label) — решение остаётся за базой."""
    allow, _ = check.verdict(hook(), D("929.00"), None)
    assert allow is True


def test_to_decimal_survives_junk() -> None:
    """Парсер суммы не должен бросать ни на чём, что придёт из чужого запроса."""
    assert check._to_decimal("929.00") == D("929.00")
    assert check._to_decimal(" 929 ") == D("929")
    for junk in (None, "", "abc", "1,5", {}, []):
        assert check._to_decimal(junk) is None
