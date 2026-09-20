"""Предохранители ручки «удалить человека» в админке.

ЧТО ЗАПИРАЕМ. Сами шаги удаления проверяет test_user_purge_pg на живой базе,
здесь — ровно то, из-за чего необратимое действие становится бедой:

  * право. Раздел «Пользователи» дают и поддержке (пресет support), но удалять
    человека — не её работа: ручка требует ПОЛНОГО доступа, а не раздела;
  * себя не удалить — иначе админ вылетает из собственной админки;
  * владельца не удалить — как и в блокировке по соседству;
  * без слова подтверждения ничего не происходит, и панель никто не дёргает;
  * панель не ответила → 502 и НИЧЕГО не удалено: «полуудалённый» человек с
    работающим VPN хуже неудалённого, и админ обязан увидеть отказ.

Порядок проверок тоже важен и тоже заперт: право и слово проверяются ДО того,
как код пойдёт в базу и в панель.
"""

import importlib
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

users_admin = importlib.import_module("src.web.endpoints.admin.users")
purge = importlib.import_module("src.infrastructure.services.overlay_user_purge")

ADMIN_ID = 1
TARGET_ID = 42
_DEFAULT = object()  # «цель не задана» ≠ «человека нет»


class FakeRequest:
    def __init__(self, access):
        self.state = SimpleNamespace(admin_access=access)


class FakeUserDao:
    def __init__(self, user):
        self.user = user
        self.asked = []

    async def get_by_id(self, user_id):
        self.asked.append(user_id)
        return self.user


class FakeSession:
    def __init__(self):
        self.commits = 0

    async def commit(self):
        self.commits += 1


FULL = {"allowed": True, "full_access": True, "can_write": True, "is_owner": False}
SECTIONS = {"allowed": True, "full_access": False, "can_write": True, "sections": ["users"]}


def user(user_id=TARGET_ID, role=1):
    return SimpleNamespace(id=user_id, role=role, name="Кто-то")


async def call(
    *,
    access=FULL,
    confirm="УДАЛИТЬ",
    target=_DEFAULT,
    purge_result=None,
    purge_error=None,
):
    """Зовёт ручку с подменённым удалением — до базы и панели дело не доходит."""
    raw = users_admin.delete_user.__dishka_orig_func__
    calls = []

    async def fake_purge(session, remnawave, user_id):
        calls.append(user_id)
        if purge_error is not None:
            raise purge_error
        return purge_result or {"mode": "purged", "panel_accounts_removed": 1}

    original = users_admin.purge_user
    users_admin.purge_user = fake_purge
    session = FakeSession()
    try:
        result = await raw(
            user_id=TARGET_ID,
            body=users_admin.DeleteUserRequest(confirm=confirm),
            request=FakeRequest(access),
            admin=user(ADMIN_ID, role=5),
            user_dao=FakeUserDao(user() if target is _DEFAULT else target),
            remnawave=object(),
            session=session,
        )
    finally:
        users_admin.purge_user = original
    return result, calls, session


async def test_удаляет_и_говорит_как_именно():
    result, calls, session = await call()
    assert result == {"success": True, "mode": "purged", "panel_accounts_removed": 1}
    assert calls == [TARGET_ID]
    assert session.commits == 1


async def test_за_платившим_остаётся_обезличенная_запись():
    result, _, _ = await call(purge_result={"mode": "anonymized", "panel_accounts_removed": 1})
    assert result["mode"] == "anonymized"


async def test_нарезанному_по_разделам_админу_нельзя():
    with pytest.raises(HTTPException) as exc:
        await call(access=SECTIONS)
    assert exc.value.status_code == 403
    assert "полным доступом" in exc.value.detail


@pytest.mark.parametrize("word", ["ну давай", "удали", ""])
async def test_без_слова_подтверждения_ничего_не_делаем(word):
    with pytest.raises(HTTPException) as exc:
        await call(confirm=word)
    assert exc.value.status_code == 400
    assert "УДАЛИТЬ" in exc.value.detail


async def test_английское_слово_тоже_подходит():
    result, calls, _ = await call(confirm="delete")
    assert result["success"] is True
    assert calls == [TARGET_ID]


async def test_себя_удалить_нельзя():
    with pytest.raises(HTTPException) as exc:
        await call(target=user(ADMIN_ID))
    assert exc.value.status_code == 400
    assert "себя" in exc.value.detail


async def test_владельца_удалить_нельзя():
    with pytest.raises(HTTPException) as exc:
        await call(target=user(role=5))
    assert exc.value.status_code == 403
    assert "владельца" in exc.value.detail


async def test_нет_такого_человека():
    raw = users_admin.delete_user.__dishka_orig_func__
    with pytest.raises(HTTPException) as exc:
        await raw(
            user_id=TARGET_ID,
            body=users_admin.DeleteUserRequest(confirm="УДАЛИТЬ"),
            request=FakeRequest(FULL),
            admin=user(ADMIN_ID, role=5),
            user_dao=FakeUserDao(None),
            remnawave=object(),
            session=FakeSession(),
        )
    assert exc.value.status_code == 404


async def test_панель_молчит_значит_502_и_ничего_не_удалено():
    with pytest.raises(HTTPException) as exc:
        await call(purge_error=purge.PanelUnavailable("panel is down"))
    assert exc.value.status_code == 502
    assert "НЕ удалён" in exc.value.detail


# ─── Порядок способов оплаты ──────────────────────────────────────────────────
#
# ЗАЧЕМ ЗДЕСЬ. Ручка короткая, но ошибиться в ней дорого: порядок задаёт, каким
# способом платит большинство (первый подставляется сам). Запираем два правила —
# список принимается только ЦЕЛИКОМ и раскладывается по позициям от единицы.


class _Gateway:
    def __init__(self, gateway_id, order_index):
        self.id = gateway_id
        self.order_index = order_index
        self.type = SimpleNamespace(value=f"G{gateway_id}")
        self.currency = SimpleNamespace(value="RUB")
        self.is_active = True
        self.settings = SimpleNamespace(is_configured=True, display_name=None)


class _GatewayDao:
    def __init__(self, gateways):
        self.gateways = gateways
        self.updated = []

    async def get_all(self, only_active=False, sorted=True):  # noqa: A002 — сигнатура вендора
        return sorted_by_order(self.gateways) if sorted else list(self.gateways)

    async def update(self, gateway):
        self.updated.append((gateway.id, gateway.order_index))
        return gateway


def sorted_by_order(gateways):
    return sorted(gateways, key=lambda g: g.order_index)


async def _reorder(ids, gateways):
    gateways_admin = importlib.import_module("src.web.endpoints.admin.gateways")
    raw = gateways_admin.reorder_gateways.__dishka_orig_func__
    dao = _GatewayDao(gateways)
    result = await raw(
        body=gateways_admin.ReorderRequest(ids=ids),
        _admin=user(ADMIN_ID, role=5),
        gateway_dao=dao,
        session=FakeSession(),
    )
    return result, dao


async def test_порядок_шлюзов_раскладывается_от_единицы():
    gateways = [_Gateway(1, 1), _Gateway(3, 2), _Gateway(7, 3)]
    result, dao = await _reorder([7, 1, 3], gateways)

    assert [g["id"] for g in result["items"]] == [7, 1, 3]
    # Пишем только тех, у кого позиция изменилась.
    assert dict(dao.updated) == {7: 1, 1: 2, 3: 3}


async def test_неполный_список_шлюзов_не_принимаем():
    gateways = [_Gateway(1, 1), _Gateway(3, 2)]
    with pytest.raises(HTTPException) as exc:
        await _reorder([1], gateways)
    assert exc.value.status_code == 400


async def test_повтор_в_списке_шлюзов_не_принимаем():
    gateways = [_Gateway(1, 1), _Gateway(3, 2)]
    with pytest.raises(HTTPException) as exc:
        await _reorder([1, 1], gateways)
    assert exc.value.status_code == 400
