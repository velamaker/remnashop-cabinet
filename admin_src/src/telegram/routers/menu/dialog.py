from aiogram.enums import ButtonStyle
from aiogram_dialog import Dialog, StartMode
from aiogram_dialog.widgets.input import MessageInput
from aiogram_dialog.widgets.style import Style
from aiogram_dialog.widgets.text import Format
from magic_filter import F

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
    menu_getter,
)
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

# Кнопки «Подключиться». Если включён наш web-кабинет (web_enabled) — ведём в
# кабинет на раздел устройств ({web_cabinet_url}/devices), а НЕ на стандартную
# саб-страницу Remnawave. Если web выключен — прежнее поведение (саб Remnawave).
# Заменяет базовый connect_buttons из src.telegram.keyboards.
cabinet_connect_buttons = (
    # web ВКЛ: Mini App → кабинет /devices
    WebApp(
        text=I18nFormat("btn-menu.connect"),
        url=Format("{web_cabinet_url}/devices"),
        id="connect_cabinet_miniapp",
        when=F["web_enabled"] & F["is_mini_app"] & F["connectable"],
        style=Style(ButtonStyle.PRIMARY),
    ),
    # web ВКЛ + резерв: открыть кабинет /devices в браузере (без Mini App).
    # Показываем всегда рядом с Mini App-кнопкой — на случай, если Mini App
    # не открывается (старый клиент, блокировки), чтобы был прямой переход.
    Url(
        text=I18nFormat("btn-menu.connect-reserve"),
        url=Format("{web_cabinet_url}/devices"),
        id="connect_cabinet_reserve",
        when=F["web_enabled"] & F["is_mini_app"] & F["connectable"],
    ),
    # web ВКЛ, не Mini App: кабинет /devices в браузере (основная)
    Url(
        text=I18nFormat("btn-menu.connect"),
        url=Format("{web_cabinet_url}/devices"),
        id="connect_cabinet_url",
        when=F["web_enabled"] & ~F["is_mini_app"] & F["connectable"],
        style=Style(ButtonStyle.PRIMARY),
    ),
    # web ВЫКЛ: прежнее поведение — стандартный саб Remnawave
    WebApp(
        text=I18nFormat("btn-menu.connect"),
        url=Format("{connection_url}"),
        id="connect_miniapp",
        when=~F["web_enabled"] & F["is_mini_app"] & F["connectable"],
        style=Style(ButtonStyle.PRIMARY),
    ),
    Url(
        text=I18nFormat("btn-menu.connect-reserve"),
        url=Format("{subscription_url}"),
        id="connect_reserve",
        when=~F["web_enabled"] & F["is_mini_app_reserve"] & F["connectable"],
    ),
    Url(
        text=I18nFormat("btn-menu.connect"),
        url=Format("{connection_url}"),
        id="connect_sub_page",
        when=~F["web_enabled"] & ~F["is_mini_app"] & F["connectable"],
        style=Style(ButtonStyle.PRIMARY),
    ),
)

menu = Window(
    Banner(BannerName.MENU),
    I18nFormat("msg-main-menu"),
    # Кнопки «Подключиться» убраны для разгрузки меню — вход в кабинет один,
    # через Mini App (см. «Личный кабинет» ниже). cabinet_connect_buttons оставлен
    # в коде на случай возврата.
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
            text=I18nFormat("btn-menu.devices"),
            id="devices",
            state=MainMenu.DEVICES,
            when=F["has_device_limit"],
        ),
        Start(
            text=I18nFormat("btn-menu.subscription"),
            id=f"{PAYMENT_PREFIX}subscription",
            state=Subscription.MAIN,
        ),
    ),
    Row(
        Button(
            text=I18nFormat("btn-menu.invite"),
            id="invite",
            on_click=on_invite,
            when=F["referral_enabled"],
        ),
        SwitchInlineQueryChosenChatButton(
            text=I18nFormat("btn-menu.invite"),
            query=Format(INLINE_QUERY_INVITE),
            allow_user_chats=True,
            allow_group_chats=True,
            allow_channel_chats=True,
            id="send",
            when=~F["referral_enabled"],
        ),
        Url(
            text=I18nFormat("btn-menu.support"),
            id="support",
            url=Format("{support_url}"),
        ),
    ),
    # Единственный вход в кабинет — Mini App (на всякий случай).
    Row(
        WebApp(
            text=I18nFormat("btn-menu.web-cabinet"),
            url=Format("{web_cabinet_url}"),
        ),
        when=F["web_enabled"],
    ),
    *custom_buttons,
    Row(
        Start(
            text=I18nFormat("btn-menu.dashboard"),
            id="dashboard",
            state=Dashboard.MAIN,
            mode=StartMode.RESET_STACK,
            when=require_permission(Permission.VIEW_DASHBOARD),
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
