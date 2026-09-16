"""Мониторинг: пропавшее имя ноды и срок токена панели.

ДВЕ ДЫРЫ, ОБЕ ПРО МОЛЧАНИЕ — а молчание заметить труднее всего, поэтому и заперто
тестами, а не «проверено руками».

ПЕРВАЯ. `node_health` при неразрешимом имени не делал НИЧЕГО: не резолвится — идём
дальше. В состоянии при этом оставался прежний IP, и все проверки продолжали идти
по нему — мониторинг рапортовал о здоровье АДРЕСА, а не имени, по которому к ноде
ходит панель. На боевой установке так и вышло: DNS-запись одной из нод
исчезла, здесь было тихо, нода числилась здоровой, а панель молчала со своей
стороны — она держала уже установленное соединение и имя не перерезолвливала.
Вскрылось при её перезапуске.

ВТОРАЯ. За сроком API-токена панели не следило ничто. Токен — единственный ключ
ко всему: истечёт, и бот перестанет выдавать подписки, продлевать и видеть статус.
На боевой установке один токен уже протух незамеченным (не использовался).

Запуск — внутри образа бота:

  docker run --rm --env-file .env --network remnawave-network \
    -v /opt/remnashop/admin_src/tests:/tmp/tests:ro \
    remnashop-remnashop sh -c 'pip install -q --target /tmp/pylibs pytest pytest-asyncio \
      && PYTHONPATH=/tmp/pylibs python -m pytest /tmp/tests/test_node_dns_and_token.py \
         -v --asyncio-mode=auto'
"""

import base64
import importlib
import json
from datetime import datetime, timedelta, timezone

nh = importlib.import_module("src.infrastructure.taskiq.tasks.node_health")

# Время берём НАСТОЯЩЕЕ, а не фиксированное: возраст засечки функция считает по
# часам, и подсунутое «сейчас» из прошлого означало бы «сбой длится давно» —
# то есть тест проверял бы не то, что происходит в бою.
NOW = datetime.now(timezone.utc)
NOW_ISO = NOW.isoformat()
LONG_AGO = (NOW - timedelta(minutes=30)).isoformat()


def call(st, ip, now_iso=NOW_ISO, after_min=15):
    return nh.dns_alerts("Node-1", "node-1.example.com", st, ip, now_iso, after_min)


# ── имя ноды ────────────────────────────────────────────────────────────────

def test_first_failure_is_silent():
    """Разовый сбой резолвера будить владельца не должен."""
    st = {"ip": "203.0.113.10"}
    assert call(st, None) == []
    assert st["dns_down_since"] == NOW_ISO, "но засечка времени должна появиться"


def test_persistent_failure_alerts_once():
    """Имя не резолвится дольше порога — говорим. И только один раз."""
    st = {"ip": "203.0.113.10", "dns_down_since": LONG_AGO}
    out = call(st, None)
    assert len(out) == 1
    assert "больше не резолвится" in out[0]
    # Главное в этом тексте — не сам факт, а ПОСЛЕДСТВИЕ: проверки врут.
    assert "сохранённому адресу" in out[0] and "203.0.113.10" in out[0]
    assert st["dns_warned"] is True

    assert call(st, None) == [], "повторно не спамим"


def test_failure_without_saved_address_says_so():
    """Сохранённого адреса нет — проверять ноду нечем, и об этом надо сказать прямо."""
    st = {"dns_down_since": LONG_AGO}
    out = call(st, None)
    assert "проверить ноду сейчас нечем" in out[0]


def test_recovery_is_reported_and_state_cleared():
    """Имя вернулось — сообщаем и забываем, иначе следующая поломка промолчит.

    Двумя удачными прогонами, а не одним: одиночный успех при мерцающем резолве
    ничего не доказывает (см. test_flapping_dns_eventually_alerts).
    """
    st = {"ip": "203.0.113.10", "dns_down_since": LONG_AGO, "dns_warned": True}
    assert call(st, "203.0.113.10") == []
    out = call(st, "203.0.113.10")
    assert len(out) == 1 and "снова резолвится" in out[0]
    assert "dns_warned" not in st and "dns_down_since" not in st


def test_recovery_without_prior_alert_is_silent():
    """Сбой был короче порога — о «выздоровлении» молчим: владелец о нём не знал."""
    st = {"ip": "203.0.113.10", "dns_down_since": NOW_ISO}
    assert call(st, "203.0.113.10") == []
    assert call(st, "203.0.113.10") == []
    assert "dns_down_since" not in st


def test_ip_change_still_works():
    """Старое поведение не потеряно: смена адреса по-прежнему алертится."""
    st = {"ip": "198.51.100.20"}
    out = call(st, "203.0.113.10")
    assert len(out) == 1 and "сменился IP" in out[0]
    assert st["ip"] == "203.0.113.10"


def test_first_ever_resolve_is_not_a_change():
    """Первый замер — не «смена адреса»."""
    st = {}
    assert call(st, "203.0.113.10") == []
    assert st["ip"] == "203.0.113.10"


def test_ip_change_and_recovery_report_together():
    """Нода переехала, пока имя не резолвилось — сказать надо и то, и другое.

    Про смену адреса говорим СРАЗУ (это факт первого же удачного ответа), а про
    выздоровление — на втором подряд.
    """
    st = {"ip": "198.51.100.20", "dns_down_since": LONG_AGO, "dns_warned": True}
    first = call(st, "203.0.113.10")
    assert any("сменился IP" in m for m in first)
    second = call(st, "203.0.113.10")
    assert any("снова резолвится" in m for m in second)


def test_flapping_dns_eventually_alerts():
    """Мерцающий резолв — самый коварный случай, и раньше он молчал НАВСЕГДА.

    Имя отвечает через раз (сломано делегирование, один NS мёртв, SERVFAIL
    вперемешку). Любой удачный ответ стирал засечку, порог не накапливался, и
    владелец не узнавал ничего — при том что панель половину попыток проваливает.
    Теперь поломку забываем только после ДВУХ удачных подряд.
    """
    st = {"ip": "203.0.113.10", "dns_down_since": LONG_AGO}
    # провал — успех — провал: одиночный успех не должен обнулять засечку
    assert call(st, None) != []          # порог уже пройден, предупредили
    st.pop("dns_warned")                  # смотрим на саму засечку, не на дедуп
    assert call(st, "203.0.113.10") == []   # один успех — молчим, но и не забываем
    assert st.get("dns_down_since") == LONG_AGO, "засечка стёрлась при первом успехе"
    assert call(st, None) != [], "после мерцания предупреждение должно вернуться"


def test_two_good_runs_in_a_row_clear_the_failure():
    st = {"ip": "203.0.113.10", "dns_down_since": LONG_AGO, "dns_warned": True}
    first = call(st, "203.0.113.10")
    assert first == [] and "dns_down_since" in st, "одного успеха мало"
    second = call(st, "203.0.113.10")
    assert any("снова резолвится" in m for m in second)
    assert "dns_down_since" not in st and "dns_warned" not in st


def test_ipv6_only_name_is_not_declared_dead():
    """`_resolve_ip` спрашивает оба семейства адресов.

    С `family=AF_INET` имя с одной AAAA-записью выглядело неразрешимым, и после
    появления алерта про мёртвое имя такая нода объявлялась бы сломанной на ровном
    месте — причём сообщение о возвращении не пришло бы никогда.
    """
    import inspect

    src = inspect.getsource(nh._resolve_ip)
    assert "family=0" in src, "запрос ограничен одним семейством адресов"
    assert "family=socket.AF_INET" not in src

# ── срок токена панели ──────────────────────────────────────────────────────

def jwt(exp: datetime | None) -> str:
    """Токен панели в том виде, в каком он лежит в .env: HS256-JWT."""
    def b64(o):
        return base64.urlsafe_b64encode(json.dumps(o).encode()).rstrip(b"=").decode()
    payload = {"uuid": "x", "username": "bot", "role": "API"}
    if exp is not None:
        payload["exp"] = int(exp.timestamp())
    return f"{b64({'alg': 'HS256'})}.{b64(payload)}.подпись-не-проверяем"


def token_in(days: float) -> str:
    return jwt(datetime.now(timezone.utc) + timedelta(days=days, hours=1))


def test_days_left_is_read_from_the_token_itself():
    assert nh._token_days_left(token_in(45)) in (45, 46)


def test_expired_token_gives_negative():
    assert nh._token_days_left(jwt(datetime.now(timezone.utc) - timedelta(days=3))) < 0


def test_opaque_key_is_not_a_failure():
    """Не JWT (опаковый ключ) — сроку взяться неоткуда, но и падать не из-за чего."""
    assert nh._token_days_left("prosto-kluch-bez-tochek") is None
    assert nh._token_days_left("") is None


def test_token_without_exp_gives_none():
    assert nh._token_days_left(jwt(None)) is None


def test_garbage_never_raises():
    """Чужой формат не повод ронять весь мониторинг нод."""
    for junk in ("a.b.c", "...", "a.!!!.c", "x." + "y" * 5 + ".z"):
        assert nh._token_days_left(junk) is None


def test_every_threshold_speaks_once_as_the_date_approaches():
    """ЛЕСТНИЦА, А НЕ ОДНА СТУПЕНЬ — и это та самая ошибка, что дожила до боя.

    Пороги были сложены по УБЫВАНИЮ, а ступень выбирается первым подходящим, то
    есть `next()` всегда возвращал 30. Владелец получал ровно одно сообщение на
    тридцатом дне и больше не слышал ничего — до самого истечения токена, после
    которого магазин перестаёт выдавать подписки.

    Прежний тест проверял ПОРЯДОК КОРТЕЖА и утверждал, что верен убывающий, —
    то есть запирал ошибку как правило. Поэтому здесь проверяется ПОВЕДЕНИЕ.
    """
    state: dict = {}
    said = []
    for days in (45, 30, 20, 8, 7, 3, 1):
        out = nh.token_alerts(state, token_in(days))
        if out:
            said.append(days)
    assert said == [30, 7, 1], f"сообщения пришли на днях {said}, а ждали 30/7/1"


def test_same_step_does_not_repeat_every_five_minutes():
    state: dict = {}
    assert nh.token_alerts(state, token_in(20))
    for _ in range(5):
        assert nh.token_alerts(state, token_in(19)) == []


def test_expired_token_is_announced_once():
    state: dict = {}
    out = nh.token_alerts(state, jwt(datetime.now(timezone.utc) - timedelta(days=1)))
    assert len(out) == 1 and "ИСТЁК" in out[0]
    assert nh.token_alerts(state, jwt(datetime.now(timezone.utc) - timedelta(days=2))) == []


def test_new_token_resets_the_ladder():
    """Выпустили новый ключ — прошлые предупреждения не должны глушить будущие."""
    state: dict = {}
    nh.token_alerts(state, token_in(5))
    assert nh.token_alerts(state, token_in(400)) == []      # тревожиться не о чем
    assert nh.token_alerts(state, token_in(20))             # и ступени снова живы


def test_expired_then_renewed_speaks_again_when_needed():
    state: dict = {}
    nh.token_alerts(state, jwt(datetime.now(timezone.utc) - timedelta(days=1)))
    assert nh.token_alerts(state, token_in(400)) == []
    assert nh.token_alerts(state, jwt(datetime.now(timezone.utc) - timedelta(days=1)))


def test_thresholds_are_ascending_because_next_takes_the_first_match():
    """Порядок — часть контракта `next()`, поэтому заперт отдельно."""
    assert list(nh.TOKEN_WARN_DAYS) == sorted(nh.TOKEN_WARN_DAYS)
    assert min(nh.TOKEN_WARN_DAYS) >= 1, "в день истечения предупреждать поздно"
