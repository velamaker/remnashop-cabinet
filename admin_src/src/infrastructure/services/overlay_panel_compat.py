"""Ветвление по версии панели Remnawave: 2.x против 3.x — overlay.

Зачем модуль. В Remnawave 3.0 сменилась не только идентичность пользователя
(uuid → числовой id), но и СЕМАНТИКА части ручек: удаления и массовые операции
стали отвечать 204/202 БЕЗ тела, шесть полей настроек подписки переехали в
`customResponseHeaders`, legacy-ручка трафика удалена. Наш код обязан работать
и на боевой 2.8.1, и на 3.x, поэтому каждое такое место — двухветочное.

Почему версия определяется здесь, а не в каждом файле. Иначе на каждый запрос
кабинета уходил бы лишний GET /api/system/metadata, а ветки разъезжались бы по
разным правилам «что считать 3.x». Здесь один кэш на процесс и одно правило.

Почему при ошибке считаем «2.x». Боевая панель у владельца — 2.8.1, и требование
жёсткое: на ней поведение не должно измениться ни на байт. Недоступная метадата
не повод переключать логику на неизвестную ветку — fail-safe в СТАРОЕ поведение.

Кэш на процесс, без TTL. Версия панели меняется только при её апгрейде, а он и
так требует перезапуска наших контейнеров (образ/миграции), то есть кэш умрёт
вместе с процессом. Пере-опрашивать на каждом запросе смысла нет.
"""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Optional

from loguru import logger
from packaging.version import InvalidVersion, Version

# Граница веток. Всё, что >= 3.0.0 — «новая» панель: убран users.uuid, удалены
# поиски by-telegram-id/by-email/by-tag/by-id, DELETE отвечает 204 без тела,
# массовые операции — 202 без тела, ip-control переименован в connections.
PANEL_V3 = Version("3.0.0")

# Переопределение версии для проверок без живой 3.x панели (её у нас нет).
# В проде НЕ задаётся: пустая переменная = спрашиваем панель.
_VERSION_ENV = "REMNAWAVE_API_VERSION"

_cached: Optional[Version] = None
_forced_logged = False
_lock = asyncio.Lock()


def _parse(raw: Any) -> Optional[Version]:
    """'3.3.2' → Version. Мусор/None → None (значит «не знаем», ветка остаётся 2.x)."""
    if not raw:
        return None
    try:
        return Version(str(raw).strip().lstrip("v"))
    except (InvalidVersion, TypeError):
        return None


def _version_from_sdk(sdk: Any) -> Optional[Version]:
    """Версия, которую уже определил DI-провайдер, — по самому объекту SDK.

    Ветку выбирают один раз на старте: провайдер отдаёт либо настоящий
    `RemnawaveSDK` (панель 2.x либо её не удалось опросить), либо `RemnawaveSDKv3`
    со своей картой идентичности. Спрашивать панель второй раз незачем — ответ уже
    в руках, и он к тому же ГАРАНТИРОВАННО совпадает с тем контрактом, по которому
    сейчас ходит остальной код.

    Опознаём по `identity`: этот атрибут заводит только слой совместимости. Через
    isinstance нельзя — импорт слоя тянет весь пакет сервисов, а на 2.x его быть
    не должно.
    """
    if getattr(sdk, "identity", None) is None:
        return None
    return _parse(getattr(sdk, "panel_version", None)) or PANEL_V3


async def panel_version(sdk: Any) -> Optional[Version]:
    """Версия панели: сначала по объекту SDK, иначе GET /api/system/metadata.

    None = не удалось узнать. Вызывающий обязан трактовать None как 2.x —
    см. `panel_is_v3`.
    """
    global _cached, _forced_logged

    forced = _parse(os.environ.get(_VERSION_ENV))
    if forced is not None:
        if not _forced_logged:
            # Логируем громко и один раз: подменённая версия — диагностический
            # режим, и он не должен однажды тихо уехать в прод.
            _forced_logged = True
            logger.warning(
                f"{_VERSION_ENV}={forced} — версия панели взята из окружения, не с панели"
            )
        return forced

    from_sdk = _version_from_sdk(sdk)
    if from_sdk is not None:
        return from_sdk

    if _cached is not None:
        return _cached

    async with _lock:
        if _cached is not None:  # успели, пока ждали лок
            return _cached
        try:
            metadata = await sdk.system.get_metadata()
        except Exception as e:  # noqa: BLE001 — панель недоступна/токен протух
            logger.warning(f"panel_compat: не смог узнать версию панели ({type(e).__name__}: {e})")
            return None
        # remnapy разворачивает {"response": {...}} в объект, но у разных версий
        # SDK это то root-модель, то обычная — достаём и так, и так.
        data = getattr(metadata, "root", metadata)
        version = _parse(getattr(data, "version", None))
        if version is None:
            logger.warning("panel_compat: в metadata нет поля version — считаю панель 2.x")
            return None
        _cached = version
        logger.info(f"panel_compat: версия панели {version}")
        return _cached


async def panel_is_v3(sdk: Any) -> bool:
    """True только когда точно знаем, что панель >= 3.0.0."""
    version = await panel_version(sdk)
    return version is not None and version >= PANEL_V3


def reset_version_cache() -> None:
    """Сброс кэша — нужен тестам и ручной перепроверке после апгрейда панели."""
    global _cached, _forced_logged
    _cached = None
    _forced_logged = False


@asynccontextmanager
async def empty_body_ok(sdk: Any) -> AsyncIterator[None]:
    """Пустое тело успешного ответа = успех (только на 3.x).

    В 3.x DELETE-ручки отвечают 204, массовые операции — 202, и обе БЕЗ тела
    (в 2.x там были `{isDeleted:true}` / `{affectedRows:N}`). remnapy в
    `_handle_response` сначала делает `raise_for_status()`, и только потом
    `response.json()` — значит JSONDecodeError долетает ТОЛЬКО после успешного
    2xx, то есть ровно в случае «панель ответила ок, тела нет». Ошибки панели
    (4xx/5xx) сюда не попадают, они уже стали ApiError и пролетают наружу.

    На 2.x исключение пробрасываем как раньше: там тело есть всегда, и молча
    глотать его отсутствие означало бы прятать настоящую поломку.
    """
    is_v3 = await panel_is_v3(sdk)
    try:
        yield
    except json.JSONDecodeError:
        if not is_v3:
            raise
        # Ничего не логируем на уровне error: для 3.x это штатный ответ.
        logger.debug("panel_compat: 204/202 без тела — считаю успехом (панель 3.x)")


# Перевода «uuid → числовой id» здесь СОЗНАТЕЛЬНО нет.
#
# Он живёт ровно в одном месте — `RemnaIdentityMap` внутри слоя совместимости
# (infrastructure/services/remnawave_v3.py), который DI-провайдер подставляет
# вместо SDK, когда панель 3.x. Там перевод трёхступенчатый: снимок → арифметика
# синтетического uuid → довосстановление по нашей базе. Вторая копия этой логики
# в endpoint'ах умела бы только первую ступень и молча возвращала бы «не знаю» для
# всех, кто заведён уже на 3.x, — то есть тихо ломала бы им график трафика и
# удаление аккаунта. Поэтому endpoint'ы зовут SDK как раньше, uuid'ами.
