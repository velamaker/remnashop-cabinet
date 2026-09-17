"""Рекламные ссылки: код, который Telegram донесёт, и готовая ссылка в ответе.

ЧТО БЫЛО. Бот засчитывает рекламный переход только из deep-link
`t.me/бот?start=ad_<код>`, а Telegram пропускает в параметре start лишь латиницу,
цифры, `_` и `-`. Админка же принимала любой код: «сторис июнь» сохранялся, ссылка
выглядела рабочей, переходы по ней молча не считались. И сама ссылка нигде не
показывалась — список отдавал голый код, который некуда вставить.

Запуск — внутри образа бота, как остальные тесты (см. .github/workflows/ci.yml).
"""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.core.enums import Deeplink
from src.web.endpoints.admin import ad_links


class FakeBot:
    def __init__(self, username="example_bot", fail=False):
        self.username = username
        self.fail = fail

    async def get_ad_link_url(self, code):
        if self.fail:
            raise RuntimeError("telegram недоступен")
        # Ровно правило базы: Deeplink.build_url на адресе бота.
        return Deeplink.ADVERTISING.build_url(f"https://t.me/{self.username}", code)


def link(code="storis_u_blogera", **kw):
    return SimpleNamespace(id=1, name="Сторис", code=code, is_active=True, created_at=None, **kw)


@pytest.mark.parametrize("code", ["insta_story-june2", "A1", "a" * 61])
def test_valid_codes_pass(code):
    assert ad_links.validate_code(code) is None


@pytest.mark.parametrize("code", ["сторис", "insta story", "promo.june", "a" * 62, ""])
def test_codes_telegram_cannot_carry_are_rejected(code):
    assert ad_links.validate_code(code)


def test_url_matches_the_code_the_bot_parses():
    """Ссылка обязана разбираться базовым парсером обратно в тот же код."""
    base = asyncio.run(ad_links.bot_link_base(FakeBot()))
    data = ad_links._link_to_dict(link("storis_u_blogera"), base)
    assert data["url"] == "https://t.me/example_bot?start=ad_storis_u_blogera"
    start = data["url"].split("?start=", 1)[1]
    prefix = Deeplink.ADVERTISING.with_underscore
    assert start.startswith(prefix) and start[len(prefix):] == "storis_u_blogera"


def test_list_still_works_when_telegram_does_not_answer():
    base = asyncio.run(ad_links.bot_link_base(FakeBot(fail=True)))
    assert base is None
    data = ad_links._link_to_dict(link(), base)
    assert "url" not in data and data["code"] == "storis_u_blogera"


def _call_create(body, dao, bot=None, session=None):
    fn = ad_links.create_ad_link
    # Снимаем dishka-обёртку: вызываем исходную корутину с подделками.
    raw = fn.__dishka_orig_func__
    return asyncio.run(raw(body=body, _admin=None, ad_link_dao=dao, bot_service=bot or FakeBot(), session=session))


class FakeDao:
    def __init__(self):
        self.created = []

    async def get_by_code(self, code):
        return None

    async def create(self, dto):
        self.created.append(dto)
        return SimpleNamespace(id=5, name=dto.name, code=dto.code, is_active=True, created_at=None)


class FakeSession:
    def __init__(self):
        self.commits = 0

    async def commit(self):
        self.commits += 1


def test_create_rejects_code_telegram_cannot_carry_and_writes_nothing():
    dao, session = FakeDao(), FakeSession()
    body = ad_links.CreateAdLinkRequest(name="Реклама", code="сторис июнь")
    with pytest.raises(HTTPException) as exc:
        _call_create(body, dao, session=session)
    assert exc.value.status_code == 400
    assert dao.created == [] and session.commits == 0


def test_create_rejects_blank_name():
    body = ad_links.CreateAdLinkRequest(name="   ", code="ok_code")
    with pytest.raises(HTTPException) as exc:
        _call_create(body, FakeDao(), session=FakeSession())
    assert exc.value.status_code == 400


def test_create_trims_and_returns_ready_link():
    dao, session = FakeDao(), FakeSession()
    body = ad_links.CreateAdLinkRequest(name="  Сторис ", code=" story_1 ")
    data = _call_create(body, dao, session=session)
    assert dao.created[0].name == "Сторис" and dao.created[0].code == "story_1"
    assert session.commits == 1
    assert data["url"] == "https://t.me/example_bot?start=ad_story_1"
