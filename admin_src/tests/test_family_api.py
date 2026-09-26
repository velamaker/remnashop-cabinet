"""Публичные ручки «Семьи»: что видит человек и чего не видит чужой.

ЧТО ЗАПЕРТО:
  * функция выключена и своих профилей нет — ответ пустой: пункта «Семья» у
    человека быть не должно, и условия тарифа ему не рассказываем;
  * профили уже есть — их видно и при выключенной функции (своя ссылка и «Удалить»);
  * ссылка есть только у работающего профиля;
  * чужой профиль — 404, как несуществующий: номер профиля ничего не рассказывает;
  * отказ панели при создании — 502, чужой request_id — 409.

Зависимости ручек подменены: здесь проверяется сама ручка, а не сервис (он — в
test_family_pg.py на живой базе).
"""

import importlib
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

endpoint = importlib.import_module("src.web.endpoints.public.family")
family = importlib.import_module("src.infrastructure.services.overlay_family")

USER = SimpleNamespace(id=10)
NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)


class Session:
    async def rollback(self):
        return None


class Settings:
    async def get(self):
        return SimpleNamespace(extra=SimpleNamespace(device_all_reset=SimpleNamespace(enabled=True, cooldown_hours=24)))


def profile(**kw):
    base = {
        "id": 5,
        "label": "Мама",
        "status": "active",
        "suspend_reason": None,
        "expired": False,
        "expire_at": NOW,
        "url": "https://sub.example/abc",
        "device_limit": 2,
        "devices": 1,
        "traffic_limit_bytes": 0,
        "traffic_used_bytes": None,
        "created_at": NOW,
        "device_reset_at": None,
    }
    base.update(kw)
    return base


def view(**kw):
    base = {
        "enabled": True,
        "available": True,
        "reason": None,
        "plan_name": "Семейный",
        "terms": {"max_profiles": 3, "devices_per_profile": 2},
        "used": 1,
        "profiles": [profile()],
    }
    base.update(kw)
    return base


@pytest.fixture
def served(monkeypatch):
    """Ручка GET /family с подменённым сервисом: что отдал сервис, то и видит тест."""
    state = {"view": view(), "config": {"enabled": True, "suspend_grace_days": 30}, "light": None}

    async def fake_view(session, sdk, owner_id, *, config=None, with_panel=True):
        assert owner_id == USER.id
        state["light"] = not with_panel
        return state["view"]

    monkeypatch.setattr(family, "family_view", fake_view)
    monkeypatch.setattr(family, "load_config", lambda: state["config"])
    raw = endpoint.get_family.__dishka_orig_func__

    async def call(light=False):
        return await raw(
            user=USER,
            session=Session(),
            remnawave=SimpleNamespace(sdk=object()),
            settings_dao=Settings(),
            light=light,
        )

    return state, call


async def test_disabled_without_profiles_tells_nothing(served):
    state, call = served
    state["config"] = {"enabled": False}
    state["view"] = view(enabled=False, available=False, reason="disabled", profiles=[])
    assert await call() == {"enabled": False, "available": False, "profiles": []}


async def test_existing_profiles_stay_visible_when_disabled(served):
    state, call = served
    state["config"] = {"enabled": False}
    state["view"] = view(enabled=False, available=False, reason="disabled")
    data = await call()
    assert [p["label"] for p in data["profiles"]] == ["Мама"]
    assert data["available"] is False


async def test_payload_shape_and_reset_rules(served):
    _state, call = served
    data = await call()
    p = data["profiles"][0]
    assert p["url"] == "https://sub.example/abc"
    assert p["expire_at"] == NOW.isoformat()
    assert data["terms"] == {"max_profiles": 3, "devices_per_profile": 2}
    assert data["reset_devices"] == {"enabled": True, "cooldown_hours": 24}


async def test_link_only_for_a_working_profile(served):
    state, call = served
    state["view"] = view(profiles=[profile(status="suspended", suspend_reason="plan")])
    assert (await call())["profiles"][0]["url"] is None


async def test_light_request_skips_the_panel(served):
    state, call = served
    await call(light=True)
    assert state["light"] is True
    await call()
    assert state["light"] is False


async def test_someone_elses_profile_is_404(monkeypatch):
    async def fake_load(session, profile_id):
        return SimpleNamespace(owner_user_id=99, status="active")

    monkeypatch.setattr(family, "load_profile", fake_load)
    with pytest.raises(HTTPException) as err:
        await endpoint._owned(Session(), USER.id, 5)
    assert err.value.status_code == 404


@pytest.mark.parametrize("outcome, code", [("failed", 502), ("conflict", 409)])
async def test_create_maps_hard_failures_to_http(monkeypatch, outcome, code):
    async def fake_create(*args, **kwargs):
        return {"result": outcome}

    monkeypatch.setattr(family, "create_profile", fake_create)
    raw = endpoint.create_family_profile.__dishka_orig_func__
    body = endpoint.CreateProfileRequest(request_id="11111111-2222-3333-4444-555555555555", label="Мама")
    with pytest.raises(HTTPException) as err:
        await raw(
            body=body,
            user=USER,
            session=Session(),
            remnawave=SimpleNamespace(sdk=object()),
            user_dao=None,
            subscription_dao=None,
        )
    assert err.value.status_code == code


async def test_create_without_panel_is_503():
    raw = endpoint.create_family_profile.__dishka_orig_func__
    body = endpoint.CreateProfileRequest(request_id="11111111-2222-3333-4444-555555555555", label="Мама")
    with pytest.raises(HTTPException) as err:
        await raw(body=body, user=USER, session=Session(), remnawave=SimpleNamespace(sdk=None), user_dao=None, subscription_dao=None)
    assert err.value.status_code == 503
