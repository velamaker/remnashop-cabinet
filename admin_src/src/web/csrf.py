"""Чистая логика CSRF-проверки (без FastAPI/DI — тестируется юнит-тестами).

Модель: защищаем ТОЛЬКО мутации, аутентифицированные НАШЕЙ cookie (браузерные сессии).
Запросы без нашей куки (платёжные вебхуки, серверные интеграции с собственной подписью)
не затрагиваются. Источник (Origin, иначе Referer) должен совпасть с доменом кабинета.
"""

from urllib.parse import urlsplit

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def host_of(url: str) -> str:
    return urlsplit(url).netloc.lower()


def csrf_blocked(
    method: str,
    path: str,
    has_auth_cookie: bool,
    origin: str | None,
    referer: str | None,
    allowed_hosts: set[str],
) -> bool:
    """True → запрос отклонить как CSRF.

    Пропускаем (False): безопасные методы; не-/api; запросы без нашей куки. Иначе требуем,
    чтобы хост Origin (или Referer) был в allowlist; нет источника или чужой → блок.
    """
    if method.upper() not in UNSAFE_METHODS:
        return False
    if "/api/" not in path:
        return False
    if not has_auth_cookie:
        return False

    src_host = None
    if origin and origin.lower() != "null":
        src_host = host_of(origin)
    elif referer:
        src_host = host_of(referer)

    if not src_host:
        return True
    if allowed_hosts and src_host not in allowed_hosts:
        return True
    return False
