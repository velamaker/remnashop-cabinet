"""Подарочный сертификат по ссылке.

ЧТО БЫЛО. Подарок и раньше был кодом `GIFT-` + 32 символа, но покупатель получал
только сам код и совет «пусть введёт его в разделе „Промокод“». Получателю надо
было найти этот раздел и набрать код руками.

ЧТО СТАЛО. Код завёрнут в ссылку `/gift/<код>`: страница показывает, что подарено,
и активирует в одно нажатие — в боте через штатный deep link `?start=promo_<код>`,
в кабинете через поле промокода с подставленным кодом.

ЧТО ЗАПЕРТО ЗДЕСЬ:
  * страница отвечает ТОЛЬКО за подарки — обычные промокоды магазина ей не
    проверить, иначе она стала бы справочной по чужим кодам;
  * состояния честные: готов / уже активирован / удалён из админки (оплачен, а
    промокода нет — так бывает, когда «чистят» список промокодов);
  * о покупателе наружу ничего: ссылку пересылают, открыть её может кто угодно;
  * ссылка на бота собирается по тому же правилу, по которому бот её разбирает, —
    иначе активация в один тап молча открывала бы пустое окно промокода;
  * сообщение с готовым подарком несёт ссылку, а без адреса кабинета — прежний код.

ЖИВАЯ БАЗА нужна для состояний: они зависят от соединения трёх таблиц. Запуск:

    RS_PG_DSN=postgresql+asyncpg://postgres:x@localhost/postgres pytest test_gift_certificate.py
"""

import importlib
import json

import pytest

from _pg_dsn import sqlalchemy_dsn

gift = importlib.import_module("src.infrastructure.services.overlay_gift")

DSN = sqlalchemy_dsn()
needs_pg = pytest.mark.skipif(not DSN, reason="нужен RS_PG_DSN (одноразовый Postgres)")

SCHEMA_NAME = "gift_certificate_test"
READY = "GIFT-" + "A" * 32
USED = "GIFT-" + "B" * 32
VOID = "GIFT-" + "C" * 32
PENDING = "GIFT-" + "D" * 32
LEGACY = "GIFT-" + "E" * 32
OFF = "GIFT-" + "F" * 32

SCHEMA = f"""
DROP SCHEMA IF EXISTS {SCHEMA_NAME} CASCADE;
CREATE SCHEMA {SCHEMA_NAME};
CREATE TABLE {SCHEMA_NAME}.gift_payments (
    payment_id UUID PRIMARY KEY, user_id INTEGER NOT NULL, plan_snapshot JSONB,
    duration_days INTEGER, amount NUMERIC(12,2), code VARCHAR(64), issued BOOLEAN NOT NULL DEFAULT false,
    issued_at TIMESTAMPTZ, created_at TIMESTAMPTZ DEFAULT now());
CREATE TABLE {SCHEMA_NAME}.users (
    id SERIAL PRIMARY KEY, cabinet_balance NUMERIC(12,2) NOT NULL DEFAULT 0);
CREATE TABLE {SCHEMA_NAME}.promocodes (
    id SERIAL PRIMARY KEY, code VARCHAR NOT NULL UNIQUE, is_active BOOLEAN NOT NULL DEFAULT true,
    reward_type VARCHAR(32) NOT NULL DEFAULT 'SUBSCRIPTION', reward INTEGER, plan_snapshot JSONB,
    availability VARCHAR(16), is_reusable BOOLEAN NOT NULL DEFAULT false, max_activations INTEGER,
    expires_at TIMESTAMPTZ);
CREATE TABLE {SCHEMA_NAME}.promocode_activations (
    id SERIAL PRIMARY KEY, promocode_id INTEGER NOT NULL REFERENCES {SCHEMA_NAME}.promocodes(id),
    user_id INTEGER NOT NULL)
"""


# ─── Без базы ─────────────────────────────────────────────────────────────────


class TestЧтоСчитаетсяПодарком:
    def test_наш_код_подходит(self):
        assert gift.is_gift_code(READY)
        assert gift.is_gift_code(READY.lower()), "регистр не важен: ссылку могли переписать руками"

    @pytest.mark.parametrize(
        "code",
        ["SUMMER2026", "GIFT-", "GIFT-ABC", "GIFT-" + "Z" * 32, "PROMO-" + "A" * 32, "", "GIFT-" + "A" * 31],
    )
    def test_чужие_и_битые_коды_не_подходят(self, code):
        # Иначе страница сертификата отвечала бы по любым промокодам магазина.
        assert not gift.is_gift_code(code)

    def test_новый_код_проходит_собственную_проверку(self):
        assert gift.is_gift_code(gift.new_gift_code())


class TestСсылки:
    def test_адрес_сертификата_от_адреса_кабинета(self, monkeypatch):
        monkeypatch.setenv("WEB_CABINET_URL", "https://shop.example/")
        assert gift.certificate_url(READY) == f"https://shop.example/gift/{READY}"

    def test_без_адреса_кабинета_ссылки_нет(self, monkeypatch):
        monkeypatch.delenv("WEB_CABINET_URL", raising=False)
        assert gift.certificate_url(READY) == ""

    def test_поделиться_экранирует_ссылку_и_текст(self):
        url = gift.share_url(f"https://shop.example/gift/{READY}", "DUO · 2 📱", 30)
        assert url.startswith("https://t.me/share/url?url=https%3A%2F%2Fshop.example%2Fgift%2F")
        assert "&text=" in url and " " not in url

    async def test_ссылка_на_бота_разбирается_ботом_в_тот_же_код(self):
        # Бот берёт `command.args`, отрезает `promo_` и делает upper() — повторяем
        # его разбор. Разойдись правила — тап по ссылке открыл бы пустое окно.
        class Bot:
            async def get_ad_link_url(self, code):
                return "https://t.me/test_shop_bot?start=ad"

        link = await gift.bot_activation_url(Bot(), READY)
        assert link.startswith("https://t.me/test_shop_bot?start=")
        args = link.split("?start=", 1)[1]
        from src.core.enums import Deeplink

        prefix = Deeplink.PROMOCODE.with_underscore
        assert args.startswith(prefix)
        assert args.removeprefix(prefix).strip().upper() == READY
        assert len(args) <= 64, "Telegram обрезает start-параметр длиннее 64 символов"

    async def test_бот_молчит_значит_ссылки_на_бота_нет(self):
        class Bot:
            async def get_ad_link_url(self, code):
                raise RuntimeError("getMe timeout")

        assert await gift.bot_activation_url(Bot(), READY) is None


class TestСообщениеСПодарком:
    def test_главное_в_сообщении_ссылка_а_код_ниже(self):
        text = gift.gift_ready_text("DUO", 30, READY, f"https://shop.example/gift/{READY}", paid=True)
        assert f"https://shop.example/gift/{READY}" in text
        assert READY in text
        assert text.index("https://") < text.index(f"<code>{READY}</code>")
        assert "Подарок оплачен" in text

    def test_без_адреса_кабинета_остаётся_прежний_код(self):
        text = gift.gift_ready_text("DUO", 30, READY, "", paid=False)
        assert f"<code>{READY}</code>" in text and "Промокод" in text

    def test_имя_тарифа_не_ломает_разметку(self):
        text = gift.gift_ready_text("<b>злой</b>", 30, READY, "", paid=False)
        assert "&lt;b&gt;злой&lt;/b&gt;" in text

    def test_кнопки_поделиться_только_при_ссылке(self):
        assert gift.gift_share_keyboard("", "DUO", 30) is None
        kb = gift.gift_share_keyboard(f"https://shop.example/gift/{READY}", "DUO", 30)
        urls = [b.url for row in kb.inline_keyboard for b in row]
        assert any(u.startswith("https://t.me/share/url?") for u in urls)
        assert f"https://shop.example/gift/{READY}" in urls


# ─── Живая база: состояния сертификата ────────────────────────────────────────


@pytest.fixture
async def session():
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    setup = create_async_engine(DSN)
    async with setup.begin() as conn:
        for stmt in SCHEMA.strip().split(";\n"):
            if stmt.strip():
                await conn.execute(text(stmt))
        snap = json.dumps({"name": "DUO · 2 📱", "id": 5})
        rows = [
            ("11111111-1111-1111-1111-111111111111", READY, True),
            ("22222222-2222-2222-2222-222222222222", USED, True),
            ("33333333-3333-3333-3333-333333333333", VOID, True),
            # Неоплаченный шлюзовый подарок: кода ещё нет (его выпускает вебхук).
            ("44444444-4444-4444-4444-444444444444", None, False),
            ("55555555-5555-5555-5555-555555555555", OFF, True),
        ]
        for pid, code, issued in rows:
            await conn.execute(
                text(
                    f"INSERT INTO {SCHEMA_NAME}.gift_payments "
                    "(payment_id, user_id, plan_snapshot, duration_days, amount, code, issued) "
                    "VALUES (CAST(:p AS uuid), 7, CAST(:s AS jsonb), 30, 499, :c, :i)"
                ),
                {"p": pid, "s": snap, "c": code, "i": issued},
            )
        # VOID — промокод удалён из админки, поэтому строки в promocodes нет.
        for code in (READY, USED):
            await conn.execute(text(f"INSERT INTO {SCHEMA_NAME}.promocodes (code) VALUES (:c)"), {"c": code})
        # OFF — админ снял галку «активен», а активаций не было.
        await conn.execute(
            text(f"INSERT INTO {SCHEMA_NAME}.promocodes (code, is_active) VALUES (:c, false)"), {"c": OFF}
        )
        # LEGACY — подарок, проданный ботом с баланса ДО исправления: только промокод,
        # строки в истории нет. Срок берётся из снимка тарифа.
        await conn.execute(
            text(
                f"INSERT INTO {SCHEMA_NAME}.promocodes (code, plan_snapshot) "
                "VALUES (:c, CAST(:s AS jsonb))"
            ),
            {"c": LEGACY, "s": json.dumps({"name": "SOLO · 1 📱", "id": 4, "duration": 90})},
        )
        await conn.execute(
            text(
                f"INSERT INTO {SCHEMA_NAME}.promocode_activations (promocode_id, user_id) "
                f"SELECT id, 99 FROM {SCHEMA_NAME}.promocodes WHERE code = :c"
            ),
            {"c": USED},
        )
        await conn.execute(text(f"INSERT INTO {SCHEMA_NAME}.users (id, cabinet_balance) VALUES (7, 1000)"))
    await setup.dispose()

    engine = create_async_engine(DSN, connect_args={"server_settings": {"search_path": SCHEMA_NAME}})
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()

    cleanup = create_async_engine(DSN)
    async with cleanup.begin() as conn:
        await conn.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA_NAME} CASCADE"))
    await cleanup.dispose()


@needs_pg
class TestСостоянияСертификата:
    async def test_живой_подарок_готов(self, session):
        cert = await gift.get_certificate(session, READY)
        assert cert == {"code": READY, "plan_name": "DUO · 2 📱", "days": 30, "state": "ready"}

    async def test_забранный_подарок_активирован(self, session):
        assert (await gift.get_certificate(session, USED))["state"] == "activated"

    async def test_удалённый_из_админки_подарок_недействителен(self, session):
        # Оплачен, но промокода нет — страница направит в поддержку, а не скажет «нет такого».
        assert (await gift.get_certificate(session, VOID))["state"] == "void"

    async def test_неоплаченный_подарок_не_показываем(self, session):
        assert await gift.get_certificate(session, PENDING) is None

    async def test_выключенный_в_админке_не_выдаём_за_активированный(self, session):
        # Активаций не было — «уже активирован» было бы неправдой, от которой обе
        # стороны решат, что код украли. Подарок оплачен → в поддержку.
        assert (await gift.get_certificate(session, OFF))["state"] == "void"

    async def test_подарок_мимо_истории_находится_по_промокоду(self, session):
        cert = await gift.get_certificate(session, LEGACY)
        assert cert == {"code": LEGACY, "plan_name": "SOLO · 1 📱", "days": 90, "state": "ready"}

    async def test_подарок_из_бота_с_баланса_сразу_виден_по_ссылке(self, session):
        # Главный блокер ревью: бот продавал подарок с баланса, но не писал историю,
        # и ссылка из его же сообщения вела на «такого подарка нет».
        from decimal import Decimal

        code = await gift.create_gift_from_balance(
            session,
            user_id=7,
            plan_snapshot={"name": "HOME · 3 📱", "id": 6, "duration": 30},
            price=Decimal("300"),
        )
        assert code and gift.is_gift_code(code)
        cert = await gift.get_certificate(session, code)
        assert cert["state"] == "ready" and cert["days"] == 30
        history = await gift.list_user_gifts(session, user_id=7)
        assert any(h["code"] == code for h in history), "подарок из бота обязан попасть в «Мои подарки»"

    async def test_код_из_ссылки_в_нижнем_регистре(self, session):
        assert (await gift.get_certificate(session, READY.lower()))["state"] == "ready"

    async def test_о_покупателе_ни_слова(self, session):
        cert = await gift.get_certificate(session, READY)
        assert set(cert) == {"code", "plan_name", "days", "state"}

    async def test_чужой_промокод_не_проверить(self, session):
        assert await gift.get_certificate(session, "SUMMER2026") is None
