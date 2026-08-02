"""Контрактные тесты кабинета: гоняются против ЛЮБОГО бэкенда.

ЗАЧЕМ. Кабинет один, а бэкендов под ним уже два: наш (`remnashop`) и адаптер
поверх чужого бота «Бедолага». Оба обязаны отдавать один и тот же контракт —
`docs/CABINET-API-CONTRACT.md`. Проверка «отвечает ли 200» это не ловит: чужой
бэкенд легко отвечает 200 с трафиком в байтах вместо гигабайт, ценой в копейках
вместо рублей или с `null` вместо 401 — и кабинет молча врёт пользователю.
Поэтому здесь проверяется СООТВЕТСТВИЕ: набор обязательных полей, их типы,
ЕДИНИЦЫ ИЗМЕРЕНИЯ и формы ошибок.

ГЛАВНОЕ ПРАВИЛО. «Бэкенд не умеет раздел» — это НЕ провал. Честный 501 или
выключенный признак в `appearance.features` = раздел просто спрятан, кабинет это
переживает. Провал — это «умеет, но отвечает не по контракту». Разделять их
обязательно, иначе на чужом боте отчёт будет красным всегда и его перестанут
читать.

БЕЗОПАСНОСТЬ. Только GET плюс единственный POST /auth/login. Ни одной ручки,
которая создаёт, списывает, замораживает или отправляет. Скрипт безопасен на
боевой установке — там его и запускают без учётки, в режиме «только публичное».

ЗАПУСК
    # адаптер поверх «Бедолаги» (нужна тестовая учётка этого бэкенда)
    python3 tests/contract/run_contract.py \
        --base http://127.0.0.1:8090 \
        --email me@example.org --password '…'

    # наш боевой бэкенд — БЕЗ учётки: публичные пути + проверка формы 401
    python3 tests/contract/run_contract.py --base http://127.0.0.1:5000

    # то же через переменные окружения (удобно для cron/CI)
    CONTRACT_BASE=http://127.0.0.1:8090 \
    CONTRACT_EMAIL=… CONTRACT_PASSWORD=… python3 tests/contract/run_contract.py

Зависимости: только стандартная библиотека (pytest в системе нет — см. шапку
tests/contract/probe.py, почему это сделано намеренно).

КОДЫ ВОЗВРАТА: 0 — расхождений нет; 1 — есть расхождения с контрактом;
2 — проверку не удалось начать (бэкенд не отвечает, вход не прошёл).
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from contract_spec import BYTES, CASES, DAY, GB, ISO, PCT, RUB, RUB_STR, Case, F  # noqa: E402
from probe import Client, Reply  # noqa: E402

# --- уровни находок ----------------------------------------------------------

FAIL = "ПРОВАЛ"      # бэкенд заявляет, что умеет, но отвечает не по контракту
WARN = "СОМНИТЕЛЬНО"  # формально пройдёт, но выглядит как готовящаяся поломка
NOTE = "ЗАМЕТКА"      # различие, которое не ломает кабинет

# Порог «это не гигабайты, а байты». Лимит в 100 000 ГБ (100 ТБ) не бывает ни у
# кого, а 1 ГБ в байтах — это 1 073 741 824, то есть промах на четыре порядка.
GB_LOOKS_LIKE_BYTES = 100_000
# Порог «это не рубли, а копейки». Обычные суммы кабинета — сотни и тысячи;
# миллион рублей на балансе физлица возможен, поэтому это только СОМНИТЕЛЬНО.
RUB_LOOKS_LIKE_KOPEKS = 1_000_000
# Во сколько раз цена должна разойтись со «своей же» ценой из соседней ручки,
# чтобы это была не скидка, а перепутанная единица (копейки = ×100).
UNIT_MISMATCH_RATIO = 30

_DAY_RX = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class Finding:
    __slots__ = ("level", "where", "text")

    def __init__(self, level: str, where: str, text: str) -> None:
        self.level, self.where, self.text = level, where, text

    def __str__(self) -> str:
        return f"[{self.level}] {self.where}: {self.text}"


# --- проверка значения по описанию поля --------------------------------------

def _kind_ok(kind: str, value: Any) -> bool:
    if kind == "any":
        return True
    if kind == "bool":
        return isinstance(value, bool)
    # bool в питоне — подвид int, но «true» вместо числа это всегда ошибка.
    if kind == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "num":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if kind == "str":
        return isinstance(value, str)
    if kind == "list":
        return isinstance(value, list)
    if kind == "obj":
        return isinstance(value, dict)
    return True


def _as_number(value: Any) -> float | None:
    """Число из значения, каким бы оно ни пришло — числом или строкой «104.08»."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.replace(",", ".").strip())
        except ValueError:
            return None
    return None


def _check_unit(unit: str, value: Any, where: str, why: str) -> list[Finding]:
    """Единицы измерения — самая дорогая часть проверки, см. шапку contract_spec."""
    out: list[Finding] = []
    tail = f" ({why})" if why else ""

    if unit == GB:
        num = _as_number(value)
        if num is None:
            return out
        if num < 0:
            out.append(Finding(FAIL, where, f"лимит трафика отрицательный: {value}"))
        elif num > GB_LOOKS_LIKE_BYTES:
            out.append(Finding(
                FAIL, where,
                f"{value} — это похоже на БАЙТЫ, а контракт требует ГИГАБАЙТЫ{tail}. "
                "Кабинет переводит ГБ в байты сам (cabinet/src/lib/format.ts)",
            ))
    elif unit == BYTES:
        num = _as_number(value)
        if num is None:
            return out
        if num < 0:
            out.append(Finding(FAIL, where, f"расход трафика отрицательный: {value}"))
        elif isinstance(value, float) and not value.is_integer():
            out.append(Finding(
                WARN, where,
                f"{value} — дробное число байт. Обычно так выглядят гигабайты, "
                "не переведённые в байты",
            ))
    elif unit == RUB:
        num = _as_number(value)
        if num is None:
            out.append(Finding(FAIL, where, f"деньги не читаются как число: {value!r}"))
        elif num < 0:
            out.append(Finding(WARN, where, f"отрицательная сумма: {value}"))
        elif isinstance(value, int) and num >= RUB_LOOKS_LIKE_KOPEKS:
            out.append(Finding(
                WARN, where,
                f"{value} — подозрительно крупная сумма; у «Бедолаги» деньги в "
                "КОПЕЙКАХ, контракт требует РУБЛИ",
            ))
    elif unit == RUB_STR:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            out.append(Finding(
                WARN, where,
                f"деньги пришли числом ({value}), а контракт объявляет строку "
                "(cabinet/src/types/api.ts) — на разных бэкендах поле окажется разного типа",
            ))
        num = _as_number(value)
        if num is None:
            out.append(Finding(FAIL, where, f"деньги не читаются как число: {value!r}"))
        elif num < 0:
            out.append(Finding(WARN, where, f"отрицательная цена: {value}"))
        elif num >= RUB_LOOKS_LIKE_KOPEKS:
            out.append(Finding(WARN, where, f"{value} — похоже на копейки вместо рублей{tail}"))
    elif unit == ISO:
        if isinstance(value, str):
            try:
                datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                out.append(Finding(FAIL, where, f"не разбирается как дата ISO-8601: {value!r}"))
    elif unit == DAY:
        if isinstance(value, str) and not _DAY_RX.match(value):
            out.append(Finding(FAIL, where, f"ожидалась дата ГГГГ-ММ-ДД, пришло {value!r}"))
    elif unit == PCT:
        num = _as_number(value)
        if num is not None and not (0 <= num <= 100):
            out.append(Finding(WARN, where, f"проценты вне диапазона 0..100: {value}"))
    return out


def check_fields(spec: dict[str, F], data: Any, where: str) -> list[Finding]:
    """Рекурсивная проверка объекта по описанию полей."""
    out: list[Finding] = []
    if not isinstance(data, dict):
        return [Finding(FAIL, where, f"ожидался объект, пришло {type(data).__name__}")]

    for name, field in spec.items():
        place = f"{where}.{name}" if where else name
        if name not in data:
            if field.req:
                tail = f" — {field.why}" if field.why else ""
                out.append(Finding(FAIL, place, f"обязательного поля нет в ответе{tail}"))
            continue
        value = data[name]
        if value is None:
            if not field.null and field.req:
                tail = f" — {field.why}" if field.why else ""
                out.append(Finding(FAIL, place, f"поле пришло null{tail}"))
            continue
        if not _kind_ok(field.kind, value):
            out.append(Finding(
                FAIL, place,
                f"ожидался {field.kind}, пришло {type(value).__name__} ({value!r:.60})",
            ))
            continue
        if field.enum and isinstance(value, str) and value not in field.enum:
            out.append(Finding(
                WARN, place,
                f"значение {value!r} вне контракта; кабинет знает {', '.join(field.enum)}",
            ))
        if field.unit:
            out.extend(_check_unit(field.unit, value, place, field.why))

        # Вложенное: список объектов или объект. Проверяем ВСЕ элементы, но
        # одинаковые претензии схлопываем — иначе один битый список даёт сотню строк.
        if isinstance(field.items, dict):
            if isinstance(value, list):
                seen: set[tuple[str, str]] = set()
                for i, item in enumerate(value):
                    for f_ in check_fields(field.items, item, f"{place}[]"):
                        key = (f_.where, f_.text)
                        if key not in seen:
                            seen.add(key)
                            out.append(f_)
                    if i >= 200:  # защита от гигантских списков
                        break
            elif isinstance(value, dict):
                out.extend(check_fields(field.items, value, place))
    return out


# --- формы ошибок ------------------------------------------------------------

def check_error_shape(reply: Reply, where: str) -> list[Finding]:
    """Ошибка обязана быть FastAPI-образной: JSON с `detail`.

    Кабинет разбирает именно её (cabinet/src/api/client.ts): при другом теле он
    покажет пользователю кусок HTML или пустую строку вместо причины.
    """
    out: list[Finding] = []
    if not reply.is_json:
        out.append(Finding(WARN, where, f"ошибка не в JSON (content-type: {reply.content_type or 'пусто'})"))
        return out
    if not isinstance(reply.body, dict) or "detail" not in reply.body:
        out.append(Finding(WARN, where, f"в теле ошибки нет detail: {reply.short(60)}"))
        return out
    detail = reply.body["detail"]
    if not isinstance(detail, (str, list)):
        out.append(Finding(WARN, where, f"detail должен быть строкой или списком, пришло {type(detail).__name__}"))
    return out


# --- сквозные проверки -------------------------------------------------------
#
# Единицу измерения в одиночном ответе часто не поймать: балансу 0 всё равно,
# рубли это или копейки. Зато её видно на СТЫКЕ двух ручек, которые обязаны
# говорить об одном и том же одинаковыми числами.

def cross_checks(got: dict[str, Any]) -> list[Finding]:
    out: list[Finding] = []

    plans = (got.get("/plans/public") or {}).get("plans") or []
    offers = (got.get("/subscription/offers") or {}).get("plans") or []
    by_code = {str(p.get("public_code")): p for p in offers if isinstance(p, dict)}
    for plan in plans:
        if not isinstance(plan, dict):
            continue
        twin = by_code.get(str(plan.get("public_code")))
        if not twin:
            continue
        name = plan.get("public_code")
        # 1. Один и тот же тариф в витрине и в прайсе — один и тот же лимит.
        a, b = _as_number(plan.get("traffic_limit")), _as_number(twin.get("traffic_limit"))
        if a is not None and b is not None and a != b:
            out.append(Finding(
                FAIL, f"тариф {name}",
                f"лимит трафика разный: /plans/public отдаёт {a}, /subscription/offers — {b}. "
                "Одно из двух не в гигабайтах",
            ))
        # 2. «от N ₽ в месяц» и цена месячного срока обязаны быть одного порядка.
        monthly = _as_number(plan.get("monthly_from_rub"))
        month_price = None
        for dur in twin.get("durations") or []:
            if isinstance(dur, dict) and 28 <= (_as_number(dur.get("days")) or 0) <= 31:
                prices = dur.get("prices") or []
                if prices and isinstance(prices[0], dict):
                    month_price = _as_number(prices[0].get("final_amount"))
                break
        if monthly and month_price:
            ratio = max(monthly, month_price) / min(monthly, month_price)
            if ratio >= UNIT_MISMATCH_RATIO:
                out.append(Finding(
                    FAIL, f"тариф {name}",
                    f"цена месяца разошлась в {ratio:.0f} раз: /plans/public «от {monthly} ₽», "
                    f"/subscription/offers — {month_price}. Похоже на копейки против рублей",
                ))

    # 3. Лимит тарифа (ГБ) против расхода (байты) — единицы соседних полей РАЗНЫЕ,
    #    и перепутать их проще всего именно тут.
    sub = got.get("/subscription/current")
    if isinstance(sub, dict):
        limit = _as_number(sub.get("traffic_limit")) or 0
        used = _as_number(sub.get("used_traffic_bytes"))
        if limit > 0 and used and used > limit * (1024 ** 3) * 5:
            out.append(Finding(
                FAIL, "/subscription/current",
                f"расход {used} байт больше лимита {limit} ГБ в разы — "
                "либо лимит не в гигабайтах, либо расход не в байтах",
            ))
        # 4. Пробный период и текущая подписка меряют трафик одинаково.
        trial = got.get("/subscription/trial-info")
        if sub.get("is_trial") and isinstance(trial, dict):
            t_gb = _as_number(trial.get("traffic_gb"))
            if t_gb and limit and max(t_gb, limit) / min(t_gb, limit) > 1000:
                out.append(Finding(
                    WARN, "/subscription/trial-info",
                    f"traffic_gb={t_gb} против traffic_limit={limit} у той же пробной "
                    "подписки — единицы разъехались",
                ))
        # 5. Лимит устройств в подписке и на странице «Устройства» — одно число.
        devices = got.get("/subscription/devices")
        if isinstance(devices, dict):
            dl, mx = sub.get("device_limit"), devices.get("max_count")
            if isinstance(dl, int) and isinstance(mx, int) and dl and mx and dl != mx:
                out.append(Finding(
                    WARN, "/subscription/devices",
                    f"max_count={mx}, а в подписке device_limit={dl} — пользователь увидит разное",
                ))

    # 6. Колокольчик: список и счётчик обязаны совпадать, иначе бейдж врёт.
    feed, badge = got.get("/notifications"), got.get("/notifications/unread-count")
    if isinstance(feed, dict) and isinstance(badge, dict):
        if isinstance(feed.get("unread"), int) and isinstance(badge.get("unread"), int):
            if feed["unread"] != badge["unread"]:
                out.append(Finding(
                    WARN, "/notifications",
                    f"непрочитанных в ленте {feed['unread']}, а в счётчике {badge['unread']}",
                ))

    # 7. Счётчики нод должны сходиться со списком: на них построена плашка «всё работает».
    for path in ("/status", "/subscription/servers"):
        data = got.get(path)
        if not isinstance(data, dict) or not isinstance(data.get("nodes"), list):
            continue
        nodes = data["nodes"]
        online = sum(1 for n in nodes if isinstance(n, dict) and n.get("online"))
        if isinstance(data.get("total"), int) and data["total"] != len(nodes):
            out.append(Finding(WARN, path, f"total={data['total']}, а в списке {len(nodes)} нод"))
        if isinstance(data.get("online"), int) and data["online"] != online:
            out.append(Finding(WARN, path, f"online={data['online']}, а онлайн в списке {online}"))
        if nodes and data.get("all_operational") and online != len(nodes):
            out.append(Finding(
                WARN, path,
                f"all_operational=true, но онлайн {online} из {len(nodes)}",
            ))

    # 8. Публичный статус не имеет права отдавать адреса нод: страница открыта всем.
    status = got.get("/status")
    if isinstance(status, dict):
        leaked = [n.get("name") for n in status.get("nodes") or []
                  if isinstance(n, dict) and n.get("host")]
        if leaked:
            out.append(Finding(
                FAIL, "/status",
                f"в публичном ответе есть host у нод ({', '.join(map(str, leaked[:3]))}) — "
                "контракт запрещает отдавать адреса без авторизации",
            ))
    return out


# --- прогон ------------------------------------------------------------------

class Result:
    __slots__ = ("case", "verdict", "status", "note", "findings")

    def __init__(self, case: Case) -> None:
        self.case = case
        self.verdict = "готово"
        self.status = 0
        self.note = ""
        self.findings: list[Finding] = []


def run(base: str, prefix: str, email: str, password: str, timeout: float) -> int:
    anon = Client(base, prefix, timeout)
    print(f"бэкенд: {base}{prefix}")

    # Оформление тянем первым: без него бэкенд можно считать нерабочим, а его
    # `features` дальше отличают «раздел выключен» от «раздел сломан».
    appearance = anon.get("/appearance")
    if appearance.status == 0:
        print(f"НЕ ОТВЕЧАЕТ: {appearance.error}")
        return 2
    features: dict[str, Any] = {}
    if isinstance(appearance.body, dict) and isinstance(appearance.body.get("features"), dict):
        features = appearance.body["features"]
        off = sorted(k for k, v in features.items() if v is False)
        print(f"бэкенд объявил список возможностей; выключено: {', '.join(off) or '—'}")
    else:
        print("списка возможностей нет — бэкенд заявляет, что умеет всё")

    user = Client(base, prefix, timeout)
    if email and password:
        login = user.request("POST", "/auth/login", {"email": email, "password": password})
        if login.status != 200 or not user.has_session:
            print(f"вход не прошёл: {login.status} {login.short()}")
            print("без сессии проверить можно только публичное — прервано намеренно")
            return 2
        print(f"вход: {email} — сессия получена")
    else:
        print("учётка не задана: режим ТОЛЬКО ЧТЕНИЕ ПУБЛИЧНОГО "
              "(проверяем публичные пути и форму 401 на закрытых)")

    results: list[Result] = []
    bodies: dict[str, Any] = {}

    for case in CASES:
        res = Result(case)
        results.append(res)

        # 1. Заход без сессии — проверяем форму отказа. Это та самая грабля, из-за
        #    которой кабинет показывал «у вас нет подписки» вместо возврата на вход.
        guest = anon.get(case.url)
        if guest.status == 0:
            res.verdict = "нет связи"
            res.findings.append(Finding(FAIL, case.path, f"бэкенд не ответил: {guest.error}"))
            continue
        # 501 — наша договорённость «раздела нет», а не сбой: см. шапку файла.
        # Считать его пятисоткой нельзя, иначе любой чужой бэкенд красный целиком.
        if case.auth == "user":
            if guest.status == 200 and guest.body is not None:
                res.findings.append(Finding(
                    FAIL, case.path,
                    "без сессии отдаёт 200 с данными — кабинет решит, что данных нет, "
                    "вместо возврата на вход, а закрытые сведения видны гостю",
                ))
            elif guest.status == 403:
                res.findings.append(Finding(
                    WARN, case.path,
                    "без сессии 403 вместо 401 — кабинет не попробует обновить токен "
                    "и разлогинит на первом же протухшем access",
                ))
            elif guest.status >= 500 and guest.status != 501:
                res.findings.append(Finding(FAIL, case.path, f"без сессии {guest.status} — это ошибка сервера"))
            elif guest.status not in (200, 401, 501):
                res.findings.append(Finding(WARN, case.path, f"без сессии неожиданный код {guest.status}"))
            if 400 <= guest.status < 500:
                res.findings.extend(check_error_shape(guest, f"{case.path} без сессии"))
        else:  # public
            if guest.status == 401:
                res.findings.append(Finding(
                    FAIL, case.path,
                    "публичный путь требует сессию — гость не увидит лендинг/цены/статус",
                ))
            elif guest.status == 501:
                res.findings.append(Finding(
                    NOTE, case.path,
                    f"публичного источника нет: {_detail(guest) or 'бэкенд отвечает 501'}. "
                    "Соответствующий блок гостю просто не покажется",
                ))
            elif guest.status >= 500:
                res.findings.append(Finding(FAIL, case.path, f"{guest.status} — ошибка сервера на публичном пути"))
            elif guest.status >= 400:
                res.findings.append(Finding(WARN, case.path, f"публичный путь ответил {guest.status}"))
            elif guest.status == 200 and user.has_session:
                # Публичная витрина, которая наполняется только после входа, — это
                # пустой лендинг для всех, кто ещё не наш клиент. Формально контракт
                # соблюдён, поэтому заметка, а не провал.
                res.findings.extend(_guest_vs_user(case, guest, user))

        # 2. Заход с сессией (или тот же гостевой ответ, если публичный путь).
        reply = user.get(case.url) if (case.auth == "user" and user.has_session) else guest
        res.status = reply.status

        if reply.status == 501:
            res.verdict = "не умеет"
            res.note = _detail(reply) or "бэкенд честно сообщил, что раздела нет"
            _feature_note(res, features)
            continue
        if case.auth == "user" and not user.has_session:
            res.verdict = "пропущено"
            res.note = "нет сессии — проверена только форма отказа"
            _feature_note(res, features)
            continue
        if reply.status == 0:
            res.verdict = "нет связи"
            res.findings.append(Finding(FAIL, case.path, f"бэкенд не ответил: {reply.error}"))
            continue
        if reply.status >= 500:
            res.verdict = "провал"
            res.findings.append(Finding(
                FAIL, case.path,
                f"{reply.status} на чтении: {reply.short(70)}. 5xx — это не «данных нет», "
                "кабинет покажет ошибку поверх экрана",
            ))
            continue
        if reply.status != 200:
            res.verdict = "провал"
            res.findings.append(Finding(FAIL, case.path, f"ожидался 200, пришло {reply.status}: {reply.short(70)}"))
            res.findings.extend(check_error_shape(reply, case.path))
            continue

        if not reply.is_json:
            res.findings.append(Finding(FAIL, case.path, f"ответ не JSON (content-type: {reply.content_type or 'пусто'})"))
        elif reply.body is None and reply.raw.strip() not in (b"null", b""):
            res.findings.append(Finding(FAIL, case.path, f"тело не разбирается как JSON: {reply.short(70)}"))
        elif reply.body is None:
            if not case.nullable_body:
                res.findings.append(Finding(
                    FAIL, case.path,
                    "тело null. Контракт разрешает пустой ответ только там, где "
                    "сущности может не быть (подписка) — здесь кабинет ждёт объект",
                ))
        else:
            bodies[case.path] = reply.body
            res.findings.extend(check_fields(case.fields, reply.body, case.path))

        _feature_note(res, features)
        if any(f.level == FAIL for f in res.findings):
            res.verdict = "провал"

    cross = cross_checks(bodies)

    # --- отчёт ---------------------------------------------------------------
    print()
    mark = {"готово": "готово   ", "не умеет": "НЕ УМЕЕТ ", "пропущено": "пропущено",
            "провал": "ПРОВАЛ   ", "нет связи": "НЕТ СВЯЗИ"}
    for res in results:
        line = f"{mark[res.verdict]} {res.case.url:<38} {res.status or '—':<4}"
        if res.note:
            line += f" {res.note[:70]}"
        print(line)
        for f in res.findings:
            print(f"          └ [{f.level}] {f.where}: {f.text}")

    if cross:
        print("\nсквозные проверки (единицы измерения на стыке ручек):")
        for f in cross:
            print(f"          └ [{f.level}] {f.where}: {f.text}")

    everything = [f for r in results for f in r.findings] + cross
    fails = sum(1 for f in everything if f.level == FAIL)
    warns = sum(1 for f in everything if f.level == WARN)
    notes = sum(1 for f in everything if f.level == NOTE)
    ok = sum(1 for r in results if r.verdict == "готово")
    nope = sum(1 for r in results if r.verdict == "не умеет")
    skipped = sum(1 for r in results if r.verdict == "пропущено")

    print(
        f"\nитог: по контракту {ok} · не умеет (это нормально) {nope} · пропущено {skipped}"
        f"\n      расхождений {fails} · сомнительно {warns} · заметок {notes}"
    )
    if not fails:
        print("расхождений с контрактом нет")
    return 1 if fails else 0


def _guest_vs_user(case: Case, guest: Reply, user: Client) -> list[Finding]:
    """Публичный путь глазами гостя и глазами вошедшего.

    Ловит бэкенды, у которых публичной витрины нет вовсе и гостю отдаётся пустой
    список: 200, форма верная, а лендинг и страница «Тарифы» пустые.
    """
    if not isinstance(guest.body, dict):
        return []
    mine = user.get(case.url)
    if mine.status != 200 or not isinstance(mine.body, dict):
        return []
    out: list[Finding] = []
    for name, value in mine.body.items():
        if not isinstance(value, list) or not value:
            continue
        theirs = guest.body.get(name)
        if isinstance(theirs, list) and not theirs:
            out.append(Finding(
                NOTE, f"{case.path}.{name}",
                f"гостю приходит пустой список, вошедшему — {len(value)} шт. "
                "Публичная витрина будет пустой",
            ))
    return out


def _detail(reply: Reply) -> str:
    if isinstance(reply.body, dict) and isinstance(reply.body.get("detail"), str):
        return reply.body["detail"]
    return ""


def _feature_note(res: Result, features: dict[str, Any]) -> None:
    """Сверяем ответ с тем, что бэкенд САМ про себя заявил в appearance.features.

    Признак важнее кода ответа: по нему кабинет решает, рисовать ли раздел.
    Признак `true` при ответе 501 — это обещание, которого бэкенд не держит:
    пользователь увидит кнопку, которая приводит в ошибку.
    """
    key = res.case.feature
    if not key or key not in features:
        return
    declared = features[key]
    if declared is True and res.status == 501:
        res.findings.append(Finding(
            FAIL, res.case.path,
            f"appearance.features.{key}=true, но путь отвечает 501 — "
            "кабинет покажет раздел, который не работает",
        ))
        res.verdict = "провал"
    elif declared is False and res.status == 200:
        res.findings.append(Finding(
            NOTE, res.case.path,
            f"appearance.features.{key}=false, но путь работает — раздел спрятан "
            "(возможно, признаку нужны и другие ручки)",
        ))


def main() -> int:
    ap = argparse.ArgumentParser(description="Контрактные тесты кабинета против любого бэкенда")
    ap.add_argument("--base", default=os.environ.get("CONTRACT_BASE", "http://127.0.0.1:8090"),
                    help="адрес бэкенда, например http://127.0.0.1:8090 (адаптер) "
                         "или http://127.0.0.1:5000 (наш)")
    ap.add_argument("--prefix", default=os.environ.get("CONTRACT_PREFIX", "/api/v1/public"),
                    help="префикс путей бэкенда; кабинет ходит в /api, nginx переписывает сюда")
    # Учётки по умолчанию нет намеренно: файл лежит в публичном репозитории, а
    # значение по умолчанию — это опубликованный рабочий пароль.
    ap.add_argument("--email", default=os.environ.get("CONTRACT_EMAIL", ""))
    ap.add_argument("--password", default=os.environ.get("CONTRACT_PASSWORD", ""))
    ap.add_argument("--timeout", type=float, default=30.0)
    args = ap.parse_args()
    return run(args.base, args.prefix, args.email, args.password, args.timeout)


if __name__ == "__main__":
    sys.exit(main())
