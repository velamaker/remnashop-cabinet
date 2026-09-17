"""Месячная сводка письмом: чего нельзя допустить ни при каком сбое.

ЧТО ПРОВЕРЯЕМ. Письмо живому человеку не отзовёшь, поэтому тесты держат не
«письмо уходит», а «письмо НЕ уходит, когда не должно»:
  * повтор месяца, рестарт посреди прохода, упавший журнал — ни одного второго
    письма (`services/overlay_digest_email.py`, журнал `digest_email_sends`);
  * препятствия (почта выключена, нет адреса кабинета, не встала правка
    оформления, Brevo с общим адресом) — ни письма, ни записи;
  * через Brevo сводка уходит ТОЛЬКО с отдельного адреса: иначе «Отписаться» в
    почтовике закрыло бы человеку коды входа;
  * ссылка отписки подписана, экранирована и есть в каждом письме, а обычные
    письма сервиса остались байт в байт прежними;
  * публичная отписка: GET ничего не пишет, кривая ссылка не доходит до базы.

Только подделки: журнал в памяти, отправитель-заглушка, подменённые httpx и
smtplib. Ни сети, ни базы, ни настоящих адресов (домен example.test).
Взаимоисключаемость выборок с Telegram-частью на живом Postgres —
test_digest_email_pg.py.

Запуск — внутри образа бота, как в CI:

  docker run --rm --env-file ci.env -v "$PWD/admin_src/tests:/tmp/tests:ro" \
    remnashop-ci-local sh -c 'pip install -q --target /tmp/pylibs pytest pytest-asyncio \
      && PYTHONPATH=/tmp/pylibs python -m pytest /tmp/tests/test_digest_email.py -v --asyncio-mode=auto'
"""

import hashlib
import importlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

import pytest
from fastapi import HTTPException

de = importlib.import_module("src.infrastructure.services.overlay_digest_email")
digest_config = importlib.import_module("src.infrastructure.services.overlay_digest")
optout = importlib.import_module("src.web.endpoints.public.email_optout")
sender_mod = importlib.import_module("overlay_patches.email_sender")

from src.core.enums import Role  # noqa: E402
from src.core.exceptions import EmailDeliveryError  # noqa: E402

SECRET = "ci-secret-for-optout-links"
MONTH = "2026-10"
START = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)
END = START + timedelta(days=30)
GB = 1024 ** 3
CABINET = "https://cabinet.example.test"

GMAIL = {
    "enabled": True,
    "provider": "gmail",
    "host": "smtp.gmail.com",
    "port": 587,
    "use_tls": True,
    "use_ssl": False,
    "username": "noreply@example.test",
    "password": "app-password",
    "from_email": "noreply@example.test",
    "from_name": "Test VPN",
    "brevo_api_key": "",
}
BREVO = {**GMAIL, "provider": "brevo", "host": "", "brevo_api_key": "brevo-key"}
BREVO_RELAY = {**GMAIL, "provider": "custom", "host": "smtp-relay.brevo.com", "port": 587}
CUSTOM = {**GMAIL, "provider": "custom", "host": "smtp.example.test"}
YANDEX = {
    **GMAIL,
    "provider": "yandex",
    "host": "smtp.yandex.ru",
    "port": 465,
    "use_tls": False,
    "use_ssl": True,
}


@pytest.fixture(autouse=True)
def cabinet_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEB_CABINET_URL", CABINET)
    # Логотип читается из assets установки — в тесте его нет и быть не должно.
    monkeypatch.setattr(sender_mod, "_logo_src", lambda: "")


# ── Подделки ──────────────────────────────────────────────────────────────────


class InMemoryLedger:
    """Журнал отправок с той же семантикой, что у таблицы: строка занимается один раз."""

    def __init__(self, rows: Optional[dict] = None, fail_claim: bool = False) -> None:
        self.rows: dict[tuple[int, str], str] = dict(rows or {})
        self.errors: dict[tuple[int, str], str] = {}
        self.fail_claim = fail_claim

    async def claim(self, user_id: int, month: str) -> bool:
        if self.fail_claim:
            raise RuntimeError('relation "digest_email_sends" does not exist')
        if (user_id, month) in self.rows:
            return False
        self.rows[(user_id, month)] = de.SENDING
        return True

    async def finish(self, user_id: int, month: str, status: str, error: Optional[str] = None) -> None:
        self.rows[(user_id, month)] = status
        if error:
            self.errors[(user_id, month)] = error

    async def mark_if_absent(self, user_id: int, month: str, status: str) -> bool:
        if (user_id, month) in self.rows:
            return False
        self.rows[(user_id, month)] = status
        return True

    async def status(self, user_id: int, month: str) -> Optional[str]:
        return self.rows.get((user_id, month))


class FakeSender:
    """Отправитель с правкой оформления: запоминает, что и кому отправил бы."""

    def __init__(self, settings: Optional[dict] = None, enabled: bool = True, fail: Any = None) -> None:
        self.is_enabled = enabled
        self._s = dict(settings or GMAIL)
        self.fail = fail
        self.sent: list[dict] = []

    def _settings(self) -> dict:
        return dict(self._s)

    async def send_branded(self, **kwargs: Any) -> None:
        self.sent.append(kwargs)
        if self.fail is not None:
            raise self.fail


class BaseLikeSender:
    """Базовый отправитель без нашей правки: `send` есть, `send_branded` — нет."""

    is_enabled = True

    def _settings(self) -> dict:
        return dict(GMAIL)

    async def send(self, **kwargs: Any) -> None:  # pragma: no cover — звать нельзя
        raise AssertionError("базовый send для рассылки звать нельзя")


class FakeResult:
    def __init__(self, rows: Optional[list] = None, scalar: Any = None) -> None:
        self._rows = rows or []
        self._scalar = scalar

    def all(self) -> list:
        return list(self._rows)

    def first(self) -> Any:
        return self._rows[0] if self._rows else None

    def scalar(self) -> Any:
        return self._scalar


class FakeSession:
    """Сессия: помнит SQL, коммиты и откаты; изображает users и email_opt_outs."""

    def __init__(self, users: tuple = (), opted: tuple = (), rows: Optional[list] = None, fail: bool = False) -> None:
        self.users = set(users)
        self.opted = set(opted)
        self.rows = rows or []
        self.fail = fail
        self.executed: list[tuple[str, dict]] = []
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, statement: Any, params: Optional[dict] = None) -> FakeResult:
        sql = str(statement)
        params = params or {}
        self.executed.append((sql, params))
        if self.fail:
            raise RuntimeError("current transaction is aborted")
        if sql.startswith("SELECT 1 FROM users"):
            return FakeResult([(1,)] if params["u"] in self.users else [])
        if sql.startswith("SELECT 1 FROM email_opt_outs"):
            return FakeResult([(1,)] if params["u"] in self.opted else [])
        if sql.startswith("INSERT INTO email_opt_outs"):
            self.opted.add(params["u"])
            return FakeResult()
        if sql.startswith("DELETE FROM email_opt_outs"):
            self.opted.discard(params["u"])
            return FakeResult()
        return FakeResult(self.rows)

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


def person(user_id: int, lang: str = "ru") -> Any:
    return de.EmailRecipient(user_id, lang, f"user{user_id}@example.test", f"uuid-{user_id}")


async def usage_ok(sdk: Any, uuid: str, start: datetime, end: datetime) -> tuple[int, str]:
    return int(42.5 * GB), "Нидерланды"


async def no_sleep(_seconds: float) -> None:
    return None


async def run_pass(
    recipients: list,
    *,
    sender: Any = None,
    ledger: Any = None,
    cfg: Optional[dict] = None,
    **kwargs: Any,
) -> tuple[dict, Any, Any]:
    sender = sender if sender is not None else FakeSender()
    ledger = ledger if ledger is not None else InMemoryLedger()
    kwargs.setdefault("usage", usage_ok)
    kwargs.setdefault("sleep", no_sleep)
    kwargs.setdefault("blocked_fetch", _never_blocked)
    summary = await de.run_email_pass(
        sender=sender,
        cfg=cfg if cfg is not None else {"email_enabled": True, "email_from": ""},
        settings=de.sender_settings(sender),
        sdk=None,
        secret=SECRET,
        month=MONTH,
        start=START,
        end=END,
        recipients=recipients,
        ledger=ledger,
        brand="Test VPN",
        **kwargs,
    )
    return summary, sender, ledger


async def _never_blocked(api_key: str, sender_email: str) -> set:
    return set()


# ── Повторы ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_second_pass_same_month_sends_nothing() -> None:
    """Дубль задачи или ручной перезапуск в тот же месяц: писем — по одному на человека."""
    ledger = InMemoryLedger()
    sender = FakeSender()
    first, _, _ = await run_pass([person(1), person(2)], sender=sender, ledger=ledger)
    second, _, _ = await run_pass([person(1), person(2)], sender=sender, ledger=ledger)

    assert first[de.SENT] == 2
    assert second[de.SENT] == 0 and second["already"] == 2
    assert sorted(s["to"] for s in sender.sent) == ["user1@example.test", "user2@example.test"]


@pytest.mark.asyncio
async def test_crash_between_claim_and_send_is_not_resent() -> None:
    """Упали после занятия строки: неизвестно, ушло ли письмо, — второй раз не шлём."""
    ledger = InMemoryLedger({(1, MONTH): de.SENDING})
    summary, sender, _ = await run_pass([person(1)], ledger=ledger)

    assert sender.sent == []
    assert summary["already"] == 1
    assert ledger.rows[(1, MONTH)] == de.SENDING


@pytest.mark.asyncio
async def test_failed_send_marked_and_not_retried() -> None:
    try:
        raise RuntimeError("Brevo API returned 400: sender not valid")
    except RuntimeError as cause:
        failure = EmailDeliveryError("Failed to send email")
        failure.__cause__ = cause
    ledger = InMemoryLedger()
    summary, _, _ = await run_pass([person(1)], sender=FakeSender(fail=failure), ledger=ledger)

    assert summary[de.FAILED] == 1
    assert ledger.rows[(1, MONTH)] == de.FAILED
    # В журнал — настоящая причина, а не обёртка «Failed to send email».
    assert "sender not valid" in ledger.errors[(1, MONTH)]

    retry_sender = FakeSender()
    await run_pass([person(1)], sender=retry_sender, ledger=ledger)
    assert retry_sender.sent == []


@pytest.mark.asyncio
async def test_no_traffic_and_panel_error_are_not_sent() -> None:
    async def usage(sdk: Any, uuid: str, start: datetime, end: datetime) -> Any:
        return None if uuid == "uuid-1" else (0, None)

    summary, sender, ledger = await run_pass([person(1), person(2)], usage=usage)

    assert sender.sent == []
    assert ledger.rows == {(1, MONTH): de.USAGE_ERROR, (2, MONTH): de.NO_TRAFFIC}
    assert summary[de.USAGE_ERROR] == 1 and summary[de.NO_TRAFFIC] == 1


@pytest.mark.asyncio
async def test_cap_marks_rest_over_limit() -> None:
    pauses: list[float] = []

    async def sleep(seconds: float) -> None:
        pauses.append(seconds)

    summary, sender, ledger = await run_pass(
        [person(1), person(2), person(3)], max_per_run=2, pause=0.5, sleep=sleep
    )

    assert len(sender.sent) == 2
    assert ledger.rows[(3, MONTH)] == de.OVER_LIMIT
    assert summary[de.OVER_LIMIT] == 1
    assert pauses and all(p == 0.5 for p in pauses)


# ── Препятствия ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_blockers_mean_zero_sends_and_zero_marks(monkeypatch: pytest.MonkeyPatch) -> None:
    # Правка оформления не встала — у отправителя только базовый send.
    summary, _, ledger = await run_pass([person(1)], sender=BaseLikeSender())
    assert de.BLOCK_PATCH in summary["blocked"] and ledger.rows == {}

    # Почта выключена в разделе «Почта».
    summary, sender, ledger = await run_pass([person(1)], sender=FakeSender(enabled=False))
    assert de.BLOCK_MAIL in summary["blocked"]
    assert sender.sent == [] and ledger.rows == {}

    # Тумблер писем выключен (например, файл восстановили из бэкапа как есть).
    summary, sender, ledger = await run_pass([person(1)], cfg={"email_enabled": False, "email_from": ""})
    assert de.BLOCK_DISABLED in summary["blocked"]
    assert sender.sent == [] and ledger.rows == {}

    # Нет адреса кабинета — нечем подписать ссылку «Отписаться».
    monkeypatch.setenv("WEB_CABINET_URL", "")
    summary, sender, ledger = await run_pass([person(1)])
    assert de.BLOCK_CABINET in summary["blocked"]
    assert sender.sent == [] and ledger.rows == {}


@pytest.mark.asyncio
async def test_brevo_requires_separate_sender() -> None:
    for own in ("", "noreply@example.test", "NoReply@Example.TEST"):
        cfg = {"email_enabled": True, "email_from": own}
        assert de.BLOCK_BREVO in de.email_blockers(FakeSender(BREVO), cfg, BREVO), own
        summary, sender, ledger = await run_pass([person(1)], sender=FakeSender(BREVO), cfg=cfg)
        assert sender.sent == [] and ledger.rows == {}, own

    # Пресет Gmail: From он подменить не даст, блок-листа Brevo у него нет — можно.
    assert de.email_blockers(FakeSender(GMAIL), {"email_enabled": True, "email_from": ""}, GMAIL) == []
    # SMTP-релей Brevo под видом «своего сервера» — та же блокировка отправителя.
    relay_cfg = {"email_enabled": True, "email_from": ""}
    assert de.BLOCK_BREVO in de.email_blockers(FakeSender(BREVO_RELAY), relay_cfg, BREVO_RELAY)
    # Отдельный адрес — препятствия нет, и уходит письмо именно с него.
    own_cfg = {"email_enabled": True, "email_from": "digest@example.test"}
    assert de.email_blockers(FakeSender(BREVO), own_cfg, BREVO) == []
    assert de.effective_from(BREVO, own_cfg) == "digest@example.test"
    assert de.effective_from(YANDEX, own_cfg) == "noreply@example.test"


@pytest.mark.asyncio
async def test_provider_blocked_not_sent() -> None:
    cfg = {"email_enabled": True, "email_from": "digest@example.test"}
    asked: list[tuple[str, str]] = []

    async def blocked(api_key: str, sender_email: str) -> Optional[set]:
        asked.append((api_key, sender_email))
        return {"user1@example.test"}

    summary, sender, ledger = await run_pass(
        [person(1), person(2)], sender=FakeSender(BREVO), cfg=cfg, blocked_fetch=blocked
    )
    assert asked == [("brevo-key", "digest@example.test")]
    assert [s["to"] for s in sender.sent] == ["user2@example.test"]
    assert ledger.rows[(1, MONTH)] == de.PROVIDER_BLOCKED and summary[de.PROVIDER_BLOCKED] == 1

    # Блок-лист не прочитался — отправка идёт: Brevo заблокированному всё равно не доставит.
    async def unknown(api_key: str, sender_email: str) -> Optional[set]:
        return None

    summary, sender, _ = await run_pass(
        [person(1), person(2)], sender=FakeSender(BREVO), cfg=cfg, blocked_fetch=unknown
    )
    assert len(sender.sent) == 2

    # Не Brevo — блок-лист не спрашиваем вовсе.
    asked.clear()
    await run_pass([person(1)], sender=FakeSender(GMAIL), blocked_fetch=blocked)
    assert asked == []


@pytest.mark.asyncio
async def test_ledger_unavailable_fails_closed() -> None:
    """Нет таблицы журнала — ни одного письма: слать без записи значит слать повторно."""
    sender = FakeSender()
    with pytest.raises(RuntimeError):
        await run_pass([person(1), person(2)], sender=sender, ledger=InMemoryLedger(fail_claim=True))
    assert sender.sent == []


@pytest.mark.asyncio
async def test_audience_select_failure_rolls_back() -> None:
    session = FakeSession(fail=True)
    assert await de.select_email_audience_safe(session) == []
    assert session.rollbacks == 1


def test_audience_sql_string_guards() -> None:
    """Дешёвый страж условий выборок (настоящая проверка — на Postgres, в _pg)."""
    for part in (
        "u.role = 'USER'",
        "u.is_blocked = false",
        "s.status = 'ACTIVE'",
        "s.user_remna_id IS NOT NULL",
        "u.telegram_id IS NULL",
        "NOT EXISTS (SELECT 1 FROM push_subscriptions p WHERE p.user_id = u.id)",
        "u.email IS NOT NULL AND u.is_email_verified = true",
        "email_opt_outs o",
        "o.kind = 'digest'",
    ):
        assert part in de.EMAIL_AUDIENCE_SQL, part
    for part in (
        "u.role = 'USER'",
        "s.status = 'ACTIVE'",
        "u.telegram_id IS NOT NULL",
        "EXISTS(SELECT 1 FROM push_subscriptions p WHERE p.user_id = u.id)",
    ):
        assert part in de.TG_AUDIENCE_SQL, part
    # Крон берёт Telegram-аудиторию именно из общей константы, а не своей копией.
    task = Path(de.__file__).parents[1] / "taskiq" / "tasks" / "digest.py"
    assert "text(TG_AUDIENCE_SQL)" in task.read_text(encoding="utf-8")


# ── Ссылка отписки и письмо ───────────────────────────────────────────────────


def test_optout_token_roundtrip_and_tamper() -> None:
    token = de.make_optout_token(42, SECRET)
    assert de.parse_optout_token(token, SECRET) == 42

    uid, sig = token.split(".")
    flipped = sig[:-1] + ("0" if sig[-1] != "0" else "1")
    for bad in (
        f"{uid}.{flipped}",
        f"43.{sig}",  # чужой id с этой подписью
        f"042.{sig}",  # тот же id с ведущим нулём
        f"{uid}.{sig.upper()}",
        f"{token}\n",
        f" {token}",
        f"{uid}.{sig}0",
        "",
        "42",
        "abc.def",
        "x" * 500,
    ):
        assert de.parse_optout_token(bad, SECRET) is None, bad
    assert de.parse_optout_token(token, "другой ключ") is None
    assert de.parse_optout_token(token, "") is None
    assert de.parse_optout_token(None, SECRET) is None


def test_preview_token_is_invalid() -> None:
    assert de.parse_optout_token("preview", SECRET) is None
    assert de.unsubscribe_page_url("preview") == f"{CABINET}/email/unsubscribe?t=preview"


def test_email_has_unsubscribe_link_and_escapes() -> None:
    subject, body, opts = de.build_email_parts("ru", int(42.5 * GB), "<b>x</b>", "Test VPN")
    assert subject == "Ваш месяц с Test VPN"
    assert "42,5 ГБ" in body and "Любимый сервер — <b>x</b>." in body

    url = f'{CABINET}/email/unsubscribe?t=1.abc&x="y"'
    _, text, html = sender_mod._render_branded(subject, body, "Test VPN", unsubscribe_url=url, **opts)

    assert f'href="{CABINET}/email/unsubscribe?t=1.abc&amp;x=&quot;y&quot;"' in html
    assert "&lt;b&gt;x&lt;/b&gt;" in html and "<b>x</b>" not in html
    assert "42,5" in html and "Отписаться от сводки" in html
    assert "Сводка приходит раз в месяц" in html
    assert text.endswith(f"Отписаться от сводки: {url}")

    en_subject, en_body, en_opts = de.build_email_parts("EN", int(42.5 * GB), None, "Test VPN", test=True)
    assert en_subject == "[Test] Your month with Test VPN"
    assert "42.5 GB" in en_body and "Favorite server" not in en_body
    assert en_opts["unsubscribe_label"] == "Unsubscribe from the summary"
    # Прочие языки — русский текст.
    assert de.build_email_parts("kk", GB, None, "B")[0] == "Ваш месяц с B"

    # Письмо, которое уйдёт человеку: ссылка отписки подписана его id.
    token = de.make_optout_token(7, SECRET)
    assert de.one_click_url(token) == f"{CABINET}/api/email-optout/digest?t={token}"
    assert de.unsubscribe_page_url(token) == f"{CABINET}/email/unsubscribe?t={token}"


@pytest.mark.asyncio
async def test_pass_puts_signed_links_into_each_email() -> None:
    _, sender, _ = await run_pass([person(7, lang="en")], cfg={"email_enabled": True, "email_from": "digest@example.test"})
    (sent,) = sender.sent
    token = de.make_optout_token(7, SECRET)
    assert sent["to"] == "user7@example.test"
    assert sent["from_email"] == "digest@example.test"
    assert sent["list_unsubscribe_url"] == de.one_click_url(token)
    assert sent["unsubscribe_url"] == de.unsubscribe_page_url(token)
    assert sent["subject"] == "Your month with Test VPN"


# Хэши вывода `_render_branded(subject, body, from_name)` ДО этой правки (v1.3.8,
# без логотипа, WEB_CABINET_URL=https://cabinet.example.test). Сверено запуском
# старой и новой функции на одних входных данных — совпали побайтно.
PLAIN_HTML_SHA = "78b8beb8fc85e525873c80542b87bd3a21f191d94a63ba56590a9c9cebedc1e1"
PLAIN_TEXT_SHA = "e71c2fd520854a4a626d80dabf89150bc486977a7e8dcd6cf15713ac694c9721"


def test_plain_branded_email_unchanged() -> None:
    """Напоминания и алерты не передают новых аргументов — их письма не изменились."""
    _, text, html = sender_mod._render_branded("Тема <1>", "Строка & 2\nстрока 3", "Brand")
    assert hashlib.sha256(html.encode()).hexdigest() == PLAIN_HTML_SHA
    assert hashlib.sha256(text.encode()).hexdigest() == PLAIN_TEXT_SHA
    assert "Отписаться" not in html and "Отписаться" not in text
    assert '<div style="background:#fafbfc;padding:14px 28px;border-top:1px solid #eef0f2;">\n      <span' in html


# ── Отправитель: Brevo и SMTP ─────────────────────────────────────────────────


def _sender(settings: dict) -> Any:
    sender = object.__new__(sender_mod.OverlaySmtpEmailSender)
    sender._settings = lambda: dict(settings)
    return sender


@pytest.mark.asyncio
async def test_brevo_branded_uses_digest_sender_without_custom_unsubscribe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    posts: list[dict] = []

    class FakeClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> "FakeClient":
            return self

        async def __aexit__(self, *exc: Any) -> None:
            return None

        async def post(self, url: str, json: dict, headers: dict) -> Any:
            posts.append({"url": url, "json": json, "headers": headers})
            return SimpleNamespace(status_code=201, text="{}")

    monkeypatch.setattr(sender_mod.httpx, "AsyncClient", FakeClient)
    sender = _sender(BREVO)

    await sender.send_branded(
        to="user1@example.test",
        subject="S",
        body="B",
        from_email="digest@example.test",
        list_unsubscribe_url=f"{CABINET}/api/email-optout/digest?t=1.x",
        unsubscribe_url=f"{CABINET}/email/unsubscribe?t=1.x",
        unsubscribe_label="Отписаться от сводки",
    )
    payload = posts[0]["json"]
    assert payload["sender"]["email"] == "digest@example.test"
    assert "headers" not in payload
    assert "List-Unsubscribe" not in posts[0]["headers"]
    assert "Отписаться от сводки" in payload["htmlContent"]

    # Письмо с кодом и прочие — с основного адреса, payload прежний.
    await sender.send(to="user1@example.test", subject="S", body="B")
    plain = posts[1]["json"]
    assert plain["sender"]["email"] == "noreply@example.test"
    assert set(plain) == {"sender", "to", "subject", "textContent", "htmlContent"}


@pytest.mark.asyncio
async def test_smtp_branded_has_list_unsubscribe_and_from(monkeypatch: pytest.MonkeyPatch) -> None:
    messages: list[Any] = []

    class FakeSMTP:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def __enter__(self) -> "FakeSMTP":
            return self

        def __exit__(self, *exc: Any) -> None:
            return None

        def ehlo(self) -> None:
            return None

        def starttls(self) -> None:
            return None

        def login(self, *args: Any) -> None:
            return None

        def send_message(self, message: Any) -> None:
            messages.append(message)

    monkeypatch.setattr(sender_mod.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(sender_mod.smtplib, "SMTP_SSL", FakeSMTP)
    one_click = f"{CABINET}/api/email-optout/digest?t=1.x"

    await _sender(CUSTOM).send_branded(
        to="user1@example.test", subject="S", body="B",
        from_email="digest@example.test", list_unsubscribe_url=one_click,
    )
    custom = messages[-1]
    assert custom["From"] == "Test VPN <digest@example.test>"
    assert custom["List-Unsubscribe"] == f"<{one_click}>"
    assert custom["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"

    # Яндекс не даёт подменить From — письмо уходит с основного адреса.
    await _sender(YANDEX).send_branded(
        to="user1@example.test", subject="S", body="B",
        from_email="digest@example.test", list_unsubscribe_url=one_click,
    )
    assert messages[-1]["From"] == "Test VPN <noreply@example.test>"
    assert messages[-1]["List-Unsubscribe"] == f"<{one_click}>"

    # Обычное письмо — без заголовков отписки.
    await _sender(CUSTOM).send(to="user1@example.test", subject="S", body="B")
    assert messages[-1]["List-Unsubscribe"] is None


class _LogCapture:
    """Всё, что отправитель написал в loguru, одной строкой на запись."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self._id: Optional[int] = None

    def __enter__(self) -> "_LogCapture":
        from loguru import logger

        self._id = logger.add(lambda m: self.lines.append(str(m)), format="{message}", level="DEBUG")
        return self

    def __exit__(self, *exc: Any) -> None:
        from loguru import logger

        logger.remove(self._id)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


@pytest.mark.asyncio
async def test_branded_brevo_log_has_no_recipient_address(monkeypatch: pytest.MonkeyPatch) -> None:
    """Проход сводки шлёт пачку писем: адрес в логе на каждое превратил бы лог
    воркера в список почт клиентов. Ни при успехе, ни при отказе Brevo адреса нет."""
    status = {"code": 201, "text": "{}"}

    class FakeClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> "FakeClient":
            return self

        async def __aexit__(self, *exc: Any) -> None:
            return None

        async def post(self, url: str, json: dict, headers: dict) -> Any:
            return SimpleNamespace(status_code=status["code"], text=status["text"])

    monkeypatch.setattr(sender_mod.httpx, "AsyncClient", FakeClient)
    address = "Client.Person@example.test"

    with _LogCapture() as logs:
        await _sender(BREVO).send_branded(
            to=address, subject="S", body="B", from_email="digest@example.test"
        )
    assert "via Brevo (status 201)" in logs.text
    assert address.lower() not in logs.text.lower()

    # Brevo отказал и вернул адрес в теле ответа (в другом регистре).
    status.update(code=400, text=f'{{"message":"{address.lower()} is blocked"}}')
    with _LogCapture() as logs, pytest.raises(EmailDeliveryError):
        await _sender(BREVO).send_branded(
            to=address, subject="S", body="B", from_email="digest@example.test"
        )
    assert "400" in logs.text, "код ответа нужен для разбора"
    assert address.lower() not in logs.text.lower()


@pytest.mark.asyncio
async def test_branded_smtp_refused_log_has_no_recipient_address(monkeypatch: pytest.MonkeyPatch) -> None:
    """SMTPRecipientsRefused несёт адрес в самом тексте ошибки — в лог он не попадает."""
    address = "client.person@example.test"

    class RefusingSMTP:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def __enter__(self) -> "RefusingSMTP":
            return self

        def __exit__(self, *exc: Any) -> None:
            return None

        def ehlo(self) -> None:
            return None

        def starttls(self) -> None:
            return None

        def login(self, *args: Any) -> None:
            return None

        def send_message(self, message: Any) -> None:
            upper = address.upper()
            raise sender_mod.smtplib.SMTPRecipientsRefused(
                {upper: (550, f"5.1.1 <{upper}>: Recipient address rejected".encode())}
            )

    monkeypatch.setattr(sender_mod.smtplib, "SMTP", RefusingSMTP)
    monkeypatch.setattr(sender_mod.smtplib, "SMTP_SSL", RefusingSMTP)

    with _LogCapture() as logs, pytest.raises(EmailDeliveryError):
        await _sender(CUSTOM).send_branded(
            to=address, subject="S", body="B", from_email="digest@example.test"
        )
    assert address not in logs.text.lower()
    assert "SMTPRecipientsRefused" in logs.text and "550" in logs.text


# ── Конфиг, расход, лента ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_config_defaults_and_survive_digest_save(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(digest_config, "ASSETS_DIR", tmp_path)
    monkeypatch.setattr(digest_config, "CONFIG_PATH", tmp_path / "digest.json")

    defaults = digest_config._normalize({})
    assert defaults["email_enabled"] is False and defaults["email_from"] == ""
    assert digest_config._normalize({"email_from": "not-an-email"})["email_from"] == ""
    assert digest_config._normalize({"email_from": " Digest@Example.TEST "})["email_from"] == "digest@example.test"

    digest_config.save_config(
        {"enabled": False, "day_of_month": 1, "hour": 12, "email_enabled": True, "email_from": "digest@example.test"}
    )
    # Карточка дайджеста сохраняет только свои три поля — поля писем не сбрасываются.
    admin_digest = importlib.import_module("src.web.endpoints.admin.digest")
    owner = SimpleNamespace(role=Role.OWNER)
    await admin_digest.update_digest(admin_digest.DigestUpdate(enabled=True, day_of_month=5, hour=9), owner)
    saved = digest_config.load_config()
    assert saved == {
        "enabled": True,
        "day_of_month": 5,
        "hour": 9,
        "email_enabled": True,
        "email_from": "digest@example.test",
    }


@pytest.mark.asyncio
async def test_fetch_usage_sum_and_favorite() -> None:
    calls: list[dict] = []

    class Stats:
        def __init__(self, nodes: Any) -> None:
            self.nodes = nodes

        async def get_stats_user_usage(self, **kwargs: Any) -> Any:
            calls.append(kwargs)
            if isinstance(self.nodes, Exception):
                raise self.nodes
            return SimpleNamespace(root=SimpleNamespace(top_nodes=self.nodes))

    nodes = [SimpleNamespace(name="Германия", total=10 * GB), SimpleNamespace(name="Нидерланды", total=30 * GB)]
    sdk = SimpleNamespace(bandwidthstats=Stats(nodes))
    assert await de.fetch_usage(sdk, "uuid-1", START, END) == (40 * GB, "Нидерланды")
    assert calls[0] == {"uuid": "uuid-1", "top_nodes_limit": 5, "start": "2026-09-01", "end": "2026-10-01"}

    assert await de.fetch_usage(SimpleNamespace(bandwidthstats=Stats([])), "u", START, END) == (0, None)
    assert await de.fetch_usage(SimpleNamespace(bandwidthstats=Stats(RuntimeError("panel down"))), "u", START, END) is None
    assert await de.fetch_usage(None, "u", START, END) is None


@pytest.mark.asyncio
async def test_sent_writes_feed_entry_best_effort() -> None:
    written: list[tuple] = []

    async def feed(session: Any, user_id: int, payload: dict) -> None:
        written.append((session, user_id, payload))

    session = FakeSession()
    summary, _, _ = await run_pass([person(1)], session_for_feed=session, feed=feed)
    assert summary[de.SENT] == 1
    (_, user_id, payload) = written[0]
    assert user_id == 1
    # В колокольчике — ровно та сводка, что у Telegram-части.
    assert payload["title"] == "📊 Ваш месяц с VPN"
    assert payload["body"] == "За месяц вы использовали 42.5 ГБ. Любимый сервер — Нидерланды. Спасибо, что с нами!"

    async def broken_feed(session: Any, user_id: int, payload: dict) -> None:
        raise RuntimeError("user_notifications is gone")

    session = FakeSession()
    summary, _, ledger = await run_pass([person(1)], session_for_feed=session, feed=broken_feed)
    assert summary[de.SENT] == 1 and ledger.rows[(1, MONTH)] == de.SENT
    assert session.rollbacks == 1


@pytest.mark.asyncio
async def test_dry_run_writes_nothing() -> None:
    session = FakeSession(rows=[(1, "ru", "user1@example.test", "uuid-1"), (2, "en", "user2@example.test", "uuid-2")])
    ledger = InMemoryLedger({(2, MONTH): de.SENT})
    result = await de.dry_run(
        session, sender=FakeSender(), cfg={"email_enabled": False, "email_from": ""}, settings=GMAIL,
        sdk=None, month=MONTH, start=START, end=END, ledger=ledger, usage=usage_ok, sleep=no_sleep,
    )
    assert result["audience"] == 2 and result["would_send"] == 1 and result["truncated"] is False
    assert [i["outcome"] for i in result["items"]] == ["would_send", "already_this_month"]
    assert result["items"][0]["gb"] == 42.5
    assert ledger.rows == {(2, MONTH): de.SENT}
    assert session.commits == 0


# ── Публичная отписка ─────────────────────────────────────────────────────────


def _writes(session: FakeSession) -> list[str]:
    return [sql for sql, _ in session.executed if sql.startswith(("INSERT", "DELETE", "UPDATE"))]


@pytest.mark.asyncio
async def test_public_optout_endpoints() -> None:
    token = de.make_optout_token(5, SECRET)
    session = FakeSession(users=(5,))

    # Открыли ссылку (или её открыл сканер почты) — ничего не меняется.
    assert await optout.optout_status(session, SECRET, token) == {"subscribed": True}
    assert _writes(session) == [] and session.commits == 0

    assert await optout.optout_set(session, SECRET, token, opted_out=True) == {"subscribed": False}
    assert any("ON CONFLICT" in sql for sql in _writes(session)) and session.commits == 1
    # Повтор (почтовик прислал one-click дважды) — не ошибка.
    assert await optout.optout_set(session, SECRET, token, opted_out=True) == {"subscribed": False}
    assert session.opted == {5}
    assert await optout.optout_status(session, SECRET, token) == {"subscribed": False}

    assert await optout.optout_set(session, SECRET, token, opted_out=False) == {"subscribed": True}
    assert _writes(session)[-1].startswith("DELETE FROM email_opt_outs") and session.commits == 3
    assert session.opted == set()

    # Кривая ссылка — 400 без единого обращения к базе.
    clean = FakeSession(users=(5,))
    for bad in ("5.deadbeef", "preview", "", f"6.{token.split('.')[1]}"):
        with pytest.raises(HTTPException) as err:
            await optout.optout_set(clean, SECRET, bad, opted_out=True)
        assert err.value.status_code == 400
    assert clean.executed == [] and clean.commits == 0

    # Подпись верна, но человека уже нет — тоже 400, и ничего не записано.
    gone = FakeSession(users=())
    with pytest.raises(HTTPException) as err:
        await optout.optout_set(gone, SECRET, token, opted_out=True)
    assert err.value.status_code == 400
    assert _writes(gone) == [] and gone.commits == 0
