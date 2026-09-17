"""Скидка на продление до окончания подписки: правила выдачи и тексты.

ЧТО ЗАПИРАЕМ. Скидка, выданная не тому, — это деньги, отданные тем, кто и так
заплатил бы (значимая доля повторных покупок делается до конца срока), или
второе предложение поверх открытого, которое сгорание другой кампании снимет
вместе с нашим. Поэтому каждая причина отказа проверяется отдельно, а общие
вещи — одним тестом на каждую:
  * без файла конфига фича выключена, пределы зажимаются, дни не совпадают с
    push-напоминанием даже при нестандартном PUSH_EXPIRING_DAYS;
  * платящий клиент в окне с Telegram — получает;
  * конец оплаченного периода считается цепочкой, а не «дата оплаты + срок»;
  * людям только с почтой — лишь если скидка доживёт до письма за 72 ч, а
    письмо не обещает её «до конца подписки», если она сгорит раньше;
  * скидка никогда не живёт дольше подписки;
  * в текстах нет неподставленных «{», склонения верные;
  * предпросмотр судит кандидата на момент будущей выдачи.

Только чистые функции и подделки: ни базы, ни сети.

Запуск — внутри образа бота, как остальные тесты рядом (см. ci.yml).
"""

import importlib
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

rd = importlib.import_module("src.infrastructure.services.overlay_renewal_discount")
permissions = importlib.import_module("src.web.permissions")
settings_io = importlib.import_module("src.web.endpoints.admin.settings_io")

NOW = datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc)
ENV = rd.Env(email_enabled=True, autopay_enabled=True)


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    monkeypatch.setattr(rd, "ASSETS_DIR", tmp_path)
    monkeypatch.setattr(rd, "CONFIG_PATH", tmp_path / "renewal_discount.json")
    monkeypatch.delenv("PUSH_EXPIRING_DAYS", raising=False)


def cfg(**over: Any) -> dict:
    return rd._normalize({**rd.DEFAULT_CONFIG, "enabled": True, **over})


def cand(**over: Any) -> Any:
    """Платящий клиент с Telegram, подписка кончается ровно через 5 дней − 1 ч."""
    base = rd.Candidate(
        user_id=101,
        role="USER",
        is_blocked=False,
        is_bot_blocked=False,
        telegram_id=555,
        email=None,
        is_email_verified=False,
        lang="ru",
        purchase_discount=0,
        personal_discount=0,
        autopay_enabled=False,
        subscription_id=9,
        expire_at=NOW + timedelta(days=5) - timedelta(hours=1),
        is_trial=False,
    )
    return replace(base, **over)


def pay(days_ago: float, *, duration: int = 30, kind: str = "NEW", discount: int = 0) -> dict:
    return {
        "created_at": NOW - timedelta(days=days_ago),
        "purchase_type": kind,
        "duration_days": duration,
        "discount_percent": discount,
    }


PAID = rd.PaidFacts(paid_count=1, last_paid_at=NOW - timedelta(days=25), early_renewals=0)


def decide(c: Any = None, facts: Any = PAID, conf: Any = None, now: datetime = NOW, env: Any = ENV, **kw: Any):
    return rd.decide(c or cand(), facts, conf or cfg(), now, env, **kw)


# ── конфиг ────────────────────────────────────────────────────────────────────


def test_without_config_file_feature_is_off():
    loaded = rd.load_config()
    assert loaded["enabled"] is False
    assert loaded["cooldown_days"] == 90
    assert loaded["skip_early_renewers"] is True


def test_broken_config_falls_back_to_defaults():
    rd.CONFIG_PATH.write_text("{не json", "utf-8")
    assert rd.load_config()["enabled"] is False


def test_limits_are_clamped(monkeypatch: pytest.MonkeyPatch):
    assert rd._normalize({"days_before": 1})["days_before"] == 4
    assert rd._normalize({"percent": 100})["percent"] == 90
    assert rd._normalize({"percent": 0})["percent"] == 1
    assert rd._normalize({"lifetime_hours": 1})["lifetime_hours"] == 24
    assert rd._normalize({"cooldown_days": -5})["cooldown_days"] == 0
    # Строка из руками правленного файла — не «включено».
    assert rd._normalize({"enabled": "true"})["enabled"] is False

    # Push «подписка заканчивается» за 5 дней → наше сообщение не раньше чем за 6,
    # и дефолт тоже поднимается, а не остаётся на совпадающих 5.
    monkeypatch.setenv("PUSH_EXPIRING_DAYS", "5")
    assert rd.min_days_before() == 6
    assert rd._normalize({"days_before": 5})["days_before"] == 6
    assert rd._normalize({})["days_before"] == 6


def test_saved_config_round_trips():
    saved = rd.save_config({"enabled": True, "percent": 15, "days_before": 7})
    assert rd.load_config() == saved
    assert saved["percent"] == 15 and saved["days_before"] == 7


# ── решение ───────────────────────────────────────────────────────────────────


def test_paying_client_in_window_with_telegram_gets_discount():
    assert decide() is None


@pytest.mark.parametrize(
    "over, code",
    [
        ({"role": "ADMIN"}, "staff"),
        ({"is_blocked": True}, "blocked"),
        ({"is_trial": True}, "trial"),
        ({"expire_at": NOW + timedelta(days=9)}, "outside_window"),
        ({"expire_at": NOW + timedelta(days=4)}, "outside_window"),
        ({"on_reserve": True}, "reserve"),
        ({"frozen": True}, "frozen"),
        ({"payment_in_progress": True}, "payment_in_progress"),
        ({"open_grant_until": NOW + timedelta(hours=3)}, "open_grant"),
        ({"purchase_discount": 5}, "has_discount"),
        ({"personal_discount": 10}, "personal_discount"),
        ({"telegram_id": None}, "unreachable"),
    ],
)
def test_each_reason_has_its_own_code(over: dict, code: str):
    assert decide(cand(**over)) == code


def test_never_paid_is_not_a_client():
    assert decide(facts=rd.PaidFacts()) == "not_paid"


def test_gifted_current_period_is_skipped():
    gift_after_payment = cand(last_gift_at=NOW - timedelta(days=3))
    assert decide(gift_after_payment) == "gift_period"
    gift_before_payment = cand(last_gift_at=NOW - timedelta(days=40))
    assert decide(gift_before_payment) is None


def test_autopay_counts_only_when_autopay_runs_at_all():
    c = cand(autopay_enabled=True)
    assert decide(c) == "autopay"
    assert decide(c, env=rd.Env(email_enabled=True, autopay_enabled=False)) is None


def test_open_winback_blocks_until_it_burns():
    alive = cand(other_offer_until=NOW + timedelta(hours=5))
    assert decide(alive) == "other_offer_open"
    burnt = cand(other_offer_until=NOW - timedelta(hours=5))
    assert decide(burnt) is None


def test_same_period_after_revoke_is_not_granted_again():
    """Срок сдвинули на 2 дня (админ, разморозка) — это тот же период."""
    shifted = cand(
        last_granted_at=NOW - timedelta(days=40),
        last_grant_sub_expire_at=NOW + timedelta(days=5) - timedelta(days=2),
    )
    assert decide(shifted, conf=cfg(cooldown_days=0)) == "already_this_period"

    next_period = cand(
        last_granted_at=NOW - timedelta(days=40),
        last_grant_sub_expire_at=NOW + timedelta(days=5) - timedelta(days=7),
    )
    assert decide(next_period, conf=cfg(cooldown_days=0)) is None


@pytest.mark.parametrize(
    "cooldown, granted_days_ago, expected",
    [(30, 20, "cooldown"), (30, 40, None), (100, 60, "cooldown"), (0, 20, None)],
)
def test_cooldown(cooldown: int, granted_days_ago: int, expected: Any):
    c = cand(
        last_granted_at=NOW - timedelta(days=granted_days_ago),
        last_grant_sub_expire_at=NOW - timedelta(days=granted_days_ago - 5),
    )
    assert decide(c, conf=cfg(cooldown_days=cooldown)) == expected


def test_early_renewer_is_skipped_only_with_the_switch():
    facts = rd.PaidFacts(paid_count=3, last_paid_at=NOW - timedelta(days=10), early_renewals=1)
    assert decide(facts=facts) == "early_renewer"
    assert decide(facts=facts, conf=cfg(skip_early_renewers=False)) is None


# ── каналы для людей только с почтой ─────────────────────────────────────────


def email_only(**over: Any) -> Any:
    return cand(**{"telegram_id": None, "email": "client@example.test", "is_email_verified": True, **over})


def test_email_only_gets_discount_when_it_lives_until_the_letter():
    assert decide(email_only(), conf=cfg(days_before=5, lifetime_hours=120)) is None


def test_email_only_unreachable_when_discount_burns_before_the_letter():
    c = email_only(expire_at=NOW + timedelta(days=7) - timedelta(hours=1))
    assert decide(c, conf=cfg(days_before=7, lifetime_hours=48)) == "unreachable"


def test_email_only_unreachable_without_email_or_verification():
    no_mail = rd.Env(email_enabled=False, autopay_enabled=True)
    assert decide(email_only(), env=no_mail) == "unreachable"
    assert decide(email_only(is_email_verified=False)) == "unreachable"


def email_line_at_letter(conf: dict, expire_at: datetime) -> tuple[Any, str]:
    """Решение на момент выдачи и строка письма за 72 ч с настоящим сроком скидки."""
    c = email_only(expire_at=expire_at)
    reason = decide(c, conf=conf)
    line = rd.email_discount_line(
        conf["percent"],
        grant_expires_at=rd.grant_expires_at(NOW, conf, expire_at),
        sub_expire_at=expire_at,
    )
    return reason, line


def test_email_line_names_deadline_when_discount_burns_before_subscription_end():
    """За 7 дней при сроке 120 ч скидка сгорает за 47 ч до конца подписки.

    Письмо за 72 ч эта выдача переживает, поэтому человеку только с почтой её дают.
    Но «пока подписка не закончилась» отправило бы его платить в последний день —
    уже по полной цене. Письмо обязано назвать настоящий срок.
    """
    expire_at = NOW + timedelta(days=7) - timedelta(hours=1)
    reason, line = email_line_at_letter(cfg(days_before=7, lifetime_hours=120), expire_at)
    assert reason is None
    assert "пока подписка не закончилась" not in line
    # NOW + 120 ч = 22 сентября 12:00 UTC = 15:00 по Москве.
    assert "до 22 сентября, 15:00 по московскому времени" in line
    assert "10%" in line


def test_email_line_promises_until_end_only_when_discount_lives_that_long():
    expire_at = NOW + timedelta(days=5) - timedelta(hours=1)
    reason, line = email_line_at_letter(cfg(days_before=5, lifetime_hours=120), expire_at)
    assert reason is None
    assert line.endswith("пока подписка не закончилась.")


def test_email_line_without_known_deadline_promises_nothing():
    line = rd.email_discount_line(12, grant_expires_at=None, sub_expire_at=NOW)
    assert "12%" in line
    assert "пока подписка" not in line and " до " not in line


def test_email_deadline_rounds_minutes_down_and_is_moscow_time():
    moment = datetime(2026, 12, 31, 21, 59, 59, tzinfo=timezone.utc)
    assert rd._email_deadline_ru(moment) == "1 января, 00:59 по московскому времени"


def test_blocked_bot_with_email_is_unreachable():
    """Письмо за 72 ч уходит только тем, у кого нет Telegram вовсе."""
    c = cand(is_bot_blocked=True, email="client@example.test", is_email_verified=True)
    assert decide(c) == "unreachable"


def test_push_alone_is_enough():
    assert decide(cand(telegram_id=None, has_push=True)) is None


# ── цепочка оплаченных периодов ──────────────────────────────────────────────


def test_chain_of_early_renewals_counts_from_previous_end():
    # 55 дн. назад NEW на 30 → конец 25 дн. назад; 35 дн. назад RENEW (рано) → конец
    # через 5 дн.; сегодня RENEW — до настоящего конца, хотя «дата прошлой оплаты +
    # срок» поставила бы конец 5 дней назад и ранней эту оплату не посчитала бы.
    facts = rd.paid_facts(
        [pay(55, kind="NEW"), pay(35, kind="RENEW"), pay(0, kind="RENEW")]
    )
    assert facts.paid_count == 3
    assert facts.early_renewals == 2
    assert facts.last_paid_at == NOW


def test_payment_with_discount_is_not_early():
    facts = rd.paid_facts([pay(40, kind="NEW"), pay(20, kind="RENEW", discount=10)])
    assert facts.early_renewals == 0


def test_change_starts_period_from_scratch():
    # 50 дн. назад NEW на 30 → конец 20 дн. назад; 40 дн. назад CHANGE на 7 (рано) →
    # конец 33 дн. назад, с нуля; 25 дн. назад RENEW — уже после этого конца.
    facts = rd.paid_facts(
        [pay(50, kind="NEW"), pay(40, kind="CHANGE", duration=7), pay(25, kind="RENEW")]
    )
    assert facts.early_renewals == 1  # только CHANGE (оплачен до конца NEW)


def test_unlimited_duration_has_no_end():
    facts = rd.paid_facts([pay(50, kind="NEW", duration=0), pay(40, kind="RENEW")])
    assert facts.early_renewals == 0


# ── срок скидки ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("lifetime", [24, 120, 720])
def test_discount_never_outlives_subscription(lifetime: int):
    sub_end = NOW + timedelta(days=4, hours=13)
    assert rd.grant_expires_at(NOW, cfg(lifetime_hours=lifetime), sub_end) <= sub_end


# ── тексты ───────────────────────────────────────────────────────────────────


def messages(percent: int = 10, lang: str = "ru", days: float = 5, lifetime_h: int = 120) -> dict:
    sub_end = NOW + timedelta(days=days)
    return rd.build_messages(
        percent,
        lang,
        now_=NOW,
        sub_expire_at=sub_end,
        grant_expires_at=rd.grant_expires_at(NOW, cfg(lifetime_hours=lifetime_h), sub_end),
    )


@pytest.mark.parametrize("lang", ["ru", "en", "tr", None])
def test_messages_have_no_placeholders_and_carry_percent(lang: Any):
    m = messages(percent=17, lang=lang)
    for value in m.values():
        assert "{" not in value and "}" not in value
    assert "17%" in m["push_title"] and "17%" in m["telegram_html"]
    assert "17%" in m["telegram_html"].split("</b>", 1)[1]


def test_unknown_language_falls_back_to_russian():
    assert messages(lang="tr") == messages(lang="ru")
    assert "renewal" in messages(lang="en")["push_title"]


def test_russian_plurals():
    assert rd.plural_days_ru(1) == "1 день"
    assert rd.plural_days_ru(2) == "2 дня"
    assert rd.plural_days_ru(5) == "5 дней"
    assert rd.plural_days_ru(11) == "11 дней"
    assert rd.plural_days_ru(21) == "21 день"
    assert "через 5 дней" in messages(days=5)["push_body"]
    # Выдача в окне [N − 12 ч; N) — «за 5 дней» не превращается в «через 4».
    assert "через 5 дней" in messages(days=4.6)["push_body"]


def test_offer_validity_wording():
    assert "до окончания подписки" in messages(days=5, lifetime_h=720)["telegram_html"]
    assert "ещё 3 дня" in messages(days=5, lifetime_h=72)["telegram_html"]
    assert "for 3 more days" in messages(lang="en", days=5, lifetime_h=72)["telegram_html"]


def test_reminder_lines():
    assert "12%" in rd.email_discount_line(12, grant_expires_at=NOW, sub_expire_at=NOW)
    assert rd.push_discount_tail("en", 12) == " Your 12% renewal discount is active."
    assert rd.push_discount_tail("kk", 12).startswith(" Для вас действует скидка 12%")


# ── предпросмотр ─────────────────────────────────────────────────────────────


class PreviewSession:
    """Отдаёт кандидатов и их оплаты; запоминает параметры выборки."""

    def __init__(self, rows: list[dict], payments: list[dict]) -> None:
        self.rows = rows
        self.payments = payments
        self.params: list[dict] = []
        self.writes = 0

    async def execute(self, statement: Any, params: Any = None) -> Any:
        sql = str(statement)
        self.params.append(dict(params or {}))
        if sql == rd.CANDIDATES_SQL:
            return _Result(self.rows)
        if sql == rd.PAYMENTS_SQL:
            return _Result(self.payments)
        self.writes += 1
        raise AssertionError(f"предпросмотр не должен выполнять {sql[:60]}")


class _Result:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def mappings(self) -> "_Result":
        return self

    def all(self) -> list[dict]:
        return list(self._rows)


def row(c: Any) -> dict:
    return {k: getattr(c, k) for k in rd.Candidate.__dataclass_fields__}


@pytest.mark.asyncio
async def test_preview_judges_candidate_at_future_grant_moment():
    far = cand(
        user_id=1,
        expire_at=NOW + timedelta(days=20),
        # Выдавали 80 дней назад: сейчас cooldown 90 ещё идёт, а через 15 дней — нет.
        last_granted_at=NOW - timedelta(days=80),
        last_grant_sub_expire_at=NOW - timedelta(days=75),
        # Win-back ещё открыт сегодня, но сгорит задолго до выдачи.
        other_offer_until=NOW + timedelta(days=2),
        # Счёт «в процессе» сегодня ничего не значит для выдачи через 15 дней.
        payment_in_progress=True,
    )
    near = cand(user_id=2, payment_in_progress=True)
    session = PreviewSession(
        [row(near), row(far)],
        [{**pay(25), "user_id": 1}, {**pay(25), "user_id": 2}],
    )

    result = await rd.preview(session, cfg(), NOW, 30, ENV, hide_ids=True)

    assert session.writes == 0
    assert result["examined"] == 2
    assert result["would_grant"] == 1
    assert result["skipped"] == {"payment_in_progress": 1}
    by_reason = {s["reason"]: s for s in result["sample"]}
    grant = by_reason[None]
    assert datetime.fromisoformat(grant["grant_at"]) == far.expire_at - timedelta(days=5)
    assert grant["user_id"] is None
    assert grant["channels"] == {"telegram": True, "push": False, "email": False}
    assert result["reason_labels"]["early_renewer"] == "и так продлевает заранее"
    assert "{" not in result["message"]["telegram_html"]
    # Горизонт расширяет только верх окна выборки.
    lo, hi = session.params[0]["lo"], session.params[0]["hi"]
    assert hi - lo == timedelta(days=30, hours=12)


# ── проводка ─────────────────────────────────────────────────────────────────


def test_admin_paths_belong_to_settings_section():
    assert permissions.section_for_path("/api/v1/admin/renewal-discount/preview") == "settings"
    assert permissions.section_for_path("/api/v1/admin/renewal-discount") == "settings"


def test_config_is_in_settings_backup():
    assert "renewal_discount.json" in settings_io.CONFIG_FILES
