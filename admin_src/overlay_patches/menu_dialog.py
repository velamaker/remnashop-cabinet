"""Главное меню бота: кнопки доступа в кабинет и подарки.

ЧТО ДЕЛАЕМ. Базовое меню про кабинет ничего не знает. Мы добавляем в него до пяти
«кнопок доступа» (мини-приложение кабинета, кабинет в браузере, подключение
устройств, резервная ссылка подписки, своя мини-аппа), кнопку «Подарить подписку»
и оформление пунктов — какие показывать, в каком порядке и какими подписями,
задаётся в админке.

ПОЧЕМУ ЦЕЛИКОМ СВОЙ МОДУЛЬ. Здесь переписаны окна и сборка самого диалога, а не
отдельные функции: точечные правки означали бы подмену почти каждого виджета.
Зато подключение диалога — одна строка в базе (`menu.dialog.router` в списке
роутеров), и объект она берёт ПО ИМЕНИ из модуля. Значит достаточно построить свой
диалог здесь и подставить его на место базового до того, как список соберётся.

Данные окна по-прежнему готовит базовый `menu_getter` из `menu/getters.py` — мы
его вызываем, а не переписываем, и дополняем результат своими полями.
"""

from urllib.parse import urlsplit

from aiogram.enums import ButtonStyle
from loguru import logger
from aiogram_dialog import Dialog, StartMode
from aiogram_dialog.widgets.input import MessageInput
from aiogram_dialog.widgets.style import BaseStyle, Style
from aiogram_dialog.widgets.text import Const, Format
from dishka import FromDishka
from dishka.integrations.aiogram_dialog import inject
from magic_filter import F

from sqlalchemy.ext.asyncio import AsyncSession

from src.application.common import Remnawave, TranslatorRunner

from src.application.common.policy import Permission
from src.core.config import AppConfig
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

from src.telegram.routers.menu.getters import (
    device_confirm_delete_getter,
    devices_getter,
    invite_about_getter,
    invite_getter,
    menu_getter as _base_menu_getter,
)
from src.telegram.routers.menu.menu_config import DEFAULT_TEXTS, NAV_KEYS, load_menu_config, validate_custom_url
from src.telegram.routers.menu.handlers import (
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

# OVERLAY: открытие подарков из главного меню. Импорт защищённый — если фичу
# вырезали, меню обязано работать как раньше (кнопка просто не появится).
try:
    from src.telegram.routers.overlay_gift import open_gift_from_menu

    _GIFT_AVAILABLE = True
except Exception as _gift_exc:  # noqa: BLE001
    _GIFT_AVAILABLE = False
    # Без этой строки кнопка «Подарить подписку» просто не появлялась в меню, и
    # понять почему было нечем: в админке галочка стоит, а в боте кнопки нет.
    logger.warning(f"Overlay gift menu button disabled: {_gift_exc}")

    async def open_gift_from_menu(callback, widget, dialog_manager) -> None:  # type: ignore[misc]
        await callback.answer()


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
    # OVERLAY: своя мини-аппа (страница подписки, чужое мини-приложение). Ссылку
    # берём не из кабинета, а из настройки custom_url — см. menu_config.
    "custom_miniapp":  {"kind": "webapp", "text": "🚀 Мини-приложение", "custom": True, "needs_connect": False},
}


def _safe_custom_url(value) -> str | None:
    """Адрес своей мини-аппы, если он пригоден для кнопки. Иначе None."""
    try:
        url = validate_custom_url(value)
    except ValueError:
        return None
    return url or None


def _is_telegram_link(url: str | None) -> bool:
    host = (urlsplit(url or "").hostname or "").lower()
    return host in ("t.me", "telegram.me", "telegram.dog")


# ── Геттер меню с флагами и ПОРЯДКОМ кнопок ───────────────────────────────────
# Оборачиваем базовый menu_getter и:
#   • прокидываем булевы флаги состава (menu_cabinet_miniapp, …) — для обратной
#     совместимости с прочими виджетами;
#   • собираем menu_access_items — упорядоченный список кнопок доступа (web ВКЛ),
#     отфильтрованный по тем же условиям, что были в статичных when.
# Конфиг (assets/menu.json) читается на КАЖДЫЙ рендер → состав и порядок из
# админки применяются сразу, без перезапуска бота.
@inject
async def menu_getter(
    # ВСЕ имена НАРОЧНО с префиксом: aiogram_dialog зовёт геттер окна со своими
    # kwargs, и одноимённый параметр под @inject даёт «got multiple values for
    # keyword argument» — на этом 18.09 легло окно «Устройства» у всех. `i18n`
    # переименован вместе с остальными: он годами работал, но полагаться на «в
    # kwargs его вроде бы нет» в главном окне бота — это ставка без выигрыша.
    # Правило и его цена — в tests/test_menu_getter_kwargs.py.
    _extra_i18n: FromDishka[TranslatorRunner],
    _extra_session: FromDishka[AsyncSession],
    _extra_panel: FromDishka[Remnawave],
    **kwargs,
):
    i18n = _extra_i18n
    data = await _base_menu_getter(**kwargs)
    await _fix_reset_time(data, _extra_session, _extra_panel, kwargs)
    cfg = load_menu_config()
    for key, value in cfg.items():
        if isinstance(value, bool):
            data[f"menu_{key}"] = value

    web_enabled = bool(data.get("web_enabled"))
    connectable = bool(data.get("connectable"))
    base_url = (data.get("web_cabinet_url") or "").rstrip("/")
    # Кнопка «Подписка (резерв)» — намеренный fallback: отдаёт РЕАЛЬНУЮ ссылку подписки,
    # чтобы юзер мог импортировать её вручную, когда мини-апп/крипто-ссылка не сработали.
    # Поэтому здесь алиас НЕ применяем (крипто-ссылки — фича кабинета, не резерва).
    sub_url = data.get("subscription_url")

    texts_cfg = cfg.get("texts", {}) or {}
    colors_cfg = cfg.get("colors", {}) or {}

    # Базовые кнопки навигации: подпись (кастом → i18n-дефолт) и цвет — в data,
    # чтобы окно меню рендерило Format("{nav_*_text}") + динамический стиль.
    for navkey, i18nkey in NAV_KEYS.items():
        data[f"{navkey}_text"] = texts_cfg.get(navkey) or i18n.get(i18nkey)
        data[f"{navkey}_color"] = colors_cfg.get(navkey)

    # OVERLAY: кнопка «Подарить подписку». i18n-ключа в ftl нет намеренно — текст
    # берём из админки, иначе из дефолта (Fluent на неизвестный ключ вернул бы сам ключ).
    data["menu_gift"] = bool(data.get("menu_gift")) and _GIFT_AVAILABLE
    data["gift_text"] = texts_cfg.get("gift") or DEFAULT_TEXTS["gift"]
    data["gift_color"] = colors_cfg.get("gift")

    items: list[dict] = []
    for key in cfg.get("order", []):
            defn = _ACCESS_DEFS.get(key)
            if not defn or not cfg.get(key):
                continue
            # Все кнопки, кроме своей мини-аппы, ведут в кабинет — без него их нет.
            if not defn.get("custom") and not web_enabled:
                continue
            if defn["needs_connect"] and not connectable:
                continue
            if defn.get("custom"):
                # Своя мини-аппа: адрес из админки (или из BOT_MINI_APP). Проверяем
                # ЕЩЁ РАЗ здесь: Telegram отвергает сообщение целиком, если хоть
                # одна кнопка с плохим адресом, — то есть кривая ссылка убрала бы
                # у людей всё меню, а не одну кнопку.
                url = _safe_custom_url(cfg.get("custom_url"))
            elif defn.get("sub"):
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
            kind = defn["kind"]
            if defn.get("custom") and _is_telegram_link(url):
                # Ссылка на t.me (мини-аппа чужого бота, канал, приглашение):
                # web_app такие адреса не принимает — отдаём обычной ссылкой,
                # Telegram сам откроет мини-аппу внутри себя.
                kind = "url"
            items.append(
                {"id": key, "kind": kind, "text": text, "url": url, "color": color}
            )
    data["menu_access_items"] = items
    return data


async def _fix_reset_time(data, session, remnawave, kwargs) -> None:
    """Дата обновления трафика в карточке подписки — по формуле ПАНЕЛИ.

    ТРЕТЬЕ И ПОСЛЕДНЕЕ МЕСТО с этой датой (первые два чинит
    overlay_patches/traffic_reset_date.py). Базовый геттер зовёт
    `get_traffic_reset_delta(strategy, subscription.created_at)`, а там неверны и
    часы, и якорь: панель считает по `created_at` СВОЕГО пользователя, наша строка
    подписки пересоздаётся при смене тарифа. Чинить обязательно вместе с остальными:
    расхождение «кабинет говорит 7-е, бот 21-е» человек читает как обман.

    Тело базового геттера не трогаем — перезаписываем ОДНО готовое поле. Любая
    ошибка означает «оставили как было»: меню открывают, когда что-то не работает,
    и падать в этот момент нельзя.
    """
    if not data.get("has_subscription"):
        return
    try:
        from src.core.utils.i18n_helpers import i18n_format_expire_time
        from src.infrastructure.services import overlay_extra_traffic as extra

        user = kwargs.get("user") or kwargs.get("event_from_user")
        user_id = getattr(user, "id", None)
        if user_id is None:
            return
        now = extra.now_utc()
        moment = await extra.reset_moment_for_user(session, remnawave, int(user_id), now)
        if moment is None:
            return
        data["reset_time"] = i18n_format_expire_time(moment - now)
    except Exception as exc:  # noqa: BLE001 — приблизительная дата лучше пустого меню
        logger.warning(f"extra_traffic: дату обновления трафика в меню не поправил: {exc}")


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
    # OVERLAY: «Подарить подписку» — тумблер/текст/цвет в админке (ключ gift).
    Row(
        Button(
            text=Format("{gift_text}"),
            id="gift_open",
            on_click=open_gift_from_menu,
            when=F["menu_gift"],
            style=_NavColorStyle("gift"),
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

# OVERLAY: кнопка «Докупить устройство» в окне «Устройства».
#
# Оплаты в боте намеренно НЕТ (решение владельца): одна кнопка-ссылка в кабинет вместо
# второго денежного пути со звёздами и отдельным диалогом ради редкой операции. Мини-апп
# входит по Telegram сам, платить человек будет там же, где уже платит за подписку.
#
# Любая ошибка — просто нет кнопки: окно «Устройства» люди открывают, когда у них что-то
# не подключается, и падать в этот момент нельзя.
EXTRA_DEVICE_BUY_TEXT = "➕ Место под устройство на 30 дней"
EXTRA_DEVICE_EXTEND_TEXT = "🧩 Продлить место под устройство"


@inject
async def devices_getter_overlay(
    # Имена НАРОЧНО не `session`/`config`: aiogram_dialog зовёт геттер окна со своими
    # kwargs, и там уже есть `config` — одноимённый параметр под @inject дал бы
    # «got multiple values for keyword argument 'config'» и окно «Устройства»
    # падало бы у всех (так и случилось на бою 18.09).
    _extra_session: FromDishka[AsyncSession],
    _extra_config: FromDishka[AppConfig],
    **kwargs,
):
    session, config = _extra_session, _extra_config
    data = await devices_getter(**kwargs)
    data.setdefault("extra_device_button", False)
    data.setdefault("extra_device_text", EXTRA_DEVICE_BUY_TEXT)
    data.setdefault("extra_device_url", "")
    try:
        from src.infrastructure.services import overlay_extra_device as extra

        base_url = (getattr(config, "web_cabinet_url", "") or "").strip().rstrip("/")
        cfg = extra.load_config()
        if not base_url or not extra.effective_enabled(cfg):
            return data
        user = kwargs.get("user") or kwargs.get("event_from_user")
        user_id = getattr(user, "id", None)
        if user_id is None:
            return data
        st = await extra.lock_state(session, int(user_id), lock=False)
        now = extra.now_utc()
        can_buy = extra.eligibility(st, cfg, now, "new") is None
        # Кнопку «Докупить» показываем только при ЗАПОЛНЕННОМ лимите: пока места есть,
        # предлагать купить ещё одно — навязывание.
        limit = int(data.get("max_count") or st.device_limit or 0)
        full = limit > 0 and int(data.get("current_count") or 0) >= limit
        extendable = next(
            (s for s in st.slots if extra.eligibility(st, cfg, now, "extend", s.id) is None), None
        )
        if extendable is not None:
            data["extra_device_button"] = True
            data["extra_device_text"] = EXTRA_DEVICE_EXTEND_TEXT
        elif can_buy and full:
            data["extra_device_button"] = True
            data["extra_device_text"] = EXTRA_DEVICE_BUY_TEXT
        if data["extra_device_button"]:
            data["extra_device_url"] = f"{base_url}/devices"
    except Exception as exc:  # noqa: BLE001 — кнопки нет, окно живо
        logger.warning(f"extra_device: кнопку в «Устройствах» не показал: {exc}")
    return data


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
        WebApp(
            text=Format("{extra_device_text}"),
            url=Format("{extra_device_url}"),
            id="extra_device",
            when=F["extra_device_button"],
            style=Style(ButtonStyle.PRIMARY),
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
    getter=devices_getter_overlay,
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


def apply() -> str:
    """Подставить наш диалог на место базового.

    Патчим сам модуль базы: `src/telegram/routers/__init__.py` читает
    `menu.dialog.router` при сборке списка роутеров, то есть уже после нас.
    """
    from . import PatchTargetChanged

    import src.telegram.routers.menu.dialog as target

    if not hasattr(target, "router"):
        raise PatchTargetChanged(
            "в menu/dialog.py нет объекта router — база перестроила главное меню, "
            "кнопки кабинета и подарков из него пропадут"
        )
    if getattr(target, "_overlay_wrapped", False):
        return "уже подставлен"

    target.router = router
    target._overlay_wrapped = True
    return "кнопки кабинета и подарков в главном меню"
