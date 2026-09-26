"""Семейные профили: чистые правила — кто и когда живёт, без базы и панели.

ЧТО ЗАПЕРТО. Таблица решений `plan_decisions` — ядро функции: по состоянию владельца
(платит, пауза, резерв, пробник, нет подписки, удалена, заблокирован, тариф сменился)
она говорит, что делать с каждым профилем. Ошибка здесь — это либо семья, которая
живёт бесплатно, либо семья, которой отключили оплаченное.

Отдельно:
  * лимит устройств профиля НИКОГДА не 0 — в панели 0 значит «безлимит»;
  * при нехватке мест приостанавливаются самые НОВЫЕ профили;
  * срок «до 2099» копируется как есть;
  * резерв владельца не даёт семье ни дня;
  * имя профиля чистится от невидимых и управляющих символов.

Данные синтетические.
"""

import importlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

family = importlib.import_module("src.infrastructure.services.overlay_family")

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
SQUAD = "11111111-2222-3333-4444-555555555555"
PLAN = {
    "id": 7,
    "name": "Семейный",
    "tag": "FAMILY",
    "traffic_limit": 200,
    "traffic_limit_strategy": "MONTH",
    "internal_squads": [SQUAD],
    "external_squad": None,
    "device_limit": 5,
}


def owner(**kw):
    base = dict(
        user_id=10,
        telegram_id=555,
        sub_id=100,
        sub_status="ACTIVE",
        is_trial=False,
        expire_at=NOW + timedelta(days=30),
        plan=dict(PLAN),
        terms=family.Terms(max_profiles=2, devices_per_profile=2),
    )
    base.update(kw)
    return family.OwnerState(**base)


def profile(pid, *, status="active", created=None, reason=None, suspended_at=None, **kw):
    target = family.target_for(owner())
    base = dict(
        id=pid,
        owner_user_id=10,
        profile_user_id=1000 + pid,
        request_id=f"00000000-0000-0000-0000-{pid:012d}",
        label=f"P{pid}",
        status=status,
        suspend_reason=reason,
        device_limit=2,
        panel_username=f"rs_fam_{1000 + pid}",
        panel_uuid=f"aaaaaaaa-0000-0000-0000-{pid:012d}",
        suspended_at=suspended_at,
        last_reconciled_at=NOW - timedelta(hours=1),
        fail_count=0,
        created_at=created or (NOW - timedelta(days=10 - pid)),
        sub_id=2000 + pid,
        sub_status="DISABLED" if status == "suspended" else "ACTIVE",
        sub_expire_at=target.expire_at,
        sub_device_limit=target.device_limit,
        sub_traffic_limit=target.traffic_limit_gb,
        sub_strategy=target.strategy,
        sub_squads=target.internal_squads,
        sub_external_squad=target.external_squad,
        sub_url=f"https://sub.example/{pid}",
        sub_remna_id=f"aaaaaaaa-0000-0000-0000-{pid:012d}",
    )
    base.update(kw)
    return family.ProfileRow(**base)


def actions(decisions):
    return {d.profile_id: (d.action, d.reason) for d in decisions}


# ── таблица решений ─────────────────────────────────────────────────────────


def test_paying_owner_in_sync_touches_nothing():
    d = family.plan_decisions(owner(), [profile(1), profile(2)], NOW, 30)
    assert actions(d) == {1: ("noop", None), 2: ("noop", None)}


def test_renewal_extends_every_profile_with_traffic_reset():
    renewed = owner(expire_at=NOW + timedelta(days=60))
    d = family.plan_decisions(renewed, [profile(1), profile(2)], NOW, 30, reset_traffic=True)
    assert {x.action for x in d} == {"sync"}
    assert all(x.reset for x in d)
    assert all(x.target.expire_at == NOW + timedelta(days=60) for x in d)


def test_cron_resets_traffic_only_after_a_purchase_it_has_not_seen():
    p = profile(1)
    later = p.last_reconciled_at + timedelta(minutes=1)
    d = family.plan_decisions(owner(), [p], NOW, 30, purchase_at=later)
    assert d[0].action == "sync" and d[0].reset
    earlier = p.last_reconciled_at - timedelta(minutes=1)
    d = family.plan_decisions(owner(), [p], NOW, 30, purchase_at=earlier)
    assert d[0].action == "noop"


@pytest.mark.parametrize(
    "state, reason",
    [
        ({"frozen": True}, "owner_frozen"),
        ({"sub_status": "DISABLED"}, "owner_frozen"),
        ({"sub_id": None, "sub_status": None}, "owner_gone"),
        ({"sub_status": "DELETED"}, "owner_gone"),
        ({"exists": False}, "owner_gone"),
        ({"is_blocked": True}, "owner_blocked"),
        ({"is_trial": True}, "plan"),
        ({"terms": None}, "plan"),
    ],
)
def test_owner_problem_suspends_all_profiles(state, reason):
    d = family.plan_decisions(owner(**state), [profile(1), profile(2)], NOW, 30)
    assert actions(d) == {1: ("suspend", reason), 2: ("suspend", reason)}


def test_block_beats_pause_and_pause_beats_plan():
    """Порядок причин — часть смысла: блокировка важнее срока, пауза важнее тарифа."""
    assert family.owner_condition(owner(is_blocked=True, frozen=True), NOW) == "owner_blocked"
    assert family.owner_condition(owner(frozen=True, terms=None), NOW) == "owner_frozen"


def test_expired_owner_copies_nothing_and_profiles_expire_themselves():
    gone = owner(expire_at=NOW - timedelta(hours=1), sub_status="EXPIRED")
    expired_profile = profile(1, sub_expire_at=NOW - timedelta(hours=1), sub_status="EXPIRED")
    d = family.plan_decisions(gone, [expired_profile], NOW, 30)
    assert actions(d) == {1: ("noop", None)}


def test_expired_owner_turns_off_a_profile_that_would_outlive_him():
    """Владельцу срок сократили, а в прошлое панель дату не примет — выключаем."""
    gone = owner(expire_at=NOW - timedelta(hours=1), sub_status="EXPIRED")
    d = family.plan_decisions(gone, [profile(1, sub_expire_at=NOW + timedelta(days=5))], NOW, 30)
    assert actions(d) == {1: ("suspend", "owner_expired")}


def test_reserve_gives_the_family_nothing():
    """Резерв двигает срок строки вперёд и оставляет ACTIVE — семья не получает ни дня."""
    reserve_end = NOW + timedelta(days=7)
    on_reserve = owner(expire_at=reserve_end, reserve_expire_at=reserve_end)
    assert family.owner_condition(on_reserve, NOW) == "expired"
    old = profile(1, sub_expire_at=NOW - timedelta(days=1), sub_status="EXPIRED")
    d = family.plan_decisions(on_reserve, [old], NOW, 30, reset_traffic=True)
    assert actions(d) == {1: ("noop", None)}


def test_purchase_during_reserve_is_a_normal_payment():
    reserve_end = NOW + timedelta(days=7)
    paid = owner(expire_at=reserve_end + timedelta(days=30), reserve_expire_at=reserve_end)
    assert family.owner_condition(paid, NOW) == "ok"


def test_change_to_regular_plan_suspends_all_and_back_resumes():
    regular = owner(terms=None, plan={**PLAN, "id": 8})
    d = family.plan_decisions(regular, [profile(1), profile(2)], NOW, 30)
    assert actions(d) == {1: ("suspend", "plan"), 2: ("suspend", "plan")}

    suspended = [
        profile(1, status="suspended", reason="plan", suspended_at=NOW),
        profile(2, status="suspended", reason="plan", suspended_at=NOW),
    ]
    d = family.plan_decisions(owner(), suspended, NOW, 30)
    assert actions(d) == {1: ("resume", None), 2: ("resume", None)}
    assert all(x.target is not None for x in d)


def test_smaller_family_plan_suspends_the_newest_first():
    one_seat = owner(terms=family.Terms(max_profiles=1, devices_per_profile=2))
    oldest = profile(1, created=NOW - timedelta(days=30))
    middle = profile(2, created=NOW - timedelta(days=20))
    newest = profile(3, created=NOW - timedelta(days=1))
    d = family.plan_decisions(one_seat, [newest, oldest, middle], NOW, 30)
    assert actions(d) == {1: ("noop", None), 2: ("suspend", "plan"), 3: ("suspend", "plan")}
    assert [p.id for p in family.suspension_order([oldest, newest, middle])] == [3, 2, 1]


def test_new_plan_devices_are_applied_to_profiles():
    bigger = owner(terms=family.Terms(max_profiles=2, devices_per_profile=4))
    d = family.plan_decisions(bigger, [profile(1)], NOW, 30)
    assert d[0].action == "sync" and d[0].target.device_limit == 4


def test_grace_deletes_only_plan_and_gone_suspensions():
    old = NOW - timedelta(days=31)
    regular = owner(terms=None)
    by_plan = profile(1, status="suspended", reason="plan", suspended_at=old)
    fresh = profile(2, status="suspended", reason="plan", suspended_at=NOW - timedelta(days=3))
    d = family.plan_decisions(regular, [by_plan, fresh], NOW, 30)
    assert actions(d) == {1: ("delete", "plan"), 2: ("noop", "plan")}

    # Пауза и блокировка — временные: такие профили ждут без срока.
    frozen = profile(3, status="suspended", reason="owner_frozen", suspended_at=old)
    d = family.plan_decisions(owner(frozen=True), [frozen], NOW, 30)
    assert actions(d) == {3: ("noop", "owner_frozen")}


def test_new_reason_restarts_the_grace_clock():
    """Пауза 20 дней, потом смена тарифа — отсрочка считается от смены, а не от паузы."""
    p = profile(1, status="suspended", reason="owner_frozen", suspended_at=NOW - timedelta(days=40))
    d = family.plan_decisions(owner(terms=None), [p], NOW, 30)
    assert actions(d) == {1: ("relabel", "plan")}


def test_profile_whose_panel_user_vanished_is_cleaned_up():
    d = family.plan_decisions(owner(), [profile(1, sub_id=None, sub_status=None)], NOW, 30)
    assert actions(d) == {1: ("gone", None)}


def test_creating_and_deleting_rows_are_not_touched_by_reconcile():
    d = family.plan_decisions(owner(), [profile(1, status="creating"), profile(2, status="deleting")], NOW, 30)
    assert d == []


# ── желаемое состояние ──────────────────────────────────────────────────────


def test_target_takes_plan_traffic_whole_and_owner_term():
    t = family.target_for(owner())
    assert t.expire_at == NOW + timedelta(days=30)
    assert t.traffic_limit_gb == 200 and t.strategy == "MONTH"
    assert t.internal_squads == (SQUAD,)
    assert t.tag == "FAMILY"


def test_device_limit_is_never_zero():
    """0 в панели — безлимит устройств: профиль с нулём был бы дырой, а не запретом."""
    zero = owner(terms=family.Terms(max_profiles=1, devices_per_profile=0))
    assert family.target_for(zero).device_limit == 1
    with pytest.raises(ValueError):
        family.normalize_terms(1, 0)
    with pytest.raises(ValueError):
        family.normalize_terms(0, 2)
    with pytest.raises(ValueError):
        family.normalize_terms(11, 2)
    assert family.normalize_terms("3", "2") == family.Terms(3, 2)


def test_unlimited_term_until_2099_is_copied_as_is():
    forever = owner(expire_at=datetime(2099, 9, 26, 12, 0, tzinfo=timezone.utc))
    assert family.owner_condition(forever, NOW) == "ok"
    assert family.target_for(forever).expire_at.year == 2099


def test_imported_tag_is_never_copied_to_a_profile():
    """С меткой IMPORTED вебхук создания завёл бы профилю отдельный аккаунт."""
    assert family.target_for(owner(plan={**PLAN, "tag": "IMPORTED"})).tag is None
    assert family.target_for(owner(plan={**PLAN, "tag": "bad tag"})).tag is None


def test_needs_sync_ignores_subsecond_drift_and_squad_order():
    t = family.target_for(owner())
    p = profile(1, sub_expire_at=t.expire_at + timedelta(milliseconds=400))
    assert not family.needs_sync(p, t)
    assert family.needs_sync(profile(1, sub_device_limit=3), t)
    assert family.needs_sync(profile(1, sub_squads=()), t)


# ── право завести профиль ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "state, live, cfg, reason",
    [
        ({}, 0, {"enabled": False}, "disabled"),
        ({"sub_id": None}, 0, {}, "no_subscription"),
        ({"is_blocked": True}, 0, {}, "blocked"),
        ({"is_trial": True}, 0, {}, "trial"),
        ({"terms": None}, 0, {}, "not_family"),
        ({"frozen": True}, 0, {}, "frozen"),
        ({"reserve_expire_at": NOW + timedelta(days=30)}, 0, {}, "reserve"),
        ({"sub_status": "EXPIRED"}, 0, {}, "not_active"),
        ({"expire_at": NOW - timedelta(minutes=1)}, 0, {}, "not_active"),
        ({}, 2, {}, "max_reached"),
        ({}, 1, {}, None),
        ({"sub_status": "LIMITED"}, 0, {}, None),
    ],
)
def test_create_eligibility(state, live, cfg, reason):
    config = {"enabled": True, **cfg}
    assert family.create_eligibility(owner(**state), config, live, NOW) == reason


# ── имя профиля ─────────────────────────────────────────────────────────────


def test_label_is_trimmed_and_cleaned():
    assert family.clean_label("  Мама  ") == "Мама"
    assert family.clean_label("Ма\nма\t") == "Ма ма"
    assert family.clean_label("‮амаМ") == "амаМ"
    assert family.clean_label("​") is None
    assert family.clean_label("") is None
    assert family.clean_label(None) is None
    assert family.clean_label("x" * 50) == "x" * family.LABEL_MAX
    # Склейка эмодзи остаётся: без неё «👨‍👩‍👧» распался бы на три лица.
    assert family.clean_label("👨‍👩‍👧 Дети") == "👨‍👩‍👧 Дети"


def test_panel_username_prefix():
    assert family.panel_username(42) == "rs_fam_42"
    assert family.is_family_username("rs_fam_42")
    assert not family.is_family_username("rs_42")
    assert not family.is_family_username(None)


# ── конфиг ──────────────────────────────────────────────────────────────────


def test_config_is_off_by_default_and_normalized(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(family, "ASSETS_DIR", tmp_path)
    monkeypatch.setattr(family, "CONFIG_PATH", tmp_path / "family.json")
    assert family.load_config() == {"enabled": False, "suspend_grace_days": 30}
    saved = family.save_config({"enabled": True, "suspend_grace_days": 9999})
    assert saved == {"enabled": True, "suspend_grace_days": 365}
    assert json.loads((tmp_path / "family.json").read_text("utf-8"))["enabled"] is True
    (tmp_path / "family.json").write_text("{битый", "utf-8")
    assert family.load_config()["enabled"] is False


def test_suspended_profile_that_is_still_on_is_switched_off_again():
    """Намерение «приостановлен» записано, а панель говорит «работает» (выключение не
    дошло или профиль включили руками) — выключаем снова, отсрочку не сдвигаем."""
    on = profile(1, status="suspended", reason="owner_frozen", suspended_at=NOW, sub_status="ACTIVE")
    assert actions(family.plan_decisions(owner(frozen=True), [on], NOW, 30)) == {1: ("suspend", "owner_frozen")}
    off = profile(1, status="suspended", reason="owner_frozen", suspended_at=NOW)
    assert actions(family.plan_decisions(owner(frozen=True), [off], NOW, 30)) == {1: ("noop", "owner_frozen")}
    gone = owner(expire_at=NOW - timedelta(hours=1), sub_status="EXPIRED")
    on_expired = profile(2, status="suspended", reason="owner_expired", suspended_at=NOW, sub_status="LIMITED")
    assert actions(family.plan_decisions(gone, [on_expired], NOW, 30)) == {2: ("suspend", "owner_expired")}


def test_panel_missing_waits_for_grace_whatever_the_owner_does():
    """Пользователя профиля нет в панели: причину не переписываем (отсрочка не
    сдвигается), выключать нечего, удаляем только по истечении отсрочки."""
    fresh = profile(1, status="suspended", reason="panel_missing", suspended_at=NOW, sub_status="ACTIVE")
    old = profile(2, status="suspended", reason="panel_missing", suspended_at=NOW - timedelta(days=31))
    for state in ({"frozen": True}, {"is_blocked": True}, {"terms": None}):
        d = family.plan_decisions(owner(**state), [fresh, old], NOW, 30)
        assert actions(d) == {1: ("noop", "panel_missing"), 2: ("delete", "panel_missing")}, state
    gone = owner(expire_at=NOW - timedelta(hours=1), sub_status="EXPIRED")
    d = family.plan_decisions(gone, [fresh, old], NOW, 30)
    assert actions(d) == {1: ("noop", "panel_missing"), 2: ("delete", "panel_missing")}
    # Владелец платит: свежий пробуем вернуть (вдруг нашёлся), старый — удаляем.
    d = family.plan_decisions(owner(), [fresh, old], NOW, 30)
    assert actions(d) == {1: ("resume", None), 2: ("delete", "panel_missing")}
