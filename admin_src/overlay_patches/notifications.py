"""Уведомления в стиле кабинета и защита от «дребезга» узлов.

ЧТО ДЕЛАЕМ, три вещи:

  • сообщения владельцу и пользователям верстаются в оформлении кабинета
    (rich-HTML телеграма) вместо простого текста базы, с тумблером возврата к
    прежнему виду из админки;
  • пользовательские события дублируются в web-push (PWA/iOS) — телеграма и почты
    базы для человека, поставившего кабинет на телефон, недостаточно;
  • алерт «узел отвалился» выдерживает паузу: узел, моргнувший на секунды,
    поднимает и гасит тревогу быстрее, чем владелец успеет её прочитать, поэтому
    сообщение уходит, только если узел не вернулся за отведённое время.

ПОЧЕМУ ПОДКЛАСС, НО НЕ ПОДМЕНА КЛАССА. Перекрываются три метода и добавляются два
своих — набор точечных правок был бы тут ничем не лучше копии файла, поэтому пишем
наследника: так видно, что наше, а что базовое. Но НА МЕСТО базового класса он не
встаёт, а отдаёт ему свои методы.

Причина конкретная. База объявляет службу как
`provide(NotificationService, provides=AnyOf[Notifier, NotificationService])` —
то есть КЛАСС стоит и в источнике, и в списке предоставляемых типов. Подставь мы
своё имя, контейнер начал бы выдавать наследника под типом наследника, а
`notification_queue.py` запрашивает у контейнера именно БАЗОВЫЙ
`NotificationService` — и получил бы отказ «нет такой фабрики», то есть очередь
уведомлений встала бы целиком. Перенос методов оставляет тип нетронутым: снаружи
класс тот же самый, меняется только его поведение.
"""

from __future__ import annotations

import asyncio
import base64
import os
import string
import traceback
from dataclasses import asdict
from datetime import datetime, timezone  # [OVERLAY] заглушка Message для rich-ветки
from typing import Any, Callable, Optional, Sequence, Union
from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramMigrateToChat,
    TelegramNotFound,
    TelegramUnauthorizedError,
)
from aiogram.types import (
    BufferedInputFile,
    Chat,  # [OVERLAY] заглушка Message для rich-ветки
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    ReplyKeyboardMarkup,
)
from aiogram.utils.formatting import Text
from aiogram.utils.keyboard import InlineKeyboardBuilder
from loguru import logger
from src.application.common import EventPublisher, Notifier, TranslatorHub
from src.application.common.dao import SettingsDao, UserDao
from src.application.dto import (
    MediaDescriptorDto,
    MessagePayloadDto,
    NotificationTaskDto,
    SettingsDto,
    SystemNotificationRouteDto,
    TempUserDto,
    UserDto,
)
from src.application.events import ErrorEvent, NotificationErrorEvent, SystemEvent
from src.application.events.base import BaseEvent, UserEvent
from src.application.events.system import (
    BlacklistRegistrationAttemptEvent,
    BotUpdateEvent,
    NodeConnectionLostEvent,
    NodeConnectionRestoredEvent,
    PromocodeActivatedEvent,
    RemnashopWelcomeEvent,
    SubscriptionRevokedEvent,
    TorrentBlockerReportEvent,
    TrialActivatedEvent,
    UserDevicesUpdatedEvent,
    UserFirstConnectionEvent,
    UserPurchaseEvent,
    UserRegisteredEvent,
)
from src.application.events.user import (
    ReferralAttachedEvent,
    SubscriptionExpiredAgoEvent,
    SubscriptionExpiredEvent,
    SubscriptionExpiresEvent,
    SubscriptionLimitedEvent,
    TorrentBlockedEvent,
    UserNotConnectedEvent,
)
from src.core.config import AppConfig
from src.core.enums import Locale, Role
from src.core.types import AnyKeyboard, NotificationType
from src.infrastructure.services.event_bus import on_event
from src.infrastructure.services.notification_queue import NotificationWorker
from src.infrastructure.services.overlay_push import (  # [OVERLAY]
    admin_rich_enabled,
    push_admin_event_standalone,
    push_user_standalone,
)
from src.infrastructure.services.overlay_rich_notify import (  # [OVERLAY]
    build_rich_html,
    logo_src,
    rich_enabled,
    send_rich_message,
)
from src.infrastructure.services.overlay_startup_stats import (  # [OVERLAY]
    startup_stats_block,
)
from src.telegram.keyboards import (
    get_buy_keyboard,
    get_close_notification_button,
    get_contact_support_keyboard,
    get_remnashop_keyboard,
    get_remnashop_update_keyboard,
    get_renew_keyboard,
    get_user_keyboard,
)
from src.telegram.widgets import extract_tg_emoji

from src.infrastructure.services.notification import (
    NotificationService as BaseNotificationService,
)

from . import PatchTargetChanged, expect_source

# sha256 методов базы v0.8.2, которые мы перекрываем.
BASE_METHODS = {
    "NotificationService.on_user_event": "288045e0707c3c101372583cb48a2c825c167aa07585f0601797530ba7b4ef92",
    "NotificationService.on_system_event": "17c0d9b309384a8367a4459910325a1bd31de6614fbe69330c4a1bc1a4791a0a",
    "NotificationService._send_message": "e18747c6c6cfd21c2b37394bc44a9b2ebecfdf90e6b77b910453a52c387ea306",
}


def _push_title_body(raw: str) -> tuple[str, str]:
    """[OVERLAY] Из отрендеренного (HTML) текста уведомления делает title/body для
    web-push: срезает теги, первая непустая строка → заголовок, остальное → тело."""
    import re

    plain = re.sub(r"<[^>]+>", "", raw or "").replace("&amp;", "&").strip()
    parts = [p.strip() for p in plain.split("\n") if p.strip()]
    if not parts:
        return ("🔔 Уведомление", "")
    title = parts[0][:80]
    body = " ".join(parts[1:])[:180] if len(parts) > 1 else ""
    return (title, body)

_NODE_FLAP_STATE: dict[str, dict[str, Any]] = {}

_NODE_FLAP_LOCK = asyncio.Lock()

def _node_flap_enabled() -> bool:
    return (os.environ.get("NODE_FLAP_TOLERANCE") or "true").strip().lower() in (
        "1", "true", "yes", "on", "да",
    )

def _node_flap_grace_sec() -> int:
    """Сколько секунд ждать восстановления, прежде чем будить владельца."""
    try:
        return max(0, int(os.environ.get("NODE_FLAP_GRACE_SEC", "180")))
    except ValueError:
        return 180

_MIN_DELETE_AFTER = 30


class OverlayNotificationService(BaseNotificationService):
    """Уведомления кабинета. Всё, что не перекрыто, — поведение базы."""

    # [OVERLAY] «Не подключился»: помогаем подключиться, а не отправляем в поддержку.
    #
    # Панель умеет сама звать тех, у кого подписка есть, а подключений не было
    # (NOT_CONNECTED_USERS_NOTIFICATIONS_* в её .env) — бот это событие уже принимает.
    # Базовый текст спрашивает «не получилось?» и даёт одну кнопку в поддержку, то есть
    # перекладывает работу на человека и на нас. Замер по боевой базе: из 78 истёкших
    # пробных 37 не подключились НИ РАЗУ, а 15 успели завести устройство и всё равно не
    # пошли дальше. Им нужна не переписка, а кнопка «подключить».
    #
    # Текст и кнопки подменяем только для этого события: остальные идут как у базы.
    def _not_connected_payload(self, event: "UserNotConnectedEvent") -> "MessagePayloadDto":
        from aiogram.types import InlineKeyboardButton
        from aiogram.utils.keyboard import InlineKeyboardBuilder

        lang = str(getattr(getattr(event, "user", None), "language", "") or "ru").lower()
        base_url = (getattr(self.config, "web_cabinet_url", "") or "").strip().rstrip("/")
        if lang.startswith("en"):
            text = (
                "<b>🔌 Your access is ready — one step left</b>\n\n"
                "The subscription is active, but we haven't seen a single connection yet. "
                "It takes a couple of minutes: open your account, pick your device and tap "
                "«Connect» — the app sets itself up.\n\n"
                "If something goes wrong, write to support and we'll sort it out."
            )
            connect, support = "⚡ Connect", "💬 Support"
        else:
            text = (
                "<b>🔌 Доступ уже ждёт — осталось подключить</b>\n\n"
                "Подписка активна, но подключений пока не было. Это пара минут: откройте "
                "кабинет, выберите устройство и нажмите «Подключиться» — приложение "
                "настроится само.\n\n"
                "Если что-то не выходит — напишите в поддержку, поможем."
            )
            connect, support = "⚡ Подключить", "💬 Поддержка"

        builder = InlineKeyboardBuilder()
        if base_url:
            builder.row(InlineKeyboardButton(text=connect, url=f"{base_url}/devices"))
        support_url = getattr(event, "support_url", "") or ""
        if support_url:
            builder.row(InlineKeyboardButton(text=support, url=support_url))
        return MessagePayloadDto(
            i18n_key="raw-message",
            i18n_kwargs={"content": text},
            reply_markup=builder.as_markup() if (base_url or support_url) else None,
            disable_default_markup=True,
            # delete_after=None обязательно: дефолт DTO — 5 секунд (грабля рассылок).
            delete_after=None,
        )

    @on_event(UserEvent)
    async def on_user_event(self, event: UserEvent) -> None:
        logger.info(f"Received '{event.event_type}' event")

        settings: SettingsDto = await self.settings_dao.get()
        if not settings.notifications.is_enabled(event.notification_type):
            logger.info(f"Notification for '{event.notification_type}' is disabled, skipping")
            return

        if isinstance(event, UserNotConnectedEvent):
            payload = self._not_connected_payload(event)
        else:
            payload = event.as_payload()
            payload.reply_markup = self._resolve_keyboard(event)
        await self.notify_user(event.user, payload)

        # [OVERLAY] Пуш пригласившему: «по вашей ссылке подключился реферал».
        # event.user — пригласивший, event.name — имя приглашённого. Работает и
        # для кабинет-only юзеров без Telegram (для них push — единственный канал).
        if isinstance(event, ReferralAttachedEvent):
            try:
                _uid = getattr(event.user, "id", None)
                if _uid:
                    _name = getattr(event, "name", "") or "друг"
                    asyncio.create_task(
                        push_user_standalone(
                            _uid,
                            {
                                "title": "🎉 Новый реферал",
                                "body": f"{_name} подключился по вашей ссылке.",
                                "url": "/referral",
                                "tag": "referral-join",
                            },
                        )
                    )
            except Exception:  # noqa: BLE001
                pass

    @on_event(SystemEvent)
    async def on_system_event(self, event: SystemEvent) -> None:
        logger.info(f"Received '{event.event_type}' event")

        if isinstance(event, NotificationErrorEvent):
            return

        settings: SettingsDto = await self.settings_dao.get()
        if not settings.notifications.is_enabled(event.notification_type):
            logger.info(f"Notification for '{event.notification_type}' is disabled, skipping")
            return

        # [OVERLAY] Короткие обрывы связи с нодой не будят владельца — см. _node_flap_gate.
        if isinstance(event, (NodeConnectionLostEvent, NodeConnectionRestoredEvent)):
            if not await self._node_flap_gate(event):
                return

        payload = event.as_payload()
        payload.reply_markup = self._resolve_keyboard(event)
        await self.notify_system(payload, notification_type=event.notification_type)

    async def _send_message(
        self,
        user: Union[TempUserDto, UserDto],
        payload: MessagePayloadDto,
    ) -> Optional[Message]:
        if user.telegram_id is None:
            logger.debug(f"Skipping notification for web-only user {user.log}")
            return None

        render_kwargs = payload.i18n_kwargs.copy()

        if isinstance(user, UserDto) and payload.i18n_key == "raw-message":
            user_data = asdict(user)
            render_kwargs = {**user_data, **payload.i18n_kwargs}

        reply_markup = self._prepare_reply_markup(
            payload.reply_markup,
            payload.disable_default_markup,
            payload.delete_after,
            user.language,
            user.telegram_id,
        )

        text = self._get_translated_text(
            locale=user.language,
            i18n_key=payload.i18n_key,
            i18n_kwargs=render_kwargs,
        )

        # [OVERLAY] К стартовому уведомлению админам дописываем живые счётчики
        # (пользователи/подписки/тикеты/панель) — владелец просил сводку при
        # старте, как у «Бедолаги». Не собралось — просто нет блока.
        try:
            _role_for_stats = getattr(user, "role", None)
            if (
                payload.i18n_key == "event-bot.startup"
                and _role_for_stats is not None
                and _role_for_stats.includes(Role.ADMIN)
            ):
                stats_block = await startup_stats_block(self.config)
                if stats_block:
                    text = f"{text}\n\n{stats_block}"
        except Exception as exc:  # noqa: BLE001 — статистика не должна мешать доставке
            logger.warning(f"[OVERLAY] Блок статистики при старте пропущен: {exc}")

        # [OVERLAY] Админ-пуши: любое уведомление, уходящее админу в Telegram
        # (включая admin-broadcast через _process_task/_broadcast — регистрации,
        # оплаты, ошибки и т.п.), зеркалим в центр уведомлений админки, а на
        # телефон (web-push) — только если включён тумблер admin_push_enabled
        # (чтобы не задваивать с Telegram). Историю центр видит всегда.
        # Только админ-роли и реальные пользователи (есть .id); фоново — не влияет
        # на TG-отправку. url=/ (главная), а НЕ /admin: врезка ловит и личные/
        # рассылочные уведомления владельца. Целевые админ-алерты (новый тикет →
        # /admin/support) шлются отдельно со своим deep-link и сюда не относятся.
        try:
            _role = getattr(user, "role", None)
            _uid = getattr(user, "id", None)
            if _uid and _role is not None and _role.includes(Role.ADMIN):
                _pt, _pb = _push_title_body(text)
                # Пустые уведомления (текст отрендерился пустым → дефолтный заголовок
                # без тела) в центр не зеркалим — иначе появляются бессмысленные
                # «🔔 Уведомление» без содержимого.
                if _pb or _pt != "🔔 Уведомление":
                    asyncio.create_task(
                        push_admin_event_standalone(
                            _uid, {"title": _pt, "body": _pb, "url": "/", "tag": "admin"}
                        )
                    )
        except Exception:  # noqa: BLE001 — зеркало push не должно мешать TG
            pass

        # [OVERLAY] Rich-вид (Bot API 10.1) для админских текстовых уведомлений:
        # заголовок + таблица «показатель → значение» + футер вместо простыни.
        # Только админам и только если текст разобрался в пары ключ-значение;
        # всё остальное (и любой отказ Bot API) уходит обычным send_message ниже.
        #
        # Два условия, а не одно:
        #   rich_enabled()      — NOTIFY_RICH (аварийный выключатель в .env) плюс
        #                         латч «сервер Bot API rich не умеет»;
        #   admin_rich_enabled() — тумблер из админки кабинета (assets/notif_settings.json).
        # Тумблер живёт на стороне бота и читается на каждой отправке: кабинет
        # может стоять на другом сервере, а перезапуск бота ради галки недопустим.
        try:
            _role = getattr(user, "role", None)
            if (
                payload.is_text
                and not payload.media
                # Самоудаляющиеся служебные сообщения («Синхронизация выполнена»)
                # раньше исключались из rich: ветка возвращала None, удаление не
                # заводилось, и сообщение висело НАВСЕГДА. Причина устранена (id
                # возвращается, удаление планируется), поэтому исключение снято —
                # решение владельца: показывать в новом виде и убирать через 30 с.
                and _role is not None
                and _role.includes(Role.ADMIN)
                and rich_enabled()
                and admin_rich_enabled()
            ):
                # Импорт ленивый: appearance тянет веб-слой, боту он при старте не нужен.
                from src.web.endpoints.public.appearance import (  # noqa: PLC0415
                    load_branding,
                    resolve_brand_name,
                )

                # Подпись под уведомлением = ТО ЖЕ имя, что показывает кабинет.
                # resolve_brand_name() смотрит только переменную окружения и имя
                # бота и НЕ читает branding.json, куда пишет «Оформление»: из-за
                # этого в подписи стояло имя бота («BEGEMOT»), хотя сервис
                # называется иначе («Begemot VPN»). Порядок повторяет
                # appearance.get_appearance: сохранённое имя, иначе авто-резолв.
                _brand = ""
                try:
                    _brand = str((load_branding() or {}).get("brand_name") or "").strip()
                except Exception as exc:  # noqa: BLE001 — подпись не должна ронять доставку
                    logger.warning(f"[OVERLAY] Бренд для подписи не прочитан: {exc}")

                rich_html = build_rich_html(
                    text,
                    _brand or resolve_brand_name() or "RemnaShop",
                    # Логотип — ТОЛЬКО в стартовом уведомлении (решение владельца
                    # 6 августа: «фотка сервиса на каждое уведомление — так не
                    # надо, только на запуске»). В ленте оплат, регистраций и
                    # алертов картинка занимает пол-экрана и мешает читать.
                    logo=logo_src() if payload.i18n_key == "event-bot.startup" else "",
                )
                rich_id = (
                    await send_rich_message(
                        self.bot,
                        user.telegram_id,
                        rich_html,
                        reply_markup=reply_markup,
                        disable_notification=payload.disable_notification,
                    )
                    if rich_html
                    else None
                )
                if rich_id is not None:
                    # Возвращаем НАСТОЯЩИЙ Message, а не None: вызывающие проверяют
                    # результат как «доставлено» — админка отвечает
                    # {"delivered": …}, бот показывает «❌ Не удалось отправить
                    # сообщение», а импортер зовёт у него .delete(). С None
                    # rich-отправка выглядела как провал, хотя сообщение уходило
                    # (владелец, написав деву из админки, видел «не доставлено»).
                    # Отсюда и message_id из ответа Bot API, а не просто «да/нет».
                    sent = Message(
                        message_id=rich_id,
                        date=datetime.now(timezone.utc),
                        chat=Chat(id=user.telegram_id, type="private"),
                    ).as_(self.bot)
                    # Автоудаление. Сегодня сюда не доходят payload'ы с
                    # delete_after (исключены условием выше), но страховка стоит:
                    # снимут исключение — сообщение всё равно удалится, а не
                    # повиснет навсегда. Нижняя граница та же, что в обычной
                    # ветке (_MIN_DELETE_AFTER), иначе rich исчезал бы быстрее.
                    if rich_id and payload.delete_after:
                        asyncio.create_task(
                            self._schedule_message_deletion(
                                chat_id=user.telegram_id,
                                message_id=rich_id,
                                delay=max(payload.delete_after, _MIN_DELETE_AFTER),
                            )
                        )
                    return sent
        except Exception as exc:  # noqa: BLE001 — rich не должен ломать доставку
            logger.warning(f"[OVERLAY] rich-вид пропущен: {exc}")

        kwargs: dict[str, Any] = {
            "disable_notification": payload.disable_notification,
            "message_effect_id": payload.message_effect,
            "reply_markup": reply_markup,
        }

        try:
            if payload.is_text:
                message = await self.bot.send_message(
                    chat_id=user.telegram_id,
                    text=text,
                    disable_web_page_preview=True,
                    **kwargs,
                )
            elif payload.media:
                method = self._get_media_method(payload)
                media = self._build_media(payload.media)

                if not method:
                    logger.warning(f"Unknown media type for payload '{payload}'")
                    return None

                message = await method(user.telegram_id, media, caption=text, **kwargs)
            else:
                logger.error(f"Payload must contain text or media for user {user.log}")
                return None

            if message and payload.delete_after:
                # [OVERLAY] Нижняя граница жизни самоудаляющегося уведомления —
                # 30 секунд (решение владельца). База ставит 5 по умолчанию, и
                # этого не хватает даже прочитать фразу: человек видит вспышку и
                # не понимает, что произошло. Более долгие сроки, заданные
                # вызывающим кодом осознанно, не укорачиваем.
                asyncio.create_task(
                    self._schedule_message_deletion(
                        chat_id=user.telegram_id,
                        message_id=message.message_id,
                        delay=max(payload.delete_after, _MIN_DELETE_AFTER),
                    )
                )

            return message

        except TelegramForbiddenError:
            logger.warning(f"Bot was blocked by user {user.log}")
            return None
        except TelegramBadRequest as e:
            # [OVERLAY] Недостижимый получатель — не ошибка приложения, а штатная
            # ситуация: пользователь есть в Remnawave по telegram_id, но чата с
            # ботом нет (никогда не жал /start) или аккаунт удалён. Гасим тихо,
            # как TelegramForbiddenError, чтобы не спамить ERROR-трейсбеками и не
            # ронять обработчик события. Остальные BadRequest (битый HTML и т.п.)
            # пробрасываем дальше — это настоящие баги.
            unreachable = ("chat not found", "user not found", "peer_id_invalid")
            if any(marker in e.message.lower() for marker in unreachable):
                logger.warning(f"Skip notification to {user.log}: {e.message}")
                return None
            logger.exception(f"Failed to send notification to {user.log}: {e}")
            raise
        except Exception as e:
            logger.exception(f"Failed to send notification to {user.log}: {e}")
            raise

    async def _node_flap_gate(
        self,
        event: Union[NodeConnectionLostEvent, NodeConnectionRestoredEvent],
    ) -> bool:
        """True — отправлять сейчас, False — придержать/проглотить.

        «Потеряна» откладывается на NODE_FLAP_GRACE_SEC: если за это время придёт
        «восстановлена», обе новости выбрасываем. Если не придёт — отложенная задача
        отправит сообщение о потере, и тогда последующее «восстановлена» уйдёт как
        обычно (иначе владелец остался бы с ноды, которая «упала и не вернулась»).
        """
        if not _node_flap_enabled():
            return True

        key = f"{event.name}|{event.address}"

        async with _NODE_FLAP_LOCK:
            state = _NODE_FLAP_STATE.get(key)

            if isinstance(event, NodeConnectionLostEvent):
                if state is not None:
                    # Нода уже числится проблемной: либо ждём выдержку, либо о ней
                    # уже сообщено. Повторные «потеряна» — шум, глушим.
                    return False
                task = asyncio.create_task(self._node_flap_deferred_alert(key, event))
                _NODE_FLAP_STATE[key] = {"alerted": False, "task": task}
                logger.info(
                    f"[OVERLAY] Node '{event.name}' lost, holding alert for "
                    f"{_node_flap_grace_sec()}s"
                )
                return False

            # NodeConnectionRestoredEvent
            if state is None:
                # Про потерю не сообщали (перезапуск процесса, тумблер был выключен) —
                # молча пропускаем: «восстановлена» без «потеряна» только путает.
                return False

            _NODE_FLAP_STATE.pop(key, None)
            task = state.get("task")
            if task is not None and not task.done():
                task.cancel()

            if not state.get("alerted"):
                logger.info(f"[OVERLAY] Node '{event.name}' recovered within grace, alert dropped")
                return False

            return True

    async def _node_flap_deferred_alert(
        self,
        key: str,
        event: NodeConnectionLostEvent,
    ) -> None:
        """Ждёт выдержку и, если связь не вернулась, всё-таки сообщает о потере."""
        try:
            await asyncio.sleep(_node_flap_grace_sec())
        except asyncio.CancelledError:
            return

        async with _NODE_FLAP_LOCK:
            state = _NODE_FLAP_STATE.get(key)
            if state is None:
                return
            state["alerted"] = True
            state["task"] = None

        try:
            settings: SettingsDto = await self.settings_dao.get()
            if not settings.notifications.is_enabled(event.notification_type):
                return
            payload = event.as_payload()
            payload.reply_markup = self._resolve_keyboard(event)
            await self.notify_system(payload, notification_type=event.notification_type)
            logger.info(f"[OVERLAY] Node '{event.name}' still down after grace, alert sent")
        except Exception as exc:  # noqa: BLE001 — задача фоновая, падать молча нельзя
            logger.error(f"[OVERLAY] Deferred node alert failed for '{event.name}': {exc}")


def apply() -> str:
    import src.infrastructure.services.notification as target

    if not hasattr(target, "NotificationService"):
        raise PatchTargetChanged(
            "в src.infrastructure.services.notification нет NotificationService — "
            "база перестроила уведомления, оформление кабинета и web-push пропадут"
        )
    if getattr(target.NotificationService, "_overlay_wrapped", False):
        return "уже применены"

    for qualname, sha in BASE_METHODS.items():
        expect_source(target, qualname, sha, qualname)

    # Переносим методы на БАЗОВЫЙ класс, а не подставляем свой (см. docstring).
    # super() в них не используется — иначе перенос сломал бы разрешение предка.
    for name, value in vars(OverlayNotificationService).items():
        if name.startswith("__"):
            continue
        setattr(target.NotificationService, name, value)

    target.NotificationService._overlay_wrapped = True
    return "оформление кабинета, web-push, пауза перед алертом об узле"
