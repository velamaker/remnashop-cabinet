"""Крошечный HTTP-клиент для контрактных проверок. Только стандартная библиотека.

ПОЧЕМУ не requests/httpx: эти проверки должны запускаться на любой установке —
в том числе на голом сервере, где стоит только системный python3, а зависимости
живут внутри контейнеров (см. грабли venv/pip в памяти проекта). Ставить пакет
ради двух GET-ов — лишний повод, чтобы проверку не запустили.

ПОЧЕМУ куки разбираем руками, а не http.cookiejar: бэкенд ставит сессию с флагом
`Secure` (так и надо в проде), а проверяем мы по http://127.0.0.1 — штатный
CookieJar такие куки молча выбрасывает и вся авторизованная часть проверки
превращается в череду 401. Тот же приём уже использован в adapter/selfcheck.py.

БЕЗОПАСНОСТЬ: клиент умеет только те методы, которые ему передали. Вызывающий код
обязан слать GET (и единственный POST /auth/login) — ничего мутирующего.
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from typing import Any


class Reply:
    """Ответ бэкенда в удобной для проверок форме.

    `status == 0` означает «связи не было вовсе» — это отдельный случай, его
    нельзя путать с 5xx: недоступный стенд не должен выглядеть как провал
    контракта.
    """

    __slots__ = ("status", "body", "content_type", "raw", "error")

    def __init__(self, status: int, raw: bytes, content_type: str, error: str = "") -> None:
        self.status = status
        self.raw = raw
        self.content_type = content_type
        self.error = error
        self.body: Any = None
        if raw:
            try:
                self.body = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                self.body = None

    @property
    def is_json(self) -> bool:
        return "application/json" in self.content_type.lower()

    @property
    def text(self) -> str:
        return self.raw.decode("utf-8", "replace")

    def short(self, limit: int = 90) -> str:
        return self.text[:limit].replace("\n", " ")


class Client:
    """Сессия к одному бэкенду: хранит куки и подставляет их в каждый запрос."""

    def __init__(self, base: str, prefix: str = "", timeout: float = 30.0) -> None:
        self.base = base.rstrip("/")
        self.prefix = prefix.rstrip("/")
        self.timeout = timeout
        self.cookies: dict[str, str] = {}
        # Чужой бот живёт не на куках, а на Bearer — канарейке нужен именно он.
        self.bearer = ""
        # Самоподписанный сертификат на локальном стенде — не повод падать:
        # проверяем контракт, а не TLS. Для http:// это всё равно не используется.
        self._ctx = ssl.create_default_context()
        self._ctx.check_hostname = False
        self._ctx.verify_mode = ssl.CERT_NONE

    @property
    def has_session(self) -> bool:
        return bool(self.cookies)

    def request(
        self,
        method: str,
        path: str,
        body: dict | None = None,
        *,
        with_cookies: bool = True,
        absolute: bool = False,
    ) -> Reply:
        url = (self.base if absolute else self.base + self.prefix) + path
        data = json.dumps(body).encode() if body is not None else None
        headers: dict[str, str] = {"accept": "application/json"}
        if data:
            headers["content-type"] = "application/json"
        if with_cookies and self.cookies:
            headers["cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        if with_cookies and self.bearer:
            headers["authorization"] = f"Bearer {self.bearer}"
        req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=self._ctx) as resp:
                self._absorb(resp.headers.get_all("set-cookie"))
                return Reply(resp.status, resp.read(), resp.headers.get("content-type", ""))
        except urllib.error.HTTPError as exc:
            # Ошибочный ответ — это тоже ответ: его форму мы как раз и проверяем.
            self._absorb(exc.headers.get_all("set-cookie") if exc.headers else None)
            return Reply(exc.code, exc.read(), exc.headers.get("content-type", "") if exc.headers else "")
        except (OSError, ValueError) as exc:
            return Reply(0, b"", "", error=str(exc))

    def get(self, path: str, **kw: Any) -> Reply:
        return self.request("GET", path, **kw)

    def _absorb(self, raw_cookies: list[str] | None) -> None:
        for raw in raw_cookies or []:
            name, _, rest = raw.partition("=")
            value = rest.split(";")[0]
            name = name.strip()
            if not name:
                continue
            # Пустое значение = бэкенд гасит куку (выход, отзыв сессии).
            if value:
                self.cookies[name] = value
            else:
                self.cookies.pop(name, None)
