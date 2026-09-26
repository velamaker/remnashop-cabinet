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


def test_state_is_read_under_the_owner_row_lock():
    """Без замка строки владельца две вкладки завели бы два профиля при лимите один."""
    assert "FOR UPDATE OF u" in family.OWNER_LOCK_SQL
    assert "LEFT JOIN subscriptions" in family.OWNER_LOCK_SQL
    assert "FOR UPDATE" not in family.OWNER_READ_SQL


@pytest.mark.parametrize(
    "func", ["create_profile", "delete_profile", "reset_profile_devices", "reconcile_owner", "_adopt", "_mark_failed"]
)
def test_every_change_takes_the_owner_lock_first(func):
    source = inspect.getsource(getattr(family, func))
    assert "load_owner(session" in source and "lock=True" in source, func


def test_request_id_is_checked_again_under_the_lock():
    """Двойной клик успевает дойти до замка дважды — второй обязан увидеть первого."""
    source = inspect.getsource(family.create_profile)
    lock = source.index("load_owner(session, owner_id, lock=True)")
    assert source.index("profile_by_request", lock) > lock
    assert "ON CONFLICT (request_id) DO NOTHING" in source


def test_creating_row_is_committed_before_the_panel_call():
    """Сбой панели обязан оставить след, по которому крон доведёт профиль."""
    source = inspect.getsource(family.create_profile)
    assert source.index("await session.commit()") < source.index("panel_create(")


def test_deleting_is_committed_before_the_panel_call():
    source = inspect.getsource(family.delete_profile)
    assert source.index("status = 'deleting'") < source.index("await session.commit()")
    assert source.index("await session.commit()") < source.index("_delete_now(")


def test_reserve_and_pause_are_read_as_open_only():
    assert "active = true" in family.PAUSE_RESERVE_SQL
    assert "ended = false" in family.PAUSE_RESERVE_SQL
    assert "reserve_expire_at > now()" in family.PAUSE_RESERVE_SQL


def test_traffic_reset_follows_only_real_plan_purchases():
    """Пополнение и докупки (id < 0) не продлевают период и не обнуляют трафик семьи."""
    assert "t.status::text = 'COMPLETED'" in family.LAST_PURCHASE_SQL
    assert "t.is_test = false" in family.LAST_PURCHASE_SQL
    assert "ELSE 0 END) > 0" in family.LAST_PURCHASE_SQL


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
