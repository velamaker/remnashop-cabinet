"""Защита от SSRF для серверных fetch по URL из админ-конфига.

Админ задаёт URL (маршрутизация Happ, источник ссылок приложений), а бэкенд его тянет.
Без проверки это позволяет сканировать внутреннюю docker-сеть (remnashop-db, remnawave),
метаданные облака (169.254.169.254) и прочие внутренние сервисы. Пропускаем ТОЛЬКО URL,
чьи все резолвнутые адреса — публичные.
"""

import ipaddress
import socket
from urllib.parse import urlsplit


def is_safe_public_url(url: str) -> bool:
    """URL ведёт на публичный хост (не loopback/private/link-local/reserved/multicast)?

    Резолвим хост и проверяем ВСЕ адреса (у хоста может быть несколько A/AAAA-записей).
    Схема — только http/https. Остаточный вектор DNS-rebind (панель перерезолвит хост при
    connect) закрывается вызовом с follow_redirects=False + повторной проверкой цели.
    """
    try:
        parts = urlsplit(url)
    except Exception:
        return False
    if parts.scheme not in ("http", "https"):
        return False
    host = parts.hostname
    if not host:
        return False
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except Exception:
        return False
    if not infos:
        return False
    for info in infos:
        ip = info[4][0]
        try:
            addr = ipaddress.ip_address(ip.split("%", 1)[0])  # срезаем zone id у IPv6
        except ValueError:
            return False
        if (
            addr.is_private
            or addr.is_loopback
            or addr.is_link_local
            or addr.is_reserved
            or addr.is_multicast
            or addr.is_unspecified
        ):
            return False
    return True
