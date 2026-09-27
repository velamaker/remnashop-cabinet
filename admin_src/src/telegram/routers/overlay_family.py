"""Семья в боте: профили для близких к семейному тарифу владельца.

Раздел открывается кнопкой «👨‍👩‍👧 Семья» главного меню (она видна, только когда
функция включена и тариф человека семейный — см. overlay_patches/menu_dialog.py).
Дальше — обычные сообщения с кнопками, а не окна диалога: окно меню остаётся на
месте, как у подарков.

ЧТО УМЕЕТ:
  * список профилей («Мама · 📱 0/2») и «Добавить профиль» — имя присылается
    ответом на сообщение бота (ForceReply): так текст не попадает в «умный поиск»
    главного окна и не зависит от состояния диалога;
  * карточка профиля: «Ссылка» (текстом, чтобы переслать, и кнопкой «Поделиться»),
    «Сбросить устройства», «Удалить» с двойным подтверждением.

Вся логика — в services/overlay_family.py; здесь только тексты и кнопки. Имя
профиля в сообщениях экранируется: бот шлёт HTML, а имя пишет человек.

ИМЕНА ЗАВИСИМОСТЕЙ НАРОЧНО С ПРЕФИКСОМ `fam_`: обработчики принимают `**data`, и
зависимость dishka с именем ключа данных апдейта (`config`, `bot`, `user`…) пришла
бы дважды — сторож tests/test_dishka_handler_kwargs.py.
"""

from __future__ import annotations

import html
import uuid as uuid_lib
from contextlib import suppress
from datetime import datetime
from typing import Any, Optional
from urllib.parse import quote

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    CallbackQuery,
    ForceReply,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram_dialog import ShowMode, StartMode
from dishka import FromDishka
from dishka.integrations.aiogram import inject
from dishka.integrations.aiogram_dialog import inject as dialog_inject
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.common import Remnawave
from src.application.common.dao import SettingsDao, SubscriptionDao, UserDao
from src.core.constants import USER_KEY
from src.infrastructure.services import overlay_family as family

try:
    from src.telegram.states import MainMenu as _MainMenu
except Exception:  # noqa: BLE001 — обновление базы не должно ломать семью
    _MainMenu = None

router = Router(name="overlay_family")

_PREFIX = "fam"
# Первая строка приглашения ввести имя. По ней ответ человека узнаётся как имя
# нового профиля — и только ответ на НАШЕ сообщение с этой строкой.
PROMPT_MARK = "👨‍👩‍👧 Новый профиль"
# Пространство для request_id из бота: один ответ на одно приглашение — один профиль.
_REQUEST_NS = uuid_lib.UUID("5f0c6a1e-2b7d-4c61-9d7e-6a3f0b1c8e42")

TITLE = "👨‍👩‍👧 <b>Семья</b>"
# Очередь семьи занята (крон как раз сверяет её с панелью): не ждём, а просим повторить.
BUSY_TEXT = "Семья сейчас обновляется — попробуйте через минуту"
MENU_BUTTON_TEXT = "👨‍👩‍👧 Семья"


def _user(data: dict[str, Any]) -> Any:
    return data.get(USER_KEY)


def _esc(value: Any) -> str:
    return html.escape(str(value)) if value is not None else ""


def _date(value: Any) -> str:
    return value.strftime("%d.%m.%Y") if isinstance(value, datetime) else "—"


def _gb(value: Optional[int]) -> str:
    if value is None:
        return "—"
    gb = value / 1024**3
    return f"{gb:.1f} ГБ" if gb < 100 else f"{gb:.0f} ГБ"


def _devices(p: dict) -> str:
    count = p.get("devices")
    limit = p.get("device_limit") or 0
    return f"{count if count is not None else '?'}/{limit}"


def _status_line(p: dict) -> str:
    if p["status"] == "creating":
        return "создаётся…"
    if p["status"] == "deleting":
        return "удаляется…"
    if p["status"] == "suspended":
        return f"приостановлен — {family.reason_ru(p.get('suspend_reason'))}"
    if p.get("expired"):
        return "срок закончился — продлите подписку"
    return "активен"


def _menu_row() -> list[list[InlineKeyboardButton]]:
    if _MainMenu is None:
        return []
    return [[InlineKeyboardButton(text="🏠 Главное меню", callback_data=f"{_PREFIX}:menu")]]


def list_view(view: dict) -> tuple[str, InlineKeyboardMarkup]:
    """Список профилей и что можно сделать. Чистая функция — её проверяют тесты."""
    lines = [TITLE, ""]
    terms = view.get("terms")
    if terms:
        plan = f"«{_esc(view.get('plan_name'))}»" if view.get("plan_name") else "семейный"
        lines.append(
            f"Тариф {plan}: до {terms['max_profiles']} профилей, по "
            f"{terms['devices_per_profile']} устр. в каждом и весь трафик тарифа."
        )
        lines.append("Участнику аккаунт не нужен — перешлите ему ссылку профиля.")
        lines.append("")
    rows: list[list[InlineKeyboardButton]] = []
    profiles = view.get("profiles") or []
    if not profiles:
        lines.append("Профилей пока нет.")
    for p in profiles:
        suffix = "" if p["status"] == "active" and not p.get("expired") else f" · {_status_line(p)}"
        lines.append(f"• {_esc(p['label'])} · 📱 {_devices(p)}{_esc(suffix)}")
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{p['label']} · 📱 {_devices(p)}"[:60],
                    callback_data=f"{_PREFIX}:p:{p['id']}",
                )
            ]
        )
    if view.get("available"):
        rows.append([InlineKeyboardButton(text="➕ Добавить профиль", callback_data=f"{_PREFIX}:add")])
    elif view.get("reason") == "disabled":
        # Функцию выключили, а профили остались: их видно и можно удалить, новых нет.
        lines.append("")
        lines.append("Новые профили сейчас не заводятся.")
    elif view.get("reason"):
        lines.append("")
        lines.append(f"Добавить профиль сейчас нельзя: {family.reason_ru(view['reason'])}.")
    rows += _menu_row()
    rows.append([InlineKeyboardButton(text="✖️ Закрыть", callback_data=f"{_PREFIX}:close")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


def card_view(p: dict) -> tuple[str, InlineKeyboardMarkup]:
    """Карточка профиля. Кнопки ссылки и сброса — только у работающего профиля."""
    limit_bytes = p.get("traffic_limit_bytes")
    traffic = (
        "без ограничений"
        if limit_bytes == 0
        else f"{_gb(p.get('traffic_used_bytes'))} из {_gb(limit_bytes)}"
    )
    text = "\n".join(
        [
            f"👤 <b>{_esc(p['label'])}</b>",
            "",
            f"Статус: {_esc(_status_line(p))}",
            f"Устройства: {_devices(p)}",
            f"Трафик: {traffic}",
            f"Действует до: {_date(p.get('expire_at'))}",
        ]
    )
    rows: list[list[InlineKeyboardButton]] = []
    if p["status"] == "active":
        rows.append([InlineKeyboardButton(text="🔗 Ссылка", callback_data=f"{_PREFIX}:l:{p['id']}")])
        rows.append(
            [InlineKeyboardButton(text="♻️ Сбросить устройства", callback_data=f"{_PREFIX}:r:{p['id']}")]
        )
    if p["status"] in ("active", "suspended", "creating"):
        rows.append([InlineKeyboardButton(text="🗑 Удалить", callback_data=f"{_PREFIX}:d:{p['id']}")])
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data=f"{_PREFIX}:list")])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def link_view(label: str, url: str) -> tuple[str, InlineKeyboardMarkup]:
    """Ссылка текстом (её пересылают) и кнопкой «Поделиться» через t.me/share."""
    note = f"Подписка «{label}» — добавьте ссылку в VPN-приложение"
    share = f"https://t.me/share/url?url={quote(url, safe='')}&text={quote(note, safe='')}"
    text = (
        f"🔗 Ссылка профиля «{_esc(label)}»:\n\n<code>{_esc(url)}</code>\n\n"
        "Перешлите её участнику: он добавит подписку в приложение. Аккаунт ему не нужен."
    )
    return text, InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="📤 Поделиться", url=share)]]
    )


def _link_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    try:
        from src.web.endpoints.public.sub_alias import maybe_alias_url

        return maybe_alias_url(url)
    except Exception:  # noqa: BLE001 — алиас не сработал: отдаём настоящую ссылку
        return url


async def _view(fam_session: AsyncSession, fam_panel: Remnawave, owner_id: int) -> dict:
    return await family.family_view(fam_session, getattr(fam_panel, "sdk", None), owner_id)


def _find(view: dict, profile_id: int) -> Optional[dict]:
    return next((p for p in view.get("profiles") or [] if p["id"] == profile_id), None)


def _profile_id(callback: CallbackQuery) -> Optional[int]:
    try:
        return int((callback.data or "").rsplit(":", 1)[1])
    except (IndexError, ValueError):
        return None


async def _edit(callback: CallbackQuery, text: str, markup: InlineKeyboardMarkup) -> None:
    if callback.message is None:
        return
    try:
        await callback.message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
    except TelegramBadRequest as exc:
        # «message is not modified» — повторное нажатие на ту же кнопку: не ошибка.
        if "not modified" not in str(exc):
            raise


# ── вход из главного меню ───────────────────────────────────────────────────


@dialog_inject
async def open_family_from_menu(
    callback: CallbackQuery,
    widget: Any,
    dialog_manager: Any,
    fam_session: FromDishka[AsyncSession],
    fam_panel: FromDishka[Remnawave],
) -> None:
    """Кнопка «Семья» главного меню: список отдельным сообщением, меню остаётся."""
    user = dialog_manager.middleware_data.get(USER_KEY)
    if user is None or callback.message is None:
        await callback.answer()
        return
    view = await _view(fam_session, fam_panel, user.id)
    if not view.get("enabled") and not view.get("profiles"):
        await callback.answer("Раздел сейчас недоступен", show_alert=True)
        return
    text, markup = list_view(view)
    await callback.message.answer(text, reply_markup=markup, disable_web_page_preview=True)
    await callback.answer()


@router.callback_query(F.data == f"{_PREFIX}:menu")
async def on_family_menu(callback: CallbackQuery, **data: Any) -> None:
    """Открыть главное меню — так же, как это делает /start базы."""
    manager = data.get("dialog_manager")
    try:
        if manager is None or _MainMenu is None:
            raise RuntimeError("менеджер диалогов или состояние меню недоступны")
        await manager.start(
            state=_MainMenu.MAIN, mode=StartMode.RESET_STACK, show_mode=ShowMode.DELETE_AND_SEND
        )
    except Exception as exc:  # noqa: BLE001 — кнопка не должна ронять раздел
        logger.warning(f"family: не смог открыть главное меню: {exc}")
        with suppress(Exception):
            await callback.answer("Не удалось открыть меню — нажмите /start", show_alert=True)
        return
    with suppress(Exception):
        await callback.answer()


@router.callback_query(F.data == f"{_PREFIX}:close")
async def on_family_close(callback: CallbackQuery, **_data: Any) -> None:
    if callback.message is not None:
        try:
            await callback.message.delete()
        except TelegramBadRequest:
            with suppress(TelegramBadRequest):
                await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer()


@router.callback_query(F.data == f"{_PREFIX}:list")
@inject
async def on_family_list(
    callback: CallbackQuery,
    fam_session: FromDishka[AsyncSession],
    fam_panel: FromDishka[Remnawave],
    **data: Any,
) -> None:
    user = _user(data)
    if user is None:
        await callback.answer()
        return
    text, markup = list_view(await _view(fam_session, fam_panel, user.id))
    await _edit(callback, text, markup)
    await callback.answer()


@router.callback_query(F.data.startswith(f"{_PREFIX}:p:"))
@inject
async def on_family_profile(
    callback: CallbackQuery,
    fam_session: FromDishka[AsyncSession],
    fam_panel: FromDishka[Remnawave],
    **data: Any,
) -> None:
    user = _user(data)
    profile_id = _profile_id(callback)
    if user is None or profile_id is None:
        await callback.answer()
        return
    p = _find(await _view(fam_session, fam_panel, user.id), profile_id)
    if p is None:
        await callback.answer("Профиль не найден", show_alert=True)
        return
    text, markup = card_view(p)
    await _edit(callback, text, markup)
    await callback.answer()


@router.callback_query(F.data.startswith(f"{_PREFIX}:l:"))
@inject
async def on_family_link(
    callback: CallbackQuery,
    fam_session: FromDishka[AsyncSession],
    fam_panel: FromDishka[Remnawave],
    **data: Any,
) -> None:
    """Ссылка — НОВЫМ сообщением: его пересылают участнику целиком."""
    user = _user(data)
    profile_id = _profile_id(callback)
    if user is None or profile_id is None or callback.message is None:
        await callback.answer()
        return
    p = _find(await _view(fam_session, fam_panel, user.id), profile_id)
    url = _link_url(p.get("url")) if p else None
    if p is None or p["status"] != "active" or not url:
        await callback.answer("Ссылка сейчас недоступна", show_alert=True)
        return
    text, markup = link_view(p["label"], url)
    await callback.message.answer(text, reply_markup=markup, disable_web_page_preview=True)
    await callback.answer()


@router.callback_query(F.data.startswith(f"{_PREFIX}:r:"))
@inject
async def on_family_reset(
    callback: CallbackQuery,
    fam_session: FromDishka[AsyncSession],
    fam_panel: FromDishka[Remnawave],
    fam_settings: FromDishka[SettingsDao],
    **data: Any,
) -> None:
    user = _user(data)
    profile_id = _profile_id(callback)
    if user is None or profile_id is None:
        await callback.answer()
        return
    reset_enabled, cooldown = True, 0
    with suppress(Exception):
        rule = (await fam_settings.get()).extra.device_all_reset
        reset_enabled, cooldown = bool(rule.enabled), int(rule.cooldown_hours or 0)
    result = await family.reset_profile_devices(
        fam_session,
        getattr(fam_panel, "sdk", None),
        owner_id=user.id,
        profile_id=profile_id,
        actor="bot",
        reset_enabled=reset_enabled,
        cooldown_hours=cooldown,
    )
    answer = {
        "reset": "Устройства профиля отвязаны — подключитесь заново",
        "not_found": "Профиль не найден",
        "panel_error": "Сервер не ответил — попробуйте позже",
        "busy": BUSY_TEXT,
    }.get(result.get("result"))
    if result.get("result") == "cooldown":
        answer = f"Сбросить снова можно после {result['available_at'].strftime('%d.%m %H:%M')} (UTC)"
    elif result.get("result") == "not_available":
        answer = (
            "Сброс устройств выключен"
            if result.get("reason") == "disabled"
            else "Профиль приостановлен — сброс недоступен"
        )
    await callback.answer(answer or "Готово", show_alert=True)


@router.callback_query(F.data.startswith(f"{_PREFIX}:d:"))
@inject
async def on_family_delete_ask(
    callback: CallbackQuery,
    fam_session: FromDishka[AsyncSession],
    fam_panel: FromDishka[Remnawave],
    **data: Any,
) -> None:
    user = _user(data)
    profile_id = _profile_id(callback)
    if user is None or profile_id is None:
        await callback.answer()
        return
    p = _find(await _view(fam_session, fam_panel, user.id), profile_id)
    if p is None:
        await callback.answer("Профиль не найден", show_alert=True)
        return
    text = (
        f"Удалить профиль «{_esc(p['label'])}»?\n\n"
        "Его ссылка перестанет работать сразу, устройства отключатся."
    )
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Да, удалить", callback_data=f"{_PREFIX}:d2:{profile_id}")],
            [InlineKeyboardButton(text="Отмена", callback_data=f"{_PREFIX}:p:{profile_id}")],
        ]
    )
    await _edit(callback, text, markup)
    await callback.answer()


@router.callback_query(F.data.startswith(f"{_PREFIX}:d2:"))
@inject
async def on_family_delete_confirm(
    callback: CallbackQuery,
    fam_session: FromDishka[AsyncSession],
    fam_panel: FromDishka[Remnawave],
    **data: Any,
) -> None:
    """Второе подтверждение: удаление необратимо, новый профиль — новая ссылка."""
    user = _user(data)
    profile_id = _profile_id(callback)
    if user is None or profile_id is None:
        await callback.answer()
        return
    p = _find(await _view(fam_session, fam_panel, user.id), profile_id)
    if p is None:
        await callback.answer("Профиль не найден", show_alert=True)
        return
    text = (
        f"Точно удалить «{_esc(p['label'])}»?\n\n"
        "Это нельзя отменить: у нового профиля будет другая ссылка, и её придётся "
        "переслать заново."
    )
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🗑 Удалить навсегда", callback_data=f"{_PREFIX}:dx:{profile_id}")],
            [InlineKeyboardButton(text="Отмена", callback_data=f"{_PREFIX}:p:{profile_id}")],
        ]
    )
    await _edit(callback, text, markup)
    await callback.answer()


@router.callback_query(F.data.startswith(f"{_PREFIX}:dx:"))
@inject
async def on_family_delete(
    callback: CallbackQuery,
    fam_session: FromDishka[AsyncSession],
    fam_panel: FromDishka[Remnawave],
    **data: Any,
) -> None:
    user = _user(data)
    profile_id = _profile_id(callback)
    if user is None or profile_id is None:
        await callback.answer()
        return
    result = await family.delete_profile(
        fam_session,
        getattr(fam_panel, "sdk", None),
        owner_id=user.id,
        profile_id=profile_id,
        actor="bot",
    )
    notice = {
        "deleted": "Профиль удалён",
        "pending": "Профиль удаляется — ссылка отключится в ближайшие минуты",
        "not_found": "Профиль не найден",
        "busy": BUSY_TEXT,
    }.get(result.get("result"), "Готово")
    text, markup = list_view(await _view(fam_session, fam_panel, user.id))
    await _edit(callback, text, markup)
    await callback.answer(notice, show_alert=result.get("result") != "deleted")


# ── новый профиль ───────────────────────────────────────────────────────────


@router.callback_query(F.data == f"{_PREFIX}:add")
@inject
async def on_family_add(
    callback: CallbackQuery,
    fam_session: FromDishka[AsyncSession],
    fam_panel: FromDishka[Remnawave],
    **data: Any,
) -> None:
    user = _user(data)
    if user is None or callback.message is None:
        await callback.answer()
        return
    view = await _view(fam_session, fam_panel, user.id)
    if not view.get("available"):
        await callback.answer(
            f"Добавить профиль нельзя: {family.reason_ru(view.get('reason'))}", show_alert=True
        )
        return
    await callback.message.answer(
        f"{PROMPT_MARK}\n\nКак назвать профиль? Например: Мама, Дочка, Планшет. "
        f"До {family.LABEL_MAX} символов — ответьте на это сообщение.",
        reply_markup=ForceReply(input_field_placeholder="Имя профиля"),
    )
    await callback.answer()


def _is_prompt_reply(message: Message) -> bool:
    reply = message.reply_to_message
    return bool(
        reply is not None
        and reply.from_user is not None
        and reply.from_user.is_bot
        and (reply.text or "").startswith(PROMPT_MARK)
    )


def request_id_for(chat_id: int, prompt_message_id: int, owner_id: int) -> uuid_lib.UUID:
    """Один ответ на одно приглашение — один профиль, сколько бы раз он ни пришёл."""
    return uuid_lib.uuid5(_REQUEST_NS, f"{chat_id}:{prompt_message_id}:{owner_id}")


@router.message(F.text, F.reply_to_message, F.func(_is_prompt_reply))
@inject
async def on_family_name(
    message: Message,
    fam_session: FromDishka[AsyncSession],
    fam_panel: FromDishka[Remnawave],
    fam_users: FromDishka[UserDao],
    fam_subs: FromDishka[SubscriptionDao],
    **data: Any,
) -> None:
    user = _user(data)
    if user is None or message.reply_to_message is None:
        return
    sdk = getattr(fam_panel, "sdk", None)
    if sdk is None:
        await message.answer("Сервер подписок недоступен — попробуйте позже.")
        return
    request_id = request_id_for(message.chat.id, message.reply_to_message.message_id, user.id)
    try:
        result = await family.create_profile(
            fam_session,
            sdk,
            owner_id=user.id,
            label=message.text,
            request_id=request_id,
            user_dao=fam_users,
            subscription_dao=fam_subs,
            actor="bot",
        )
    except Exception:  # noqa: BLE001
        await fam_session.rollback()
        logger.exception(f"family(bot): профиль не создан user_id={user.id}")
        await message.answer("Не удалось создать профиль — попробуйте ещё раз.")
        return
    outcome = result.get("result")
    if outcome == "created":
        view = await _view(fam_session, fam_panel, user.id)
        p = _find(view, int(result["profile_id"]))
        if p is not None:
            text, markup = card_view(p)
            await message.answer(
                "✅ Профиль готов. Откройте «Ссылка» и перешлите её участнику.\n\n" + text,
                reply_markup=markup,
                disable_web_page_preview=True,
            )
            return
        await message.answer("✅ Профиль готов — он в разделе «Семья».")
        return
    replies = {
        "pending": "Профиль создаётся — загляните в «Семью» через пару минут.",
        "failed": "Сервер подписок не создал профиль. Попробуйте позже.",
        "label_taken": "Профиль с таким именем уже есть — пришлите другое имя ответом на вопрос выше.",
        "bad_label": "Имя пустое — пришлите его текстом ответом на вопрос выше.",
        "conflict": "Не получилось — нажмите «Добавить профиль» ещё раз.",
        "busy": f"{BUSY_TEXT} — пришлите имя ещё раз ответом на вопрос выше.",
    }
    if outcome == "not_available":
        await message.answer(f"Добавить профиль нельзя: {family.reason_ru(result.get('reason'))}.")
        return
    await message.answer(replies.get(outcome, "Не получилось — попробуйте ещё раз."))
