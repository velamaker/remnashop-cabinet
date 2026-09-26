"""Кому МОЖНО задать «Всё работает?» и написать «давно не был» — и, главное, кому нельзя.

ЦЕНА ОШИБКИ ЗДЕСЬ — ДОВЕРИЕ. Лишнее сообщение платящему человеку читается как
слежка («откуда вы знаете, что я не подключался?») или как спам, а молчание панели,
принятое за «не подключался», превращает КАЖДОГО в адресата. Поэтому заперто:
  • нет данных панели — не пишем никому (ни вопрос, ни «давно не был»);
  • оба сигнала срабатывают только в окне после события, и включённый тумблер не
    рассылает по истории;
  • вопрос — один раз на человека, «давно не был» — не чаще раза в K дней;
  • пробные, персонал, заблокированные, отказавшиеся, без Telegram — мимо;
  • на паузе (заморозка) и не ACTIVE в панели — мимо: это «не может», а не «пропал»;
  • «никогда не подключался» — не наш сигнал (его ведёт событие панели).
Данные синтетические.
"""

import importlib
from datetime import datetime, timedelta, timezone

import pytest

cs = importlib.import_module("src.infrastructure.services.overlay_churn_signals")

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)


def cfg(**over):
    return cs.normalize({**cs.DEFAULT_CONFIG, "check_enabled": True, "idle_enabled": True, **over})


def candidate(**over) -> "cs.Candidate":
    base = dict(
        user_id=7,
        telegram_id=123456,
        lang="ru",
        is_blocked=False,
        is_bot_blocked=False,
        role="USER",
        remna_uuid="11111111-1111-1111-1111-111111111111",
        is_trial=False,
        expire_at=NOW + timedelta(days=20),
        opted_out=False,
        check_done=False,
        idle_last_sent=None,
    )
    base.update(over)
    return cs.Candidate(**base)


def seen(first_hours_ago=None, online_days_ago=None, online_hours_ago=None, status="ACTIVE"):
    first = NOW - timedelta(hours=first_hours_ago) if first_hours_ago is not None else None
    online = None
    if online_days_ago is not None:
        online = NOW - timedelta(days=online_days_ago)
    elif online_hours_ago is not None:
        online = NOW - timedelta(hours=online_hours_ago)
    return cs.PanelSeen(first_connected_at=first, online_at=online, status=status)


# ── конфиг ──────────────────────────────────────────────────────────────────


def test_both_signals_are_off_by_default():
    assert cs.DEFAULT_CONFIG["check_enabled"] is False
    assert cs.DEFAULT_CONFIG["idle_enabled"] is False
    assert cs.any_enabled(cs.normalize({})) is False


def test_missing_or_broken_config_file_means_off(tmp_path, monkeypatch):
    path = tmp_path / "churn_signals.json"
    monkeypatch.setattr(cs, "CONFIG_PATH", path)
    assert cs.any_enabled(cs.load_config()) is False
    path.write_text("{ не json", encoding="utf-8")
    assert cs.any_enabled(cs.load_config()) is False


def test_string_false_does_not_switch_it_on():
    """Руками правленый файл со строкой "false" — это выключено, а не «непустая строка»."""
    assert cs.normalize({"check_enabled": "false", "idle_enabled": "true"})["check_enabled"] is False
    assert cs.normalize({"idle_enabled": "true"})["idle_enabled"] is False


def test_numbers_are_clamped():
    got = cs.normalize({"check_delay_hours": 0, "idle_days": 1000, "idle_cooldown_days": -1,
                        "idle_min_days_left": "x"})
    assert got["check_delay_hours"] == 2
    assert got["idle_days"] == 60
    assert got["idle_cooldown_days"] == 7
    assert got["idle_min_days_left"] == cs.DEFAULT_CONFIG["idle_min_days_left"]


def test_save_and_load_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(cs, "CONFIG_PATH", tmp_path / "churn_signals.json")
    cs.save_config({"check_enabled": True, "idle_days": 10})
    got = cs.load_config()
    assert got["check_enabled"] is True and got["idle_enabled"] is False and got["idle_days"] == 10


# ── «Всё работает?» ─────────────────────────────────────────────────────────


def test_check_asks_a_day_after_the_first_connection():
    assert cs.decide_check(candidate(), seen(first_hours_ago=25), cfg(), NOW) is None


def test_check_waits_until_the_delay_passes():
    assert cs.decide_check(candidate(), seen(first_hours_ago=5), cfg(), NOW) == "too_early"


def test_check_does_not_ask_people_who_connected_long_ago():
    """Включили тумблер — старожилам вопрос «всё работает?» не прилетает."""
    reason = cs.decide_check(candidate(), seen(first_hours_ago=24 * 30), cfg(), NOW)
    assert reason == "too_late"
    # Граница окна: задержка + CHECK_WINDOW_HOURS.
    edge = 24 + cs.CHECK_WINDOW_HOURS
    assert cs.decide_check(candidate(), seen(first_hours_ago=edge - 0.5), cfg(), NOW) is None
    assert cs.decide_check(candidate(), seen(first_hours_ago=edge + 0.5), cfg(), NOW) == "too_late"


def test_check_is_once_per_person():
    reason = cs.decide_check(candidate(check_done=True), seen(first_hours_ago=25), cfg(), NOW)
    assert reason == "already_asked"


def test_check_silent_panel_means_no_message():
    """Панель молчала — мы НЕ знаем, подключался ли человек. Не пишем."""
    assert cs.decide_check(candidate(), None, cfg(), NOW) == "no_panel_data"


def test_check_not_for_people_who_never_connected():
    assert cs.decide_check(candidate(), seen(), cfg(), NOW) == "not_connected_yet"


def test_check_respects_delay_setting():
    assert cs.decide_check(candidate(), seen(first_hours_ago=25), cfg(check_delay_hours=48), NOW) == "too_early"
    assert cs.decide_check(candidate(), seen(first_hours_ago=50), cfg(check_delay_hours=48), NOW) is None


def test_check_asks_trials_too():
    """Вопрос нужен как раз пробным: из 200 пробных 169 молча ушли."""
    assert cs.decide_check(candidate(is_trial=True), seen(first_hours_ago=25), cfg(), NOW) is None


@pytest.mark.parametrize(
    "over, reason",
    [
        ({"role": "ADMIN"}, "staff"),
        ({"role": "OWNER"}, "staff"),
        ({"telegram_id": None}, "no_telegram"),
        ({"is_blocked": True}, "blocked"),
        ({"is_bot_blocked": True}, "blocked"),
        ({"opted_out": True}, "opted_out"),
    ],
)
def test_check_skips_people_we_must_not_write(over, reason):
    assert cs.decide_check(candidate(**over), seen(first_hours_ago=25), cfg(), NOW) == reason


# ── «Давно не подключался» ──────────────────────────────────────────────────


def test_idle_writes_to_a_paying_person_idle_for_a_week():
    assert cs.decide_idle(candidate(), seen(online_days_ago=8), cfg(), NOW) is None


def test_idle_not_for_recently_online():
    assert cs.decide_idle(candidate(), seen(online_days_ago=2), cfg(), NOW) == "recently_online"


def test_idle_not_a_batch_over_history():
    """Пропал полгода назад — это не «попал в окно», тумблер не пишет ему пачкой."""
    assert cs.decide_idle(candidate(), seen(online_days_ago=180), cfg(), NOW) == "idle_too_long"
    edge = 7 + cs.IDLE_WINDOW_DAYS
    assert cs.decide_idle(candidate(), seen(online_hours_ago=edge * 24 - 1), cfg(), NOW) is None
    assert cs.decide_idle(candidate(), seen(online_hours_ago=edge * 24 + 1), cfg(), NOW) == "idle_too_long"


def test_idle_silent_panel_is_not_idle():
    """Главная грабля: молчание панели ≠ «давно не подключался»."""
    assert cs.decide_idle(candidate(), None, cfg(), NOW) == "no_panel_data"


def test_idle_never_connected_is_not_our_signal():
    assert cs.decide_idle(candidate(), seen(), cfg(), NOW) == "never_connected"


def test_idle_not_for_trials():
    assert cs.decide_idle(candidate(is_trial=True), seen(online_days_ago=8), cfg(), NOW) == "trial"


def test_idle_not_when_subscription_is_about_to_end():
    c = candidate(expire_at=NOW + timedelta(days=2))
    assert cs.decide_idle(c, seen(online_days_ago=8), cfg(), NOW) == "ends_soon"
    c = candidate(expire_at=NOW + timedelta(days=4))
    assert cs.decide_idle(c, seen(online_days_ago=8), cfg(), NOW) is None


def test_idle_cooldown():
    c = candidate(idle_last_sent=NOW - timedelta(days=10))
    assert cs.decide_idle(c, seen(online_days_ago=8), cfg(), NOW) == "cooldown"
    c = candidate(idle_last_sent=NOW - timedelta(days=31))
    assert cs.decide_idle(c, seen(online_days_ago=8), cfg(), NOW) is None
    c = candidate(idle_last_sent=NOW - timedelta(days=31))
    assert cs.decide_idle(c, seen(online_days_ago=8), cfg(idle_cooldown_days=60), NOW) == "cooldown"


def test_idle_respects_days_setting():
    assert cs.decide_idle(candidate(), seen(online_days_ago=8), cfg(idle_days=14), NOW) == "recently_online"
    assert cs.decide_idle(candidate(), seen(online_days_ago=15), cfg(idle_days=14), NOW) is None


@pytest.mark.parametrize(
    "over, reason",
    [
        ({"role": "DEV"}, "staff"),
        ({"telegram_id": None}, "no_telegram"),
        ({"is_blocked": True}, "blocked"),
        ({"is_bot_blocked": True}, "blocked"),
        ({"opted_out": True}, "opted_out"),
    ],
)
def test_idle_skips_people_we_must_not_write(over, reason):
    assert cs.decide_idle(candidate(**over), seen(online_days_ago=8), cfg(), NOW) == reason


def test_naive_panel_time_is_treated_as_utc():
    naive = (NOW - timedelta(days=8)).replace(tzinfo=None)
    assert cs.decide_idle(candidate(), cs.PanelSeen(None, naive, "ACTIVE"), cfg(), NOW) is None


# ── заморозка и статус в панели ─────────────────────────────────────────────


def test_frozen_subscription_gets_neither_signal():
    """На паузе человек не подключается, потому что сам так решил: «давно не видели
    вас» ему — давление, а «всё работает?» — вопрос не к месту."""
    frozen = candidate(frozen=True)
    assert cs.decide_idle(frozen, seen(online_days_ago=8), cfg(), NOW) == "frozen"
    assert cs.decide_check(frozen, seen(first_hours_ago=25), cfg(), NOW) == "frozen"


@pytest.mark.parametrize("status", ["DISABLED", "LIMITED", "EXPIRED", None, ""])
def test_not_active_in_panel_gets_neither_signal(status):
    """Выключен, упёрся в трафик или истёк в панели — подключиться он не может.
    Статус неизвестен — тоже не пишем: «не знаем» ≠ «можно»."""
    idle = seen(online_days_ago=8, status=status)
    fresh = seen(first_hours_ago=25, status=status)
    assert cs.decide_idle(candidate(), idle, cfg(), NOW) == "panel_inactive"
    assert cs.decide_check(candidate(), fresh, cfg(), NOW) == "panel_inactive"


def test_panel_status_accepts_the_remnapy_enum_and_any_case():
    from enum import Enum

    class UserStatus(str, Enum):  # как remnapy.enums.users.UserStatus
        ACTIVE = "ACTIVE"
        DISABLED = "DISABLED"

    assert cs.panel_status(UserStatus.ACTIVE) == "ACTIVE"
    assert cs.panel_status(UserStatus.DISABLED) == "DISABLED"
    assert cs.panel_status("active") == "ACTIVE"
    assert cs.panel_status(None) is None and cs.panel_status("  ") is None
    lower = seen(online_days_ago=8, status="active")
    assert cs.decide_idle(candidate(), lower, cfg(), NOW) is None


# ── тексты и кнопки ─────────────────────────────────────────────────────────


def test_texts_have_no_braces_for_push_format():
    for text in (cs.check_message("ru"), cs.check_message("en"), cs.idle_message(8, "ru"),
                 cs.idle_message(8, "en"), cs.broken_message("ru"), cs.broken_message("en")):
        assert "{" not in text and "}" not in text


def test_idle_text_names_days_in_russian():
    assert "8 дней назад" in cs.idle_message(8, "ru")
    assert "21 день назад" in cs.idle_message(21, "ru")
    assert "22 дня назад" in cs.idle_message(22, "ru")
    assert "8 days ago" in cs.idle_message(8, "en")


def _buttons(markup):
    return [b for row in (markup.inline_keyboard if markup else []) for b in row]


def test_check_keyboard_carries_row_id_and_optout():
    buttons = _buttons(cs.check_keyboard(42, "ru"))
    assert [b.text for b in buttons] == ["✅ Всё работает", "❌ Не работает", "🔕 Не присылать такое"]
    assert [b.callback_data for b in buttons] == ["rs_sig:ok:42", "rs_sig:bad:42", "rs_sig:off:check"]


def test_idle_keyboard_leads_to_self_check_and_support():
    buttons = _buttons(cs.idle_keyboard("https://cab.example/", "https://t.me/help", "ru"))
    assert buttons[0].url == "https://cab.example/support"
    assert buttons[1].url == "https://t.me/help"
    assert buttons[-1].callback_data == "rs_sig:off:idle"


def test_keyboards_never_carry_empty_urls():
    """Пустой url Telegram отвергает вместе со всем сообщением."""
    buttons = _buttons(cs.idle_keyboard("", "", "ru"))
    assert [b.callback_data for b in buttons] == ["rs_sig:off:idle"]
    assert cs.broken_keyboard("", "", "ru") is None
    only_support = _buttons(cs.broken_keyboard("", "https://t.me/help", "en"))
    assert [b.url for b in only_support] == ["https://t.me/help"]


def test_callback_parsing_is_strict():
    assert cs.parse_callback("rs_sig:ok:17") == ("works", "17")
    assert cs.parse_callback("rs_sig:bad:17") == ("broken", "17")
    assert cs.parse_callback("rs_sig:off:idle") == ("off", "idle")
    assert cs.parse_callback("rs_sig:off:check") == ("off", "check")
    for junk in (None, "", "rs_sig:", "rs_sig:ok:x", "rs_sig:off:all", "rs_sig:ok:1:2",
                 "rs_pay_reminder_off", "rs_sig:drop:1"):
        assert cs.parse_callback(junk) is None, junk


def test_support_link_from_config():
    from types import SimpleNamespace

    secret = SimpleNamespace(get_secret_value=lambda: "@begemot_help")
    assert cs.support_link(SimpleNamespace(bot=SimpleNamespace(support_username=secret))) == (
        "https://t.me/begemot_help"
    )
    assert cs.support_link(SimpleNamespace()) == ""


def test_share_percent_of_nothing_is_unknown():
    assert cs.share_percent(0, 0) is None
    assert cs.share_percent(1, 4) == 25
