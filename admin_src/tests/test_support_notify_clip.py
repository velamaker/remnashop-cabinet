"""Уведомление владельцу о тикете не должно пропадать из-за длины.

Тело обращения ограничено 4000 символами, а шапка (номер, подпись человека, тема)
и ссылка на админку добавляют ещё пару сотен. Telegram режет на 4096: сообщение
длиннее не обрезается, а НЕ ОТПРАВЛЯЕТСЯ вовсе — владелец не узнаёт об обращении.
С «паспортом обращения» (устройство + что не работает + комментарий) тикеты стали
длиннее, поэтому граница стала достижимой.
"""

from pathlib import Path

REL = "src/web/endpoints/public/support.py"


def _support_source() -> str:
    """Файл ручки: в образе — /opt/remnashop, в репозитории — admin_src.

    Импортировать модуль нельзя: он тянет fastapi/dishka и конфиг приложения, а
    проверяем мы чистую функцию обрезки. Поэтому читаем исходник как текст.
    """
    for base in (Path("/opt/remnashop"), Path(__file__).resolve().parents[1]):
        path = base / REL
        if path.exists():
            return path.read_text(encoding="utf-8")
    raise AssertionError(f"не нашёл {REL}")


def _load():
    """Модуль ручки тянет dishka/fastapi — берём из него только чистую функцию."""
    src = _support_source()
    start = src.index("TG_MESSAGE_LIMIT")
    end = src.index("async def _notify_owner")
    ns: dict = {}
    exec(compile(src[start:end], "support_clip", "exec"), ns)  # noqa: S102
    return ns


NS = _load()
clip = NS["_clip_for_telegram"]
LIMIT = NS["TG_MESSAGE_LIMIT"]


def test_короткое_уведомление_не_трогаем():
    msg = "🎫 Новый тикет #5\nОт: Александр\nТема: VPN не работает\n\nУстройство: iPhone"
    assert clip(msg) == msg


def test_длинное_укладывается_в_лимит():
    msg = "🎫 Новый тикет #5\nОт: Александр\n" + ("x" * 4500) + "\n\nОткрыть: https://site/admin/support"
    out = clip(msg)
    assert len(out) <= LIMIT


def test_сохраняем_начало_и_конец():
    head = "🎫 Новый тикет #5\nОт: Александр · @user · tg:1\nТема: VPN не работает"
    tail = "platform=ios · problems=instagram\n\nОткрыть: https://site/admin/support"
    out = clip(head + "\n\n" + "y" * 4500 + "\n\n" + tail)
    assert out.startswith(head)
    assert out.endswith(tail)
    assert "обрезано" in out


def test_граница_ровно_по_лимиту_не_режется():
    msg = "z" * LIMIT
    assert clip(msg) == msg
    assert len(clip("z" * (LIMIT + 1))) <= LIMIT
