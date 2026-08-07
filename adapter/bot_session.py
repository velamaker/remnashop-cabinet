"""Служебный вход в бота «Бедолага» — один на все фоновые задачи.

Зачем отдельный модуль. Личное сообщение человеку у них открывается ТОЛЬКО
сессией администратора: машинный ключ (`X-API-Key`) их кабинетное API не
принимает, а персонального сообщения в машинном API нет вовсе. Значит фоновой
задаче нужен собственный вход — почта и пароль служебного админа бота с правом
`users:send_message`.

Почему в общем файле. Дайджест, «новое устройство» и утренняя сводка приехали
тремя независимыми задачами, и каждая завела свою копию входа: три реализации
одного и того же — три места, где чинить протухший токен и разбирать их отказы.
Здесь одна.

Настройка (обе переменные обязательны, иначе доставка выключена честно):
  BEDOLAGA_CABINET_EMAIL / BEDOLAGA_CABINET_PASSWORD
Не заданы — `configured()` вернёт False, и раздел откажется включаться с
человеческой причиной вместо тихой имитации работы.
"""

from __future__ import annotations

import os
import time
from typing import Any

import httpx

EMAIL = os.environ.get("BEDOLAGA_CABINET_EMAIL", "").strip()
PASSWORD = os.environ.get("BEDOLAGA_CABINET_PASSWORD", "")

# Право, без которого их API не даст писать людям.
SEND_RIGHT = "users:send_message"

# Текст отказа общий для всех разделов: человек должен получить одну и ту же
# внятную инструкцию, из какого бы раздела он ни пришёл.
NO_DELIVERY = (
    "Уведомление некому доставить: личное сообщение у «Бедолаги» открывается "
    "только сессией администратора (машинный ключ X-API-Key их кабинетное API "
    "не принимает), а за фоновой задачей администратор не стоит. Заведите в боте "
    "служебного админа с правом «users:send_message» и передайте адаптеру "
    "BEDOLAGA_CABINET_EMAIL и BEDOLAGA_CABINET_PASSWORD."
)

# Токен живёт в памяти процесса: на диск его класть незачем, он протухает за
# минуты, а перезапуск адаптера просто войдёт заново.
_session: dict[str, Any] = {"token": "", "until": 0.0}


def configured() -> bool:
    """Заданы ли учётные данные служебного входа."""
    return bool(EMAIL and PASSWORD)


def forget() -> None:
    """Забыть текущий токен (для тестов и после смены пароля)."""
    _session.update({"token": "", "until": 0.0})


async def token(ctx: Any, renew: bool = False) -> str:
    """Токен служебного входа. `renew=True` — войти заново, не глядя на кэш."""
    now = time.monotonic()
    if not renew and _session["token"] and now < float(_session["until"]):
        return str(_session["token"])
    _session.update({"token": "", "until": 0.0})

    resp = await ctx.call(
        "POST",
        "/cabinet/auth/email/login",
        json={"email": EMAIL, "password": PASSWORD},
    )
    if resp.status_code >= 400:
        raise RuntimeError(
            f"служебный вход в бота не удался ({resp.status_code}): "
            "проверьте BEDOLAGA_CABINET_EMAIL/BEDOLAGA_CABINET_PASSWORD"
        )
    try:
        data = resp.json() or {}
    except ValueError as exc:
        raise RuntimeError("служебный вход: бот ответил не-JSON") from exc
    value = str(data.get("access_token") or "")
    if not value:
        raise RuntimeError("служебный вход: бот не отдал access_token")
    try:
        ttl = float(data.get("expires_in") or 900)
    except (TypeError, ValueError):
        ttl = 900.0
    # Минута форы: токен, протухший между проверкой и отправкой, стоил бы человеку
    # пропущенного уведомления.
    _session.update({"token": value, "until": now + max(60.0, ttl - 60.0)})
    return value


def detail(resp: httpx.Response) -> str:
    """Причина отказа словами.

    У них `detail` бывает и строкой, и объектом с `code`/`message` — например
    `no_telegram_id`, когда человек зарегистрирован только по почте и телеграма
    у него нет вовсе. Такой отказ не ошибка задачи, а факт про человека.
    """
    try:
        body = resp.json()
    except ValueError:
        body = None
    value = body.get("detail") if isinstance(body, dict) else None
    if isinstance(value, dict):
        return str(value.get("message") or value.get("code") or resp.status_code)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return f"код {resp.status_code}"


def code(resp: httpx.Response) -> str:
    """Машинный код отказа (`forbidden`, `no_telegram_id`, …) или пустая строка.

    Зачем отдельно от `detail`. Текст в `detail` — английская фраза для человека,
    и она у них меняется от сборки к сборке. Там, где по причине принимается
    РЕШЕНИЕ («писать этому человеку бесполезно всегда» против «бот сейчас
    недоступен, попробуем через час»), опираться на фразу нельзя — только на код.
    """
    try:
        body = resp.json()
    except ValueError:
        return ""
    value = body.get("detail") if isinstance(body, dict) else None
    return str(value.get("code") or "").strip() if isinstance(value, dict) else ""


async def request(ctx: Any, method: str, path: str, **kw: Any) -> httpx.Response:
    """Запрос к их КАБИНЕТНОМУ API от имени служебного админа.

    Нужен там, где машинного ключа не хватает: часть их админских ручек живёт
    только в кабинетном API и принимает исключительно сессию (личное сообщение,
    промокод с признаком «только для первой покупки»).

    Их access живёт минуты, поэтому 401 — не беда, а норма жизни: один повтор с
    новым токеном. Бесконечно повторять нельзя, это петля на 401.

    ВАЖНО ПРО КОНТЕКСТ. Служебный токен уезжает заголовком, а `Ctx.call`
    перебивает его собственным токеном сессии, если он есть. Значит звать это
    можно только из фоновой задачи (там `ctx.token` пустой), но не из
    обработчика ручки: иначе запрос уйдёт от имени зашедшего админа и упрётся в
    ЕГО права.
    """
    headers = dict(kw.pop("headers", None) or {})
    resp: httpx.Response | None = None
    for attempt in (1, 2):
        value = await token(ctx, renew=attempt == 2)
        resp = await ctx.call(
            method, path, headers={**headers, "authorization": f"Bearer {value}"}, **kw
        )
        if resp.status_code != 401 or attempt == 2:
            break
    assert resp is not None  # цикл всегда выполняется хотя бы раз
    return resp


async def send(ctx: Any, user_id: int, text: str) -> tuple[bool, str, str]:
    """Личное сообщение → (дошло, причина словами, машинный код отказа).

    Отказ НЕ превращаем в успех: по этому ответу задача решает, ставить ли
    отметку об отправке. Поставить её на неудаче — значит лишить человека
    уведомления до следующего раза.

    Третьим значением едет код: `forbidden` (заблокировал бота) и
    `no_telegram_id` — это НАВСЕГДА, и задача имеет право перестать пытаться, а
    «бот недоступен» — беда сегодняшнего часа, её надо повторить.
    """
    try:
        resp = await request(
            ctx,
            "POST",
            f"/cabinet/admin/users/{int(user_id)}/send-message",
            json={"text": text},
        )
    except httpx.HTTPError as exc:
        # Сетевая беда с ОДНИМ сообщением не должна ронять весь прогон:
        # остальным написать можно, а этот попадёт в следующий заход.
        return False, f"бот недоступен: {exc}", ""
    if resp.status_code < 400:
        return True, "", ""
    return False, detail(resp), code(resp)


async def send_message(ctx: Any, user_id: int, text: str) -> tuple[bool, str]:
    """То же самое без машинного кода — для задач, которым он не нужен."""
    ok, reason, _code = await send(ctx, user_id, text)
    return ok, reason
