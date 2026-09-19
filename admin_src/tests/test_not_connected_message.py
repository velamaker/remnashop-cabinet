"""Подсказка «не подключился»: помогаем подключиться, а не отправляем в поддержку.

ЗАЧЕМ. Панель умеет звать тех, у кого подписка есть, а подключений не было; бот это
событие принимает давно, но базовый текст спрашивает «Не получилось подключиться?» и даёт
одну кнопку — в поддержку. Замер по боевой базе: из 78 истёкших пробных 37 не подключились
НИ РАЗУ, а 15 успели завести устройство и дальше не пошли. Им нужна кнопка «подключить»,
а не переписка.

ЧТО ЗАПЕРТО:
  * текст и кнопки подменяются ТОЛЬКО для этого события (остальные уведомления не трогаем);
  * есть кнопка в кабинет и кнопка поддержки, и ни одна не появляется без адреса;
  * сообщение не самоудаляется (дефолт DTO — 5 секунд, грабля рассылок);
  * английский текст для англоязычных.

Данные синтетические.
"""

import importlib
from types import SimpleNamespace

import pytest

notifications = importlib.import_module("overlay_patches.notifications")


class Service(notifications.OverlayNotificationService):
    """Только то, что нужно методу: конфиг с адресом кабинета."""

    def __init__(self, cabinet: str = "https://cab.example") -> None:  # noqa: D107
        self.config = SimpleNamespace(web_cabinet_url=cabinet)


def event(lang: str = "RU", support: str = "https://t.me/support"):
    return SimpleNamespace(
        user=SimpleNamespace(id=1, language=lang, telegram_id=555),
        support_url=support,
    )


def buttons(payload) -> list[str]:
    markup = payload.reply_markup
    return [b.text for row in (markup.inline_keyboard if markup else []) for b in row]


def urls(payload) -> list[str]:
    markup = payload.reply_markup
    return [b.url for row in (markup.inline_keyboard if markup else []) for b in row]


def test_message_offers_the_way_in_not_a_ticket():
    payload = Service()._not_connected_payload(event())
    text = payload.i18n_kwargs["content"]
    assert "осталось подключить" in text
    assert payload.i18n_key == "raw-message"
    # Кнопка в кабинет — первая: это и есть действие, ради которого пишем.
    assert buttons(payload) == ["⚡ Подключить", "💬 Поддержка"]
    assert urls(payload)[0] == "https://cab.example/devices"


def test_message_never_self_destructs():
    """Дефолт DTO — 5 секунд: уведомление исчезло бы из чата."""
    assert Service()._not_connected_payload(event()).delete_after is None


def test_english_user_gets_english():
    payload = Service()._not_connected_payload(event(lang="EN"))
    assert "one step left" in payload.i18n_kwargs["content"]
    assert buttons(payload) == ["⚡ Connect", "💬 Support"]


def test_no_cabinet_no_dead_button():
    payload = Service(cabinet="")._not_connected_payload(event())
    assert buttons(payload) == ["💬 Поддержка"]


def test_no_addresses_at_all_still_sends_text():
    payload = Service(cabinet="")._not_connected_payload(event(support=""))
    assert payload.reply_markup is None
    assert payload.i18n_kwargs["content"]


# ── кнопка «Продлить» ────────────────────────────────────────────────────────


class RenewEvent:
    """Событие об окончании подписки: боту известны только эти два факта."""

    def __init__(self, is_trial: bool = False, lang: str = "RU") -> None:
        self.is_trial = is_trial
        self.user = SimpleNamespace(id=1, language=lang, telegram_id=555)


def test_renew_button_leads_straight_to_payment():
    """Базовая кнопка открывала диалог покупки: тариф → срок → способ → оплатить.

    Для продления всё это уже известно, и четыре шага ради «то же самое ещё раз» —
    ровно то место, где люди отваливаются.
    """
    markup = Service()._renew_keyboard(RenewEvent())
    button = markup.inline_keyboard[0][0]
    assert button.text == "⚡ Продлить"
    assert button.url == "https://cab.example/billing?renew=1"


def test_trial_gets_a_choice_not_a_renewal():
    """Пробнику продлевать нечего — ему нужен выбор тарифа."""
    markup = Service()._renew_keyboard(RenewEvent(is_trial=True))
    button = markup.inline_keyboard[0][0]
    assert button.text == "⚡ Выбрать тариф"
    assert button.url == "https://cab.example/billing"


def test_english_renew_button():
    markup = Service()._renew_keyboard(RenewEvent(lang="EN"))
    assert markup.inline_keyboard[0][0].text == "⚡ Renew"


def test_without_cabinet_base_keyboard_stays():
    """Нет адреса кабинета — возвращаем None, чтобы осталась базовая клавиатура."""
    assert Service(cabinet="")._renew_keyboard(RenewEvent()) is None
