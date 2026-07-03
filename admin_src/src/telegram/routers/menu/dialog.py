from aiogram.enums import ButtonStyle
from aiogram_dialog import Dialog, StartMode
from aiogram_dialog.widgets.input import MessageInput
from aiogram_dialog.widgets.style import BaseStyle, Style
from aiogram_dialog.widgets.text import Const, Format
from dishka import FromDishka
from dishka.integrations.aiogram_dialog import inject
from magic_filter import F

from src.application.common import TranslatorRunner

from src.application.common.policy import Permission
from src.core.constants import INLINE_QUERY_INVITE, PAYMENT_PREFIX
from src.core.enums import BannerName
from src.telegram.keyboards import build_buttons_row
from src.telegram.routers.dashboard.handlers import on_smart_search
from src.telegram.states import Dashboard, MainMenu, Subscription
from src.telegram.utils import require_permission
from src.telegram.widgets import Banner, I18nFormat, IgnoreUpdate
from src.telegram.widgets.kbd import (
    Button,
    CopyText,
    ListGroup,
    Row,
    Start,
    SwitchInlineQueryChosenChatButton,
    SwitchTo,
    Url,
    WebApp,
)
from src.telegram.window import Window

from .getters import (
    device_confirm_delete_getter,
    devices_getter,
    invite_about_getter,
    invite_getter,
    menu_getter as _base_menu_getter,
)
from .menu_config import NAV_KEYS, load_menu_config
from .handlers import (
    on_device_delete_all_confirm,
    on_device_delete_confirm,
    on_device_delete_request,
    on_get_trial,
    on_invite,
    on_reissue_subscription_confirm,
    on_reset_referral_code,
    on_show_qr,
    on_text_button_click,
    on_withdraw_points,
    show_reason,
)

custom_buttons = (
    build_buttons_row(1, text_on_click=on_text_button_click),
    build_buttons_row(2, text_on_click=on_text_button_click),
    build_buttons_row(3, text_on_click=on_text_button_click),
)

# Определения 5 «кнопок доступа» (web ВКЛ): тип (webapp/url), текст (i18n-ключ
# или готовая строка), путь к кабинету и нужна ли активная подписка (connectable).
# Сами условия показа и порядок применяются в menu_getter ниже.
_ACCESS_DEFS: dict[str, dict] = {
    "cabinet_miniapp": {"kind": "webapp", "i18n": "btn-menu.web-cabinet", "path": "", "needs_connect": False},
    "cabinet_url":     {"kind": "url", "text": "🌐 Кабинет в браузере", "path": "", "needs_connect": False},
    "connect_miniapp": {"kind": "webapp", "i18n": "btn-menu.connect", "path": "/devices", "needs_connect": True},
    "connect_url":     {"kind": "url", "i18n": "btn-menu.connect-reserve", "path": "/devices", "needs_connect": True},
    "remna_sub":       {"kind": "url", "text": "📲 Подписка (резерв)", "sub": True, "needs_connect": True},
}


# ── Геттер меню с флагами и ПОРЯДКОМ кнопок ───────────────────────────────────
# Оборачиваем базовый menu_getter и:
#   • прокидываем булевы флаги состава (menu_cabinet_miniapp, …) — для обратной
#     совместимости с прочими виджетами;
#   • собираем menu_access_items — упорядоченный список кнопок доступа (web ВКЛ),
#     отфильтрованный по тем же условиям, что были в статичных when.
# Конфиг (assets/menu.json) читается на КАЖДЫЙ рендер → состав и порядок из
# админки применяются сразу, без перезапуска бота.
@inject
async def menu_getter(i18n: FromDishka[TranslatorRunner], **kwargs):
    data = await _base_menu_getter(**kwargs)
    cfg = load_menu_config()
    for key, value in cfg.items():
        if isinstance(value, bool):
            data[f"menu_{key}"] = value

    web_enabled = bool(data.get("web_enabled"))
    connectable = bool(data.get("connectable"))
    base_url = (data.get("web_cabinet_url") or "").rstrip("/")
    sub_url = data.get("subscription_url")

    texts_cfg = cfg.get("texts", {}) or {}
    colors_cfg = cfg.get("colors", {}) or {}

    # Базовые кнопки навигации: подпись (кастом → i18n-дефолт) и цвет — в data,
    # чтобы окно меню рендерило Format("{nav_*_text}") + динамический стиль.
    for navkey, i18nkey in NAV_KEYS.items():
        data[f"{navkey}_text"] = texts_cfg.get(navkey) or i18n.get(i18nkey)
        data[f"{navkey}_color"] = colors_cfg.get(navkey)

    items: list[dict] = []
    if web_enabled:
        for key in cfg.get("order", []):
            defn = _ACCESS_DEFS.get(key)
            if not defn or not cfg.get(key):
                continue
            if defn["needs_connect"] and not connectable:
                continue
            if defn.get("sub"):
                url = sub_url
            else:
                url = (base_url + defn.get("path", "")) if base_url else None
            if not url:
                continue
            # Текст: кастомный из админки → иначе i18n/готовая строка.
            default_text = i18n.get(defn["i18n"]) if "i18n" in defn else defn["text"]
            text = texts_cfg.get(key) or default_text
            # Цвет: кастомный из админки → иначе дефолт (webapp синяя, ссылка обычная).
            default_color = "primary" if defn["kind"] == "webapp" else None
            color = colors_cfg.get(key) or default_color
            items.append(
                {"id": key, "kind": defn["kind"], "text": text, "url": url, "color": color}
            )
    data["menu_access_items"] = items
    return data


# Динамический цвет кнопки: берём item[color] (задаётся в админке кабинета).
# Пусто/None → без стиля (дефолтная кнопка).
class _ItemColorStyle(BaseStyle):
    async def _render_style(self, data, manager):  # type: ignore[override]
        color = (data.get("item") or {}).get("color")
        return ButtonStyle(color) if color else None

    async def _render_emoji(self, data, manager):  # type: ignore[override]
        return None


# Цвет базовой кнопки навигации: читает data["nav_<key>_color"] (из getter'а).
class _NavColorStyle(BaseStyle):
    def __init__(self, navkey: str, when=None):
        super().__init__(when=when)
        self._navkey = navkey

    async def _render_style(self, data, manager):  # type: ignore[override]
        color = data.get(f"{self._navkey}_color")
        return ButtonStyle(color) if color else None

    async def _render_emoji(self, data, manager):  # type: ignore[override]
        return None


# Кнопки доступа (web ВКЛ): состав, видимость и ПОРЯДОК берутся из getter'а
# (menu_access_items — упорядоченный, уже отфильтрованный список из админки).
# Рендерим через ListGroup: на каждый элемент одна кнопка, тип по item[kind]
# (webapp = Mini App, url = обычная ссылка). Текст и цвет — из item (админка).
menu_access_list = ListGroup(
    Row(
        WebApp(
            text=Format("{item[text]}"),
            url=Format("{item[url]}"),
            id="acc_wa",
            when=F["item"]["kind"] == "webapp",
            style=_ItemColorStyle(),
        ),
        Url(
            text=Format("{item[text]}"),
            url=Format("{item[url]}"),
            id="acc_url",
            when=F["item"]["kind"] == "url",
            style=_ItemColorStyle(),
        ),
    ),
    id="menu_access",
    item_id_getter=lambda item: item["id"],
    items="menu_access_items",
)

# web ВЫКЛ — стандартное поведение базового бота (Mini App/сабка Remnawave).
# Эти кнопки в админке не настраиваются (порядок/состав — только для web ВКЛ).
base_connect_buttons = (
    WebApp(
        text=I18nFormat("btn-menu.connect"),
        url=Format("{connection_url}"),
        id="connect_miniapp_base",
        when=~F["web_enabled"] & F["is_mini_app"] & F["connectable"],
        style=Style(ButtonStyle.PRIMARY),
    ),
    Url(
        text=I18nFormat("btn-menu.connect-reserve"),
        url=Format("{subscription_url}"),
        id="connect_reserve_base",
        when=~F["web_enabled"] & F["connectable"],
    ),
)

menu = Window(
    Banner(BannerName.MENU),
    I18nFormat("msg-main-menu"),
    # Кнопки доступа (Личный кабинет Mini App/браузер, Подписка, Подключиться) —
    # состав и ПОРЯДОК редактируются в админке кабинета (страница «Меню»).
    menu_access_list,
    *base_connect_buttons,
    Row(
        Button(
            text=I18nFormat("btn-menu.connect-not-available"),
            id="not_available",
            on_click=show_reason,
        ),
        when=F["has_subscription"] & ~F["connectable"],
    ),
    Row(
        Button(
            text=I18nFormat("btn-menu.trial"),
            id="trial_free",
            on_click=on_get_trial,
            when=F["trial_available"] & F["trial_is_free"],
            style=Style(ButtonStyle.SUCCESS),
        ),
        Button(
            text=I18nFormat("btn-menu.trial-paid"),
            id="trial_paid",
            on_click=on_get_trial,
            when=F["trial_available"] & ~F["trial_is_free"],
            style=Style(ButtonStyle.SUCCESS),
        ),
    ),
    Row(
        SwitchTo(
            text=Format("{nav_devices_text}"),
            id="devices",
            state=MainMenu.DEVICES,
            when=F["has_device_limit"],
            style=_NavColorStyle("nav_devices"),
        ),
        Start(
            text=Format("{nav_subscription_text}"),
            id=f"{PAYMENT_PREFIX}subscription",
            state=Subscription.MAIN,
            style=_NavColorStyle("nav_subscription"),
        ),
    ),
    Row(
        Button(
            text=Format("{nav_invite_text}"),
            id="invite",
            on_click=on_invite,
            when=F["referral_enabled"],
            style=_NavColorStyle("nav_invite"),
        ),
        SwitchInlineQueryChosenChatButton(
            text=Format("{nav_invite_text}"),
            query=Format(INLINE_QUERY_INVITE),
            allow_user_chats=True,
            allow_group_chats=True,
            allow_channel_chats=True,
            id="send",
            when=~F["referral_enabled"],
        ),
        Url(
            text=Format("{nav_support_text}"),
            id="support",
            url=Format("{support_url}"),
            style=_NavColorStyle("nav_support"),
        ),
    ),
    *custom_buttons,
    Row(
        Start(
            text=Format("{nav_dashboard_text}"),
            id="dashboard",
            state=Dashboard.MAIN,
            mode=StartMode.RESET_STACK,
            when=require_permission(Permission.VIEW_DASHBOARD),
            style=_NavColorStyle("nav_dashboard"),
        ),
    ),
    MessageInput(func=on_smart_search),
    IgnoreUpdate(),
    state=MainMenu.MAIN,
    getter=menu_getter,
)

devices = Window(
    Banner(BannerName.DEVICES),
    I18nFormat("msg-menu-devices"),
    Row(
        Button(
            text=I18nFormat("btn-common.devices-empty"),
            id="devices_empty",
            when=~F["has_devices"],
        ),
    ),
    ListGroup(
        Row(
            Button(
                text=Format("{item[label]}"),
                id="device_item",
                on_click=on_device_delete_request,
                when=F["data"]["device_single_enabled"],
            ),
            Button(
                text=Format("{item[label]}"),
                id="device_item_display",
                when=~F["data"]["device_single_enabled"],
            ),
        ),
        id="devices_list",
        item_id_getter=lambda item: item["index"],
        items="devices",
        when=F["has_devices"],
    ),
    Row(
        Start(
            text=I18nFormat("btn-devices.delete-all"),
            id="delete_all",
            state=MainMenu.DEVICE_CONFIRM_DELETE_ALL,
            when=F["has_devices"] & F["device_all_enabled"],
            style=Style(ButtonStyle.DANGER),
        ),
    ),
    Row(
        Start(
            text=I18nFormat("btn-devices.reissue"),
            id="reissue",
            state=MainMenu.DEVICE_CONFIRM_REISSUE,
            style=Style(ButtonStyle.PRIMARY),
            when=F["link_reset_enabled"],
        ),
    ),
    Row(
        SwitchTo(
            text=I18nFormat("btn-back.general"),
            id="back",
            state=MainMenu.MAIN,
        ),
    ),
    IgnoreUpdate(),
    state=MainMenu.DEVICES,
    getter=devices_getter,
)

device_confirm_delete = Window(
    Banner(BannerName.MENU),
    I18nFormat("msg-menu-devices-confirm-delete"),
    Row(
        Button(
            text=I18nFormat("btn-devices.confirm-delete"),
            id="confirm_delete",
            on_click=on_device_delete_confirm,
            style=Style(ButtonStyle.DANGER),
        ),
        SwitchTo(
            text=I18nFormat("btn-common.cancel"),
            id="cancel",
            state=MainMenu.DEVICES,
        ),
    ),
    IgnoreUpdate(),
    state=MainMenu.DEVICE_CONFIRM_DELETE,
    getter=device_confirm_delete_getter,
)

device_confirm_delete_all = Window(
    Banner(BannerName.MENU),
    I18nFormat("msg-menu-devices-confirm-delete-all"),
    Row(
        Button(
            text=I18nFormat("btn-devices.confirm-delete"),
            id="confirm_delete_all",
            on_click=on_device_delete_all_confirm,
            style=Style(ButtonStyle.DANGER),
        ),
        SwitchTo(
            text=I18nFormat("btn-common.cancel"),
            id="cancel",
            state=MainMenu.DEVICES,
        ),
    ),
    IgnoreUpdate(),
    state=MainMenu.DEVICE_CONFIRM_DELETE_ALL,
    getter=device_confirm_delete_getter,
)

invite = Window(
    Banner(BannerName.REFERRAL),
    I18nFormat("msg-menu-invite"),
    Row(
        SwitchTo(
            text=I18nFormat("btn-invite.about"),
            id="about",
            state=MainMenu.INVITE_ABOUT,
        ),
    ),
    Row(
        CopyText(
            text=I18nFormat("btn-invite.copy"),
            copy_text=Format("{referral_url}"),
        ),
    ),
    Row(
        Button(
            text=I18nFormat("btn-invite.qr"),
            id="qr",
            on_click=on_show_qr,
        ),
        SwitchInlineQueryChosenChatButton(
            text=I18nFormat("btn-invite.send"),
            query=Format(INLINE_QUERY_INVITE),
            allow_user_chats=True,
            allow_group_chats=True,
            allow_channel_chats=True,
            id="send",
        ),
    ),
    Row(
        Button(
            text=I18nFormat("btn-invite.withdraw-points"),
            id="withdraw_points",
            on_click=on_withdraw_points,
            when=~F["has_points"],
        ),
        Url(
            text=I18nFormat("btn-invite.withdraw-points"),
            id="withdraw_points",
            url=Format("{withdraw}"),
            when=F["has_points"],
        ),
        when=F["is_points_reward"],
    ),
    Row(
        Button(
            text=I18nFormat("btn-invite.reset-referral"),
            id="reset_referral",
            on_click=on_reset_referral_code,
            when=F["referral_reset_enabled"],
        ),
    ),
    Row(
        SwitchTo(
            text=I18nFormat("btn-back.general"),
            id="back",
            state=MainMenu.MAIN,
        ),
    ),
    IgnoreUpdate(),
    state=MainMenu.INVITE,
    getter=invite_getter,
)

invite_about = Window(
    Banner(BannerName.REFERRAL),
    I18nFormat("msg-menu-invite-about"),
    Row(
        SwitchTo(
            text=I18nFormat("btn-back.general"),
            id="back",
            state=MainMenu.INVITE,
        ),
    ),
    IgnoreUpdate(),
    state=MainMenu.INVITE_ABOUT,
    getter=invite_about_getter,
)


device_confirm_reissue = Window(
    Banner(BannerName.MENU),
    I18nFormat("msg-menu-devices-confirm-reissue"),
    Row(
        Button(
            text=I18nFormat("btn-devices.confirm-reissue"),
            id="confirm_reissue",
            on_click=on_reissue_subscription_confirm,
            style=Style(ButtonStyle.DANGER),
        ),
        SwitchTo(
            text=I18nFormat("btn-devices.cancel-reissue"),
            id="cancel_reissue",
            state=MainMenu.DEVICES,
        ),
    ),
    IgnoreUpdate(),
    state=MainMenu.DEVICE_CONFIRM_REISSUE,
    getter=device_confirm_delete_getter,
)

router = Dialog(
    menu,
    devices,
    device_confirm_delete,
    device_confirm_delete_all,
    device_confirm_reissue,
    invite,
    invite_about,
)
