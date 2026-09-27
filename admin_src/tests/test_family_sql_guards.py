"""Семейные профили: сторожа — куски кода, на которых держатся деньги и доступ.

ЗАЧЕМ ОТДЕЛЬНЫЙ ФАЙЛ. Подделки в соседних тестах отвечают на запрос по куску текста
и не замечают того, что из запроса ПРОПАЛО: «FOR UPDATE OF u» можно стереть, и они
останутся зелёными, а на бою две вкладки заведут два профиля при лимите один. Здесь
заперты именно строки и тела запросов — дёшево и ровно там, где ошибка дорогая.
"""

import ast
import importlib
import inspect
import uuid as uuid_lib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

family = importlib.import_module("src.infrastructure.services.overlay_family")

TARGET = family.Target(
    expire_at=datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc),
    device_limit=2,
    traffic_limit_gb=100,
    strategy="MONTH",
    internal_squads=("11111111-2222-3333-4444-555555555555",),
    external_squad=None,
    tag="FAMILY",
)
UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


class Capture:
    """SDK, который запоминает тело запроса и отвечает тем, что ему велели."""

    def __init__(self, answer=None):
        self.bodies: list = []
        self.answer = answer
        self.users = self

    async def update_user(self, body):
        self.bodies.append(body)
        if self.answer is not None:
            return self.answer
        return SimpleNamespace(
            hwid_device_limit=body.hwid_device_limit,
            traffic_limit_bytes=body.traffic_limit_bytes,
            expire_at=body.expire_at,
        )

    async def create_user(self, body):
        self.bodies.append(body)
        return SimpleNamespace(uuid=uuid_lib.uuid4())


def test_family_never_locks_the_owner_row():
    """Семья стоит в СВОЕЙ очереди, а не на строке users: база при покупке пишет в
    эту строку после панели, и замок семьи на ней ронял оплату по таймауту."""
    assert "FOR UPDATE" not in family.OWNER_READ_SQL
    assert "LEFT JOIN subscriptions" in family.OWNER_READ_SQL
    source = inspect.getsource(family)
    assert "FOR UPDATE OF u" not in source


def test_family_queue_is_an_advisory_lock_with_a_timeout():
    assert "pg_advisory_xact_lock(:ns, :owner)" in family.FAMILY_LOCK_SQL
    assert "pg_try_advisory_xact_lock(:ns, :owner)" in family.FAMILY_TRY_LOCK_SQL
    source = inspect.getsource(family.family_lock)
    # Ожидание очереди и всех записей семейной транзакции ограничено.
    assert "SET LOCAL lock_timeout" in source
    assert family.LOCK_WAIT_MS <= 10_000


@pytest.mark.parametrize(
    "func",
    [
        "create_profile",
        "delete_profile",
        "reset_profile_devices",
        "reconcile_owner",
        "_adopt",
        "_mark_failed",
        "_delete_now",
    ],
)
def test_every_change_stands_in_the_family_queue(func):
    source = inspect.getsource(getattr(family, func))
    assert "family_lock(session" in source, func


def test_request_id_is_checked_again_in_the_queue():
    """Двойной клик успевает дойти до очереди дважды — второй обязан увидеть первого."""
    source = inspect.getsource(family.create_profile)
    lock = source.index("family_lock(session, owner_id)")
    assert source.index("profile_by_request", lock) > lock
    assert "ON CONFLICT (request_id) DO NOTHING" in source


def test_payment_hook_never_waits_and_has_a_deadline():
    source = inspect.getsource(family.after_purchase)
    assert "wait_ms=0" in source
    assert "asyncio.wait_for(" in source and "HOOK_DEADLINE_SECONDS" in source
    assert family.HOOK_DEADLINE_SECONDS <= 20


def test_reconcile_commits_after_every_profile():
    """Одно действие в панели — одна запись и commit: откат соседа его не сотрёт."""
    source = inspect.getsource(family.reconcile_owner)
    loop = source[source.index("while True:"):]
    assert loop.index("_apply_decision(") < loop.index("await session.commit()", loop.index("_apply_decision("))


def test_creating_row_is_committed_before_the_panel_call():
    """Сбой панели обязан оставить след, по которому крон доведёт профиль."""
    source = inspect.getsource(family.create_profile)
    assert source.index("await session.commit()") < source.index("panel_create(")


def test_deleting_is_committed_before_the_panel_call():
    source = inspect.getsource(family.delete_profile)
    assert source.index("status = 'deleting'") < source.index("await session.commit()")
    assert source.index("await session.commit()") < source.index("_delete_now(")


def test_pause_is_read_as_open_only():
    assert "active = true" in family.PAUSE_SQL


def test_reserve_counts_only_while_its_window_is_open():
    """Семья «истекла» только на ОТКРЫТОМ резерве без приобретения после него;
    закрытый резерв не влияет, остаток резерва не вычитается."""
    sql = family.OWNER_TIMELINE_SQL
    assert "r.ended = false AND r.reserve_expire_at > now()" in sql
    assert "t.status::text = 'COMPLETED' AND t.is_test = false" in sql
    assert "ELSE 0 END) > 0" in sql
    assert "p.reward_type::text = 'SUBSCRIPTION'" in sql
    assert "reserve_left" not in sql and "EPOCH" not in sql


def test_gifts_to_others_are_not_own_purchases():
    """Подарок другому человеку — оплачен владельцем, но период не его: ни резерв не
    закрывает, ни трафик семье не обнуляет. Исключение — как в переносе остатка."""
    for sql in (family.OWNER_TIMELINE_SQL, family.PERIOD_START_SQL):
        assert "NOT EXISTS (SELECT 1 FROM gift_payments gp WHERE gp.payment_id = t.payment_id)" in sql
        assert "COALESCE(t.gateway_display_name, '') <> 'Баланс · подарок'" in sql


def test_traffic_reset_follows_only_real_plan_purchases():
    """Пополнение и докупки (id < 0) не продлевают период и не обнуляют трафик семьи."""
    assert "t.status::text = 'COMPLETED'" in family.PERIOD_START_SQL
    assert "t.is_test = false" in family.PERIOD_START_SQL
    assert "ELSE 0 END) > 0" in family.PERIOD_START_SQL
    # Подарок или промокод на тариф и новая строка подписки тоже начинают период.
    assert "p.reward_type::text = 'SUBSCRIPTION'" in family.PERIOD_START_SQL
    assert "GREATEST(s.created_at" in family.PERIOD_START_SQL


def test_pending_rows_are_left_alone_for_a_grace_period():
    assert "make_interval(mins => :grace)" in family.PENDING_SQL
    assert family.PENDING_GRACE_MINUTES >= 2


async def test_sync_patch_is_narrow():
    """Только то, что семья наследует от владельца: имя, описание, телеграм, почту и
    метку не трогаем — узкое тело PATCH, как у докупки устройства."""
    sdk = Capture()
    await family.panel_sync(sdk, UUID, TARGET, status_active=False)
    sent = set(sdk.bodies[0].model_dump(exclude_unset=True))
    assert sent == {
        "uuid",
        "expire_at",
        "hwid_device_limit",
        "traffic_limit_bytes",
        "traffic_limit_strategy",
        "active_internal_squads",
        "external_squad_uuid",
    }
    await family.panel_sync(sdk, UUID, TARGET, status_active=True)
    assert set(sdk.bodies[1].model_dump(exclude_unset=True)) == sent | {"status"}


async def test_sync_checks_what_the_panel_answered():
    """«Принял, но не применил» оставило бы семью со старым сроком без единого слова."""
    wrong = SimpleNamespace(hwid_device_limit=0, traffic_limit_bytes=None, expire_at=TARGET.expire_at)
    with pytest.raises(family.PanelError):
        await family.panel_sync(Capture(wrong), UUID, TARGET, status_active=False)
    stale = SimpleNamespace(
        hwid_device_limit=2, traffic_limit_bytes=None, expire_at=TARGET.expire_at - timedelta(days=30)
    )
    with pytest.raises(family.PanelError):
        await family.panel_sync(Capture(stale), UUID, TARGET, status_active=False)


async def test_create_body_has_no_telegram_and_never_zero_devices():
    sdk = Capture()
    await family.panel_create(sdk, username="rs_fam_7", target=TARGET, description="Семья rs_1: «Мама»")
    body = sdk.bodies[0].model_dump(exclude_unset=True)
    assert body["username"] == "rs_fam_7"
    assert body["hwid_device_limit"] == 2
    assert "telegram_id" not in body and "email" not in body
    assert body["tag"] == "FAMILY"


def test_cron_runs_every_five_minutes_under_the_guard():
    tick = importlib.import_module("src.infrastructure.taskiq.tasks.family")
    task = tick.run_family_tick
    assert task.labels["schedule"] == [{"cron": "*/5 * * * *"}]
    inner = getattr(task.original_func, "__dishka_orig_func__", task.original_func)
    assert getattr(inner, "__cron_guard__", None) == "family"


def _handle_success_source() -> str:
    path = Path(importlib.import_module("overlay_patches.gateway_payment").__file__)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "ProcessPayment_handle_success":
            return ast.get_source_segment(path.read_text(encoding="utf-8"), node) or ""
    raise AssertionError("обработчик успешной оплаты не найден")


def test_payment_hook_runs_after_the_purchase_and_cannot_break_it():
    """Хук стоит ПОСЛЕ выдачи и в try с откатом: панель не срывает оплаченное."""
    source = _handle_success_source()
    hook = source.index("family.after_purchase(")
    assert source.index("purchase_subscription.system(") < hook
    block = source[source.rindex("try:", 0, hook) : source.index("_after_change", hook)]
    assert "except Exception" in block and "self.session.rollback()" in block


class _NoDb:
    """Сессия, которой всё равно: здесь проверяется тело PATCH, а не запись в базу."""

    async def execute(self, *args, **kwargs):
        return None


class _Panel(Capture):
    async def reset_user_traffic(self, uuid):
        return None


def _row(status: str):
    rules = importlib.import_module("test_family_rules")
    return rules.profile(1, sub_status=status)


@pytest.mark.parametrize(
    "status, sends_active",
    [("EXPIRED", True), ("ACTIVE", True), ("LIMITED", True), ("DISABLED", False)],
)
async def test_renewal_does_not_switch_on_a_profile_disabled_by_hand(status, sends_active):
    """Как база владельцу: продление включает истёкший профиль, но не отключённый
    руками в панели — его включает только тот, кто выключил."""
    sdk = _Panel()
    await family._apply_sync(_NoDb(), sdk, _row(status), TARGET, reset=True, now=TARGET.expire_at)
    sent = sdk.bodies[0].model_dump(exclude_unset=True)
    assert ("status" in sent) is sends_active


def test_purge_takes_the_family_queue_then_the_owner_row_then_lists_targets():
    """Первым делом замки, в одном порядке с остальными; список семьи — уже под ними:
    профиль, заведённый между списком и удалением, остался бы в панели сиротой."""
    purge = importlib.import_module("src.infrastructure.services.overlay_user_purge")
    source = inspect.getsource(purge.purge_user)
    queue = source.index("family.family_lock(session, user_id")
    row = source.index("FOR UPDATE")
    targets = source.index("family.purge_targets(")
    assert queue < row < targets


def test_purge_targets_does_not_swallow_database_errors():
    """«Таблицы нет» проверяется явно; любой другой сбой базы прерывает удаление, а
    не превращается в «семьи нет» с работающими профилями."""
    source = inspect.getsource(family.purge_targets)
    assert "to_regclass('family_profiles')" in source
    assert "except Exception" not in source


def test_traffic_reset_waits_for_the_issued_period():
    """Обнулять — за выданный период: оплата не позже обновления строки подписки."""
    assert "t.updated_at <= s.updated_at" in family.PERIOD_START_SQL




def test_owner_passes_are_fair():
    for sql in (family.CHANGED_OWNERS_SQL, family.ALL_OWNERS_SQL):
        assert "ORDER BY bool_or(fp.last_reconciled_at IS NULL) DESC" in sql
        assert sql.rstrip().endswith("LIMIT :lim")


def test_on_off_goes_through_patch_status_first():
    """`actions/enable|disable` на панели 3.4.4 отвечают «уже включён/выключен» (A030 /
    A029) — повтор после сбоя падал бы навсегда. Включение — только статусом в теле
    PATCH; выключение — тоже PATCH, а действие — лишь запасной путь для LIMITED и
    EXPIRED (их PATCH не выключает), где «уже выключен» считается успехом."""
    source = inspect.getsource(family)
    assert "enable_user(" not in source
    assert source.count("disable_user(") == 1
    disable = inspect.getsource(family.panel_disable)
    assert disable.index("status=UserStatus.DISABLED") < disable.index("disable_user(")
    assert "_already_disabled(exc)" in disable


def test_traffic_reset_is_the_last_step_and_marked_right_after():
    source = inspect.getsource(family._apply_sync)
    reset = source.index("panel_reset_traffic(")
    assert source.index("panel_sync(") < source.index("_profile_ok(") < reset
    assert source.index("traffic_reset_at = now()", reset) > reset
