"""Ступени реферальной программы «Бедолаги»: что кабинет напечатает человеку.

ЗАЧЕМ ЭТОТ ТЕСТ. С их v4.5 у наград две схемы. При `REFERRAL_REWARD_SCHEME=levels`
поля `commission_percent` и `first_payment_commission_percent` не управляют ничем:
начисления идут по таблице уровней, и их собственная схема ответа это прямо
оговаривает. Адаптер же продолжал отдавать кабинету legacy-процент — на стенде
боту были настроены 25 % / 10 % / 7 дней на трёх коленах, а кабинет показывал
«Уровень 1 — 15 % от платежей», и эти 15 % не платились никому.

Обещание награды — это обещание денег, поэтому оно заперто тестом, а не только
живой проверкой: живая проверка показывает ОДНО состояние их настроек, а сломать
можно любое из четырёх (классика, цепочка, ранги, включённая схема без уровней).

Запуск — внутри образа адаптера (зависимости compose.py лежат только там):

  docker build -t remnashop-adapter-ci adapter
  docker run --rm -v "$PWD/adapter/tests:/tmp/tests:ro" remnashop-adapter-ci \
    sh -c 'pip install -q --target /tmp/pylibs pytest &&
           PYTHONPATH=/tmp/pylibs python -m pytest /tmp/tests -q -p no:cacheprovider'
"""

import importlib

compose = importlib.import_module("compose")


def level(**kw):
    """Ступень в том виде, в каком её отдаёт их `/cabinet/referral/terms`."""
    base = {
        "level": 1,
        "is_current": False,
        "rewards": ["25% от суммы"],
        "pays_referrer": True,
        "trigger": "first_topup",
        "trigger_label": "за первое пополнение",
        "required_referrals": 0,
        "required_referrals_active_only": True,
        "referee_reward": None,
    }
    base.update(kw)
    return base


def terms(**kw):
    base = {"scheme": "levels", "levels_mode": "chain", "levels": [level()]}
    base.update(kw)
    return base


def test_classic_scheme_is_not_touched():
    """Классика — прежнее поведение: ступени собирает не эта функция.

    Регрессия важнее новой фичи: у подавляющего большинства установок схема
    классическая, и она обязана остаться ровно такой, какой была.
    """
    assert compose.referral_reward_levels({"scheme": "legacy", "levels": []}) == []
    assert compose.referral_reward_levels({}) == []


def test_chain_levels_carry_their_own_wording():
    """Цепочка: берём ИХ готовую строку награды, а не пересчитываем числа.

    Строка собрана тем же кодом, что считает выплату, и уже учитывает личную
    ставку партнёра и выбор «деньги или дни».
    """
    out = compose.referral_reward_levels(terms(levels=[
        level(level=1, rewards=["25% от суммы"]),
        level(level=2, rewards=["10% от суммы"]),
        level(level=3, rewards=["7 дн. подписки (Про)"]),
    ]))
    assert [l["level"] for l in out] == [1, 2, 3]
    assert out[2]["label"] == "7 дн. подписки (Про) за первое пополнение"


def test_mixed_reward_keeps_both_halves():
    """Ступень платит и процентом, и днями — показываем обе половины."""
    out = compose.referral_reward_levels(terms(levels=[
        level(rewards=["25% от суммы", "7 дн. подписки"]),
    ]))
    assert out[0]["label"] == "25% от суммы + 7 дн. подписки за первое пополнение"


def test_chain_threshold_is_named():
    """Порог открытия ступени обязан быть назван: иначе награда выглядит даром."""
    out = compose.referral_reward_levels(terms(levels=[
        level(level=2, required_referrals=3, required_referrals_active_only=True),
    ]))
    assert "открывается за 3 рефералов с пополнением" in out[0]["label"]

    out = compose.referral_reward_levels(terms(levels=[
        level(level=2, required_referrals=3, required_referrals_active_only=False),
    ]))
    assert "открывается за 3 приглашённых" in out[0]["label"]


def test_tier_mode_wording_differs_from_chain():
    """Режим рангов — это лестница, и порог там называется иначе, чем в цепочке."""
    out = compose.referral_reward_levels(terms(levels_mode="tiers", levels=[
        level(level=1, required_referrals=0, is_current=True),
        level(level=2, required_referrals=5),
    ]))
    # Нулевой порог в лестнице — стартовая ступень; без пометки она читается как
    # ступень, условие которой забыли указать.
    assert "стартовый" in out[0]["label"]
    assert "ваш уровень" in out[0]["label"]
    assert "от 5 рефералов с пополнением" in out[1]["label"]
    assert "стартовый" not in out[1]["label"]


def test_non_paying_level_does_not_get_a_trigger():
    """«Вам не начисляется за первое пополнение» звучало бы как награда.

    Правило их же: повод у неплатящей ступени не называется.
    """
    out = compose.referral_reward_levels(terms(levels_mode="tiers", levels=[
        level(rewards=["вам не начисляется"], pays_referrer=False, is_current=True),
    ]))
    assert "за первое пополнение" not in out[0]["label"]
    assert out[0]["label"].startswith("вам не начисляется")


def test_level_without_rewards_is_dropped():
    """Строка без награды не несёт ничего — печатать её нечего."""
    assert compose.referral_reward_levels(terms(levels=[level(rewards=[])])) == []
    assert compose.referral_reward_levels(terms(levels=[level(rewards=["", "  "])])) == []


def test_garbage_does_not_raise():
    """Ответ чужого бота — не наш договор: он может прийти каким угодно."""
    assert compose.referral_reward_levels(terms(levels="не список")) == []
    assert compose.referral_reward_levels(terms(levels=[None, 42, level()])) != []
    assert compose.referral_reward_levels({"scheme": "levels"}) == []


def test_value_is_zero_because_the_reward_is_not_a_number():
    """`value` остаётся в форме ради нашего бэкенда, но числом эту награду не выразить.

    Кабинет при непустом `label` печатает именно его — проверено в
    cabinet/src/pages/ReferralPage.tsx.
    """
    out = compose.referral_reward_levels(terms())
    assert out[0]["value"] == 0
    assert out[0]["label"]
