"""Семья в боте: тексты, кнопки и вход из главного меню.

ЧТО ЗАПЕРТО:
  * имя профиля пишет человек, а бот шлёт HTML — имя экранируется везде;
  * у приостановленного профиля нет «Ссылки» и «Сброса» — только «Удалить»;
  * «Поделиться» ведёт в t.me/share с закодированной ссылкой;
  * ответ на одно приглашение — один профиль, сколько бы раз он ни пришёл;
  * имя принимается только ответом на НАШЕ приглашение;
  * кнопка «Семья» в главном меню: видна по правилу сервиса, а любая беда внутри
    оставляет меню живым (цена ошибки в этом геттере — бот без меню, см. 18.09).
"""

import importlib
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

bot = importlib.import_module("src.telegram.routers.overlay_family")
family = importlib.import_module("src.infrastructure.services.overlay_family")


def profile(**kw):
    base = {
        "id": 5,
        "label": "Мама",
        "status": "active",
        "suspend_reason": None,
        "expired": False,
        "expire_at": datetime(2026, 10, 26, tzinfo=timezone.utc),
        "url": "https://sub.example/abc",
        "device_limit": 2,
        "devices": 0,
        "traffic_limit_bytes": 100 * 1024**3,
        "traffic_used_bytes": 3 * 1024**3,
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
        "profiles": [profile()],
    }
    base.update(kw)
    return base


def buttons(markup):
    return [b.text for row in markup.inline_keyboard for b in row]


def test_list_shows_devices_and_add_button():
    text, markup = bot.list_view(view())
    assert "Мама · 📱 0/2" in text
    assert "до 3 профилей" in text
    assert "➕ Добавить профиль" in buttons(markup)


def test_label_is_escaped_everywhere():
    evil = profile(label="<b>x</b>&")
    text, _ = bot.list_view(view(profiles=[evil]))
    assert "<b>x</b>" not in text and "&lt;b&gt;x&lt;/b&gt;&amp;" in text
    text, _ = bot.card_view(evil)
    assert "&lt;b&gt;" in text
    text, _ = bot.link_view(evil["label"], "https://sub.example/<a>")
    assert "&lt;b&gt;" in text and "&lt;a&gt;" in text


def test_no_add_button_when_not_available_and_reason_is_told():
    text, markup = bot.list_view(view(available=False, reason="max_reached"))
    assert "➕ Добавить профиль" not in buttons(markup)
    assert "профилей уже максимум" in text


def test_suspended_profile_has_only_delete():
    text, markup = bot.card_view(profile(status="suspended", suspend_reason="plan"))
    names = buttons(markup)
    assert "🔗 Ссылка" not in names and "♻️ Сбросить устройства" not in names
    assert "🗑 Удалить" in names
    assert "тариф больше не семейный" in text


def test_share_button_encodes_the_link():
    _text, markup = bot.link_view("Мама", "https://sub.example/a?b=c&d")
    url = markup.inline_keyboard[0][0].url
    assert url.startswith("https://t.me/share/url?url=https%3A%2F%2Fsub.example%2Fa%3Fb%3Dc%26d")


def test_one_prompt_one_profile():
    a = bot.request_id_for(100, 7, 42)
    assert a == bot.request_id_for(100, 7, 42)
    assert a != bot.request_id_for(100, 8, 42)
    assert a != bot.request_id_for(100, 7, 43)


def _message(reply_text, *, from_bot=True):
    reply = SimpleNamespace(text=reply_text, from_user=SimpleNamespace(is_bot=from_bot))
    return SimpleNamespace(reply_to_message=reply)


def test_name_is_taken_only_as_a_reply_to_our_prompt():
    assert bot._is_prompt_reply(_message(bot.PROMPT_MARK + "\n\nКак назвать?"))
    assert not bot._is_prompt_reply(_message("что-то другое"))
    assert not bot._is_prompt_reply(_message(bot.PROMPT_MARK, from_bot=False))
    assert not bot._is_prompt_reply(SimpleNamespace(reply_to_message=None))


def test_router_is_connected_before_base_routers():
    routers = importlib.import_module("overlay_patches.bot_routers")
    assert "src.telegram.routers.overlay_family" in dict(routers._ROUTERS)


# ── кнопка в главном меню ───────────────────────────────────────────────────


@pytest.fixture
def menu():
    return importlib.import_module("overlay_patches.menu_dialog")


class _Session:
    async def rollback(self):
        return None


@pytest.mark.parametrize("visible", [True, False])
async def test_menu_button_follows_the_service_rule(menu, monkeypatch, visible):
    async def fake_visible(session, owner_id, now=None):
        assert owner_id == 7
        return visible

    monkeypatch.setattr(family, "menu_visible", fake_visible)
    data: dict = {}
    await menu._family_button(data, _Session(), {"user": SimpleNamespace(id=7)})
    assert data["family_button"] is visible
    assert data["family_text"] == bot.MENU_BUTTON_TEXT


async def test_menu_survives_any_failure(menu, monkeypatch):
    async def broken(session, owner_id, now=None):
        raise RuntimeError("база легла")

    monkeypatch.setattr(family, "menu_visible", broken)
    data: dict = {}
    await menu._family_button(data, _Session(), {"user": SimpleNamespace(id=7)})
    assert data["family_button"] is False


async def test_menu_visible_is_off_while_the_feature_is_off(monkeypatch, tmp_path):
    monkeypatch.setattr(family, "CONFIG_PATH", tmp_path / "family.json")

    class NoDb:
        async def execute(self, *a, **kw):
            raise AssertionError("выключенная функция не ходит в базу")

    assert await family.menu_visible(NoDb(), 7) is False
