"""Тревога владельцу, когда фоновая задача падает целиком.

ЗАЧЕМ. Наши кроны написаны «best-effort»: ошибка по одному человеку не должна
ронять проход по остальным, и это правильно. Но тот же приём заодно гасил и
падение ВСЕГО прогона: панель не ответила, запрос к базе сломался, резолверы
ссылок легли — крон писал warning в лог воркера и молчал. Лог воркера никто не
читает, и крон мог не работать неделями: автопродление не продлевает, резерв не
выдаётся, напоминания не уходят. Так было с картой t_id→uuid на панели 3.x:
снимок устройств и «новое устройство» стояли молча, пока поломку не нашли руками.

А там, где исключение улетало наружу, выходило наоборот: taskiq-мидлварь базы
шлёт отчёт об ошибке на КАЖДЫЙ прогон — почасовой крон будил владельца каждый час.

ЧТО ДЕЛАЕТ. Декоратор `cron_guard(name, title)` ловит падение прогона и сообщает
владельцу только о ПЕРЕХОДАХ:
  * работало → упало: одно сообщение «не выполнилась» с причиной;
  * упало → снова работает: одно сообщение «снова работает» и сколько лежала;
  * падает дальше: молчим, только лог.

Что считать падением, крон решает сам:
  * исключение, вылетевшее из тела задачи, — падение (декоратор его гасит и пишет
    в лог с трейсбеком: повторять его отчётом базы каждый час больше незачем);
  * крон, который сам поймал ошибку ВСЕГО прохода (панель недоступна, прогон не
    удался) и вышел, зовёт `cron_failed(причина)` — иначе прогон сочли бы удачным;
  * крон, который вышел, ничего не сделав по настройке (выключен, не тот час),
    зовёт `cron_skipped()`: такой прогон не говорит ни о поломке, ни о починке —
    иначе ежечасная проверка «сейчас не 9 утра» объявляла бы починку сводки,
    которая упала час назад.
Ошибка по одному человеку или записи, пойманная внутри цикла, — не падение прогона:
её декоратор не видит и видеть не должен.

«ТАБЛИЦЫ ЕЩЁ НЕТ» — НЕ ПАДЕНИЕ. Миграции катает контейнер бота, а воркер при
выкатке может проснуться раньше. Такой прогон считаем пропущенным: иначе каждый
выпуск с новой таблицей приносил бы владельцу пару «упало / снова работает».
Если миграция не прошла вовсе, не стартует сам бот — это видно и без нас.

ГДЕ СОСТОЯНИЕ. Файл assets/cron_guard_state.json: кроны исполняет только воркер,
бот сюда не пишет, поэтому общего Redis-дедупа, как у traffic_alert, не нужно.
Но процессов воркера два (taskiq по умолчанию), и почасовые кроны стартуют в одну
минуту — поэтому чтение-правка-запись идёт под flock, а запись атомарная (временный
файл + os.replace): читатель видит либо старый файл, либо новый, но не половину.

КАК ШЛЁМ. Напрямую ботом владельцу (BOT_OWNER_ID), как app_links и update_notifier,
а не очередью уведомлений базы: очередь не говорит, дошло ли сообщение, а нам это
важно — не дошедшее «упало» надо повторить на следующем падении, а не считать
сказанным. Прокси бота (BOT_PROXY_URL) учитываем: без него на серверах, где
Telegram закрыт, тревога не уходила бы никогда.

САМ НЕ РОНЯЕТ. Не удалось отправить, прочитать или записать состояние — пишем в
лог и отпускаем крон. Отключить тревоги: env CRON_GUARD_ALERTS=false.
"""

from __future__ import annotations

import contextlib
import fcntl
import functools
import html
import json
import os
import tempfile
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterator, Optional, TypeVar

from loguru import logger

ASSETS_DIR = Path(os.environ.get("APP_ASSETS_DIR", "/opt/remnashop/assets"))
STATE_PATH = ASSETS_DIR / "cron_guard_state.json"
LOCK_PATH = ASSETS_DIR / ".cron_guard_state.lock"

# Сколько причины показываем в сообщении: трейсбек лежит в логе, в Telegram — суть.
_REASON_LIMIT = 300
# SQLSTATE «relation does not exist».
_UNDEFINED_TABLE = "42P01"

F = TypeVar("F", bound=Callable[..., Awaitable[Any]])
Sender = Callable[[str], Awaitable[Any]]


def _enabled() -> bool:
    return (os.environ.get("CRON_GUARD_ALERTS") or "true").strip().lower() in (
        "1", "true", "yes", "on", "да",
    )


# ── исход одного прогона ─────────────────────────────────────────────────────


@dataclass
class _Run:
    failed: bool = False
    skipped: bool = False
    reason: str = ""


_current: ContextVar[Optional[_Run]] = ContextVar("overlay_cron_guard_run", default=None)


def _describe(reason: Any) -> str:
    if isinstance(reason, BaseException):
        text = f"{type(reason).__name__}: {reason}" if str(reason) else type(reason).__name__
    else:
        text = str(reason)
    text = " ".join(text.split())
    return text if len(text) <= _REASON_LIMIT else text[: _REASON_LIMIT - 1] + "…"


def missing_table(exc: BaseException) -> bool:
    """Упало потому, что таблицы ещё нет (окно выкатки), а не потому, что сломалось.

    SQLAlchemy заворачивает ошибку asyncpg в свою, а та держит исходную в `.orig` и
    в цепочке причин — идём по всем, пока не найдём SQLSTATE 42P01.
    """
    seen: set[int] = set()
    stack: list[Any] = [exc]
    while stack:
        err = stack.pop()
        if err is None or id(err) in seen or len(seen) > 20:
            continue
        seen.add(id(err))
        if type(err).__name__ == "UndefinedTableError":
            return True
        if _UNDEFINED_TABLE in (getattr(err, "sqlstate", None), getattr(err, "pgcode", None)):
            return True
        stack.extend(
            (getattr(err, "orig", None), getattr(err, "__cause__", None), getattr(err, "__context__", None))
        )
    return False


def cron_failed(reason: Any) -> None:
    """Прогон целиком не выполнился, хотя крон поймал ошибку сам и вышел штатно.

    Вне `cron_guard` ничего не делает — функции крона зовут и из тестов, и из админки.
    """
    run = _current.get()
    if run is None:
        return
    if isinstance(reason, BaseException) and missing_table(reason):
        run.skipped = True
        return
    run.failed = True
    run.reason = _describe(reason)


def cron_skipped() -> None:
    """Прогон ничего не делал по настройке: не поломка и не починка."""
    run = _current.get()
    if run is not None:
        run.skipped = True


# ── состояние ────────────────────────────────────────────────────────────────


def _read(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except ValueError as exc:
        # Битый файл (запись атомарная, но руками его править можно) не должен
        # выключать тревоги навсегда: начинаем с чистого, файл перепишется сам.
        logger.warning(f"cron_guard: {path} нечитаем ({exc}) — начинаю с чистого состояния")
        return {}
    return data if isinstance(data, dict) else {}


def _write(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=1)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


@contextlib.contextmanager
def _locked(path: Path, lock_path: Path) -> Iterator[dict[str, Any]]:
    """Прочитать состояние под замком; изменённый словарь записывается на выходе."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            state = _read(path)
            before = json.dumps(state, sort_keys=True)
            yield state
            if json.dumps(state, sort_keys=True) != before:
                _write(path, state)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _fmt_duration(seconds: float) -> str:
    minutes = max(1, int(seconds // 60))
    if minutes < 60:
        return f"{minutes} мин"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours} ч {minutes} мин" if minutes else f"{hours} ч"
    days, hours = divmod(hours, 24)
    return f"{days} дн. {hours} ч" if hours else f"{days} дн."


def _down_text(title: str, reason: str) -> str:
    return (
        "🛑 <b>Фоновая задача не выполнилась</b>\n\n"
        f"<b>{html.escape(title)}</b> — прогон упал целиком:\n"
        f"<code>{html.escape(reason or 'причина не указана')}</code>\n\n"
        "Пока она падает, повторять не буду — напишу, когда снова заработает. "
        "Подробности — в логе контейнера remnashop-taskiq-worker."
    )


def _up_text(title: str, entry: dict[str, Any], now: datetime) -> str:
    failures = f"неудачных прогонов подряд: {int(entry.get('failures') or 0)}."
    try:
        since = datetime.fromisoformat(str(entry.get("since")))
        tail = f"Не работала {_fmt_duration((now - since).total_seconds())}, {failures}"
    except (TypeError, ValueError):
        tail = failures[0].upper() + failures[1:]
    return f"✅ <b>{html.escape(title)}</b> — снова работает.\n\n{tail}"


async def send_to_owner(text: str) -> None:
    """Сообщение владельцу ботом. Бросает исключение, если не дошло."""
    from aiogram import Bot
    from aiogram.client.default import DefaultBotProperties
    from aiogram.client.session.aiohttp import AiohttpSession

    from src.core.config import AppConfig

    config = AppConfig.get()
    session = None
    if config.bot.proxy_url:
        # aiohttp_socks не знает схем socks5h/socks4a; aiogram и так резолвит имена
        # на стороне прокси, поэтому они равны socks5/socks4 (так же делает база).
        proxy = config.bot.proxy_url.get_secret_value()
        for alias, plain in (("socks5h://", "socks5://"), ("socks4a://", "socks4://")):
            if proxy.startswith(alias):
                proxy = plain + proxy[len(alias):]
        session = AiohttpSession(proxy=proxy)
    bot = Bot(
        token=config.bot.token.get_secret_value(),
        session=session,
        default=DefaultBotProperties(parse_mode="HTML"),
    )
    try:
        await bot.send_message(int(config.bot.owner_id), text, disable_web_page_preview=True)
    finally:
        with contextlib.suppress(Exception):
            await bot.session.close()


async def record(
    name: str,
    title: str,
    *,
    failed: bool,
    reason: str = "",
    send: Optional[Sender] = None,
    path: Optional[Path] = None,
    lock_path: Optional[Path] = None,
) -> Optional[str]:
    """Учесть исход прогона и при переходе сообщить владельцу.

    Возвращает, что сделали: "down" / "up" — сообщение ушло, None — сообщать нечего
    или не вышло. Не бросает: ни сбой отправки, ни сбой файла не должны ронять крон.

    «Сообщили» отмечаем ДО отправки и откатываем, если не дошло: иначе два прогона
    одного крона, упавшие одновременно, прислали бы две тревоги, а не дошедшая
    тревога считалась бы сказанной, и владелец так и не узнал бы о поломке.
    """
    send = send or send_to_owner
    path = path or STATE_PATH
    lock_path = lock_path or LOCK_PATH
    now = _now()

    try:
        if not failed:
            # Самый частый случай — всё работает и работало. Без замка и без записи:
            # файл пишется атомарно, прочитать его наполовину нельзя.
            if name not in _read(path):
                return None
        with _locked(path, lock_path) as state:
            entry = state.get(name)
            if failed:
                entry = entry if isinstance(entry, dict) else {}
                entry.setdefault("since", now.isoformat())
                entry["failures"] = int(entry.get("failures") or 0) + 1
                entry["error"] = reason
                entry["last_failed_at"] = now.isoformat()
                notify = not entry.get("alerted")
                if notify:
                    entry["alerted"] = True
                state[name] = entry
                text = _down_text(title, reason) if notify else None
            else:
                if not isinstance(entry, dict):
                    state.pop(name, None)
                    return None
                state.pop(name)
                if not entry.get("alerted"):
                    # О падении владелец так и не узнал (тревога не дошла) — и
                    # «снова работает» ему ни о чём не скажет.
                    logger.info(f"cron_guard: «{title}» снова работает (о падении сообщить не удалось)")
                    return None
                text = _up_text(title, entry, now)
    except Exception as exc:  # noqa: BLE001 — сторож не роняет крон
        logger.error(f"cron_guard: состояние «{title}» не сохранено ({exc}) — тревоги нет")
        return None

    if text is None:
        logger.warning(f"cron_guard: «{title}» — прогон снова упал, владельцу уже сообщали: {reason}")
        return None

    try:
        await send(text)
    except Exception as exc:  # noqa: BLE001
        logger.error(f"cron_guard: сообщение владельцу о «{title}» не дошло: {exc}")
        # Откат отметки: не дошедшее сообщение повторим на следующем прогоне.
        try:
            with _locked(path, lock_path) as state:
                if failed:
                    if isinstance(state.get(name), dict):
                        state[name]["alerted"] = False
                elif name not in state:
                    state[name] = entry
        except Exception as exc2:  # noqa: BLE001
            logger.error(f"cron_guard: не откатил отметку «{title}»: {exc2}")
        return None

    logger.info(
        f"cron_guard: владельцу сообщено — «{title}» {'не выполнилась' if failed else 'снова работает'}"
    )
    return "down" if failed else "up"


# ── декоратор ────────────────────────────────────────────────────────────────


def cron_guard(name: str, title: str) -> Callable[[F], F]:
    """Сторож крона. Ставится ПОД `@inject` — прямо на функцию задачи:

        @broker.task(schedule=[...], retry_on_error=False)
        @inject(patch_module=True)
        @cron_guard("autopay", "Автопродление с баланса")
        async def run_autopay(...): ...

    Под `@inject`, а не над ним: dishka читает сигнатуру и аннотации нашей обёртки
    (functools.wraps отдаёт ей исходные), и зависимости приходят сюда именованными
    аргументами. Над `@inject` сигнатуру разбирал бы уже taskiq — со своим
    параметром контейнера, который обёртка должна была бы честно повторить.

    `name` — ключ в файле состояния, не менять без нужды (сбросит «уже сообщали»);
    `title` — как задачу называть владельцу.
    """

    def decorate(func: F) -> F:
        @functools.wraps(func)
        async def guarded(*args: Any, **kwargs: Any) -> Any:
            run = _Run()
            token = _current.set(run)
            result = None
            try:
                result = await func(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 — падение прогона и есть наш случай
                if missing_table(exc):
                    run.skipped = True
                    logger.warning(f"cron_guard: «{title}» пропущена — таблицы ещё нет ({exc})")
                else:
                    run.failed = True
                    run.reason = _describe(exc)
                    logger.opt(exception=exc).error(f"cron_guard: «{title}» упала целиком")
            finally:
                _current.reset(token)

            if run.skipped and not run.failed:
                return result
            if _enabled():
                await record(name, title, failed=run.failed, reason=run.reason)
            return result

        # Метка для сторожа в тестах: каждый наш крон обязан стоять под cron_guard.
        guarded.__cron_guard__ = name  # type: ignore[attr-defined]
        return guarded  # type: ignore[return-value]

    return decorate


__all__ = ["cron_guard", "cron_failed", "cron_skipped", "missing_table", "record", "send_to_owner"]
