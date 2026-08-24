"""Провайдер SDK панели с учётом её версии — подставляется вместо базового.

ЗАЧЕМ. В Remnawave 3.0 сменился контракт пользователя: убран `uuid`, всё
адресуется числовым `id`, удалены точечные поиски, DELETE отвечает 204 без тела,
`ip-control` переименован в `connections`. Правка каждого вызывающего места
означала бы два расходящихся набора кода, поэтому решение принимается ОДИН раз и
в одной точке — здесь: под 2.x отдаём ровно тот же `RemnawaveSDK`, что и раньше,
под 3.x — обёртку `RemnawaveSDKv3` с тем же интерфейсом
(см. src/infrastructure/services/remnawave_v3.py).

ВЕТКУ ВЫБИРАЕТ ПАНЕЛЬ, А НЕ КОНФИГ. Спрашиваем `/api/system/metadata` — маршрут,
который в 3.x не менялся и отдаёт `response.version`. Никакого тумблера в .env:
он рано или поздно разъедется с реальностью после апгрейда панели.

ПАНЕЛЬ НЕДОСТУПНА — НЕ ПАДАЕМ. Панель перезагружается чаще, чем бот: если её
недоступность на старте будет ронять контейнер, бот уйдёт в рестарт-петлю и не
поднимется даже тогда, когда панель вернётся. Поэтому при неудачном опросе берём
ветку 2.x (сегодняшнее поведение) и пишем в лог причину.

ПОЧЕМУ ФАЙЛ ЛЕЖИТ ЗДЕСЬ, А НЕ В ДЕРЕВЕ БОТА. Раньше overlay клал свою копию по
пути базового провайдера и тем самым замещал его файл целиком. Теперь класс живёт
у нас, а на место базового он встаёт подменой имени — см. apply() ниже. Исходники
бота при этом не меняются вовсе.
"""


import asyncio
from collections.abc import AsyncIterator
from typing import Optional

from dishka import Provider, Scope, provide
from httpx import AsyncClient, Timeout
from loguru import logger
from packaging.version import InvalidVersion, Version
from remnapy import RemnawaveSDK
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.core.config import AppConfig

from . import PatchTargetChanged

# Версия, начиная с которой у пользователя нет uuid и работает только числовой id.
_V3 = Version("3.0.0")

_PROBE_ATTEMPTS = 3
_PROBE_TIMEOUT = 3.0
_PROBE_RETRY_DELAY = 1.0


async def _detect_panel_version(client: AsyncClient) -> Optional[Version]:
    """Версия панели по `/system/metadata`; None — спросить не удалось.

    Три быстрые попытки с КОРОТКИМ таймаутом вместо клиентского (15/25 с). Причина в
    цене ошибки: угадать 2.x на живой 3.x — значит до перезапуска ходить по несуществующим
    маршрутам, так что пережить короткую перезагрузку панели стоит того. При этом длинный
    таймаут задержал бы старт бота на минуту, что тоже неприемлемо, — отсюда 3 с на
    попытку и 1 с пауза: худший случай около десяти секунд.
    """
    for attempt in range(1, _PROBE_ATTEMPTS + 1):
        try:
            response = await client.get("/system/metadata", timeout=_PROBE_TIMEOUT)
            response.raise_for_status()
            raw = (response.json() or {}).get("response", {}).get("version")
            return Version(str(raw))
        except InvalidVersion as e:
            # Версия пришла, но нечитаемая — повтор не поможет.
            logger.warning(f"Remnawave panel version is unparsable: '{e}'")
            return None
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"Failed to read Remnawave panel version "
                f"(attempt {attempt}/{_PROBE_ATTEMPTS}): '{e}'"
            )
            if attempt < _PROBE_ATTEMPTS:
                await asyncio.sleep(_PROBE_RETRY_DELAY)
    return None


class RemnawaveProvider(Provider):
    scope = Scope.APP

    @provide
    async def get_remnawave(
        self,
        config: AppConfig,
        session_maker: async_sessionmaker[AsyncSession],
    ) -> AsyncIterator[RemnawaveSDK]:
        logger.debug("Initializing RemnawaveSDK")

        headers = {}
        headers["Authorization"] = f"Bearer {config.remnawave.token.get_secret_value()}"
        headers["X-Api-Key"] = config.remnawave.caddy_token.get_secret_value()
        headers["CF-Access-Client-Id"] = config.remnawave.cf_client_id.get_secret_value()
        headers["CF-Access-Client-Secret"] = config.remnawave.cf_client_secret.get_secret_value()

        if not config.remnawave.is_external:
            headers["x-forwarded-proto"] = "https"
            headers["x-forwarded-for"] = "127.0.0.1"

        client = AsyncClient(
            base_url=f"{config.remnawave.url.get_secret_value()}/api",
            headers=headers,
            cookies=config.remnawave.cookies,
            verify=True,
            timeout=Timeout(connect=15.0, read=25.0, write=10.0, pool=5.0),
        )

        try:
            version = await _detect_panel_version(client)

            if version is None:
                logger.warning(
                    "Remnawave panel version unknown (panel unreachable at startup) — "
                    "assuming 2.x contract; restart the bot after the panel is upgraded"
                )
                yield RemnawaveSDK(client)
            elif version < _V3:
                # Штатный сегодняшний путь: ничего не меняем.
                yield RemnawaveSDK(client)
            else:
                # Импорт локальный: модуль совместимости тянет за собой
                # src.infrastructure.services (там весь пакет сервисов), и на 2.x
                # платить за этот импорт незачем.
                from src.infrastructure.services.remnawave_v3 import RemnawaveSDKv3

                logger.info(
                    f"Remnawave panel version '{version}' — enabling 3.x compatibility layer "
                    f"(numeric user ids, /users/stream, /connections)"
                )
                sdk = RemnawaveSDKv3(client, session_maker, str(version))
                # Отдаём карту наружу: её просит правка вебхуков (см. webhook_v3.py).
                from . import set_identity_map

                set_identity_map(sdk.identity)
                yield sdk
        finally:
            await client.aclose()
            logger.debug("RemnawaveSDK AsyncClient closed")


def apply() -> str:
    """Подставить наш провайдер вместо базового.

    База собирает список провайдеров функциями `get_aiogram_providers()` и
    `get_taskiq_providers()`, и обе создают `RemnawaveProvider()` по имени из
    своего пакета — то есть имя ищется в момент вызова, уже после нашей подмены.
    Значит достаточно переставить имя, а списки трогать не нужно.
    """
    import src.infrastructure.di.providers as providers

    current = getattr(providers, "RemnawaveProvider", None)
    if current is None:
        raise PatchTargetChanged(
            "в src.infrastructure.di.providers нет имени RemnawaveProvider — "
            "база перестроила сборку провайдеров, выбор SDK по версии панели пропадёт"
        )
    if current is RemnawaveProvider:
        return "уже подставлен"

    providers.RemnawaveProvider = RemnawaveProvider
    return "выбор SDK по версии панели (2.x или 3.x)"
