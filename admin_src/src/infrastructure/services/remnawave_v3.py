"""Совместимость с Remnawave 3.x: обёртка над RemnawaveSDK (overlay).

ЗАЧЕМ ЭТОТ ФАЙЛ
---------------
В Remnawave 3.0 у пользователя УБРАН `uuid`: миграция панели
`20260720132335_drop_user_uuid` дропает колонку `users.uuid`, и весь API переходит
на числовой `id` (тот же, что в 2.8 лежал в `users.t_id`). Заодно:

  • удалены поиски `/users/by-telegram-id/{id}`, `/users/by-email/{email}`,
    `/users/by-tag/{tag}`, `/users/by-id/{id}` — вместо них один
    `GET /users/stream` с query-фильтрами и курсорной постраничностью;
  • `POST /users` больше не принимает `uuid` и отвечает 201 вместо 200;
  • `DELETE /users/{id}` отвечает 204 БЕЗ ТЕЛА (было `{isDeleted:true}`),
    массовые операции — 202 без тела (было `affectedRows`);
  • `/hwid/devices/{userUuid}` → `/hwid/devices/{userId}`, в телах `userUuid` → `userId`;
  • модуль `ip-control` переименован в `connections` (`POST /connections/drop`, 202 без тела);
  • удалена legacy-ручка `/bandwidth-stats/users/{userUuid}/legacy` — остался только
    `GET /bandwidth-stats/users/{userId}` с «графиковым» ответом.

Опаснее всего третий пункт: код, читающий тело ответа DELETE, на 3.x сломается МОЛЧА —
пустой ответ распарсится в «не удалено», и мы будем считать удаление неуспешным.

ПОЧЕМУ ОБЁРТКА, А НЕ ПРАВКА ВЫЗЫВАЮЩИХ
--------------------------------------
Панель дёргают из ~20 мест (бот, кабинет, кроны, админка), и почти все ходят либо через
`RemnawaveImpl`, либо напрямую через `remnawave.sdk`. Переписывать их все — это два
разных набора кода на 2.x и 3.x и гарантированная рассинхронизация. Вместо этого
подменяется ОДИН объект: DI-провайдер отдаёт либо настоящий `RemnawaveSDK` (панель 2.x,
поведение остаётся байт в байт прежним), либо `RemnawaveSDKv3` — объект с тем же
интерфейсом, но ходящий по путям 3.x. Всё, что в 3.x не менялось (ноды, хосты, инбаунды,
сквады, system, настройки подписки), проваливается в настоящий SDK через `__getattr__`.

СИНТЕТИЧЕСКИЙ UUID
------------------
Наша база хранит именно uuid (`subscriptions.user_remna_id`, `reserve_grants.remna_uuid`,
`subscription_freezes.remna_uuid`), и менять её схему в этой задаче нельзя. Поэтому
обёртка обязана отдавать наверх DTO с полем `uuid` в прежнем виде. Перевод «uuid ↔ id»
делает `RemnaIdentityMap` (см. его docstring) — трёхступенчатый:

  1. снимок `remna_identity_map`, снятый на живой 2.8 (307 строк) — для СТАРЫХ юзеров,
     чьи настоящие uuid уже лежат в наших строках;
  2. арифметика — для юзеров, заведённых уже на 3.x: их uuid мы генерируем сами так,
     чтобы id читался обратно из самого uuid, без похода в базу;
  3. довосстановление по нашей же базе (короткий uuid из ссылки подписки / имя вида
     `rs_<telegram_id>`) через живые ручки панели — если снимок промахнулся.

Промах на всех трёх ступенях НЕ глотается: пишем в лог uuid и причину и бросаем
`NotFoundError` — ровно то, что вызывающий код уже умеет обрабатывать.

ПРОВЕРЕНО НА ЗАМОКАННЫХ ОТВЕТАХ. Живой панели 3.x у нас нет: пути, коды ответов и формы
тел сверены с официальной OpenAPI 3.3.2 и контрактом @remnawave/backend-contract.
"""

from __future__ import annotations

import asyncio
from typing import Any, Final, Optional, Union
from uuid import UUID

from httpx import AsyncClient
from loguru import logger
from remnapy import RemnawaveSDK
from remnapy.exceptions import ApiErrorResponse, NotFoundError, handle_api_error
from remnapy.models import (
    CreateUserHwidDeviceRequestDto,
    CreateUserHwidDeviceResponseDto,
    CreateUserRequestDto,
    CreateUserResponseDto,
    DeleteUserAllHwidDeviceRequestDto,
    DeleteUserHwidDeviceRequestDto,
    DeleteUserHwidDeviceResponseDto,
    DeleteUserResponseDto,
    DisableUserResponseDto,
    DropConnectionsRequestDto,
    DropConnectionsResponseDto,
    EmailUserResponseDto,
    EnableUserResponseDto,
    GetAllUsersResponseDto,
    GetStatsUserUsageResponseDto,
    GetUserAccessibleNodesResponseDto,
    GetUserByIdResponseDto,
    GetUserByShortUuidResponseDto,
    GetUserByUsernameResponseDto,
    GetUserByUuidResponseDto,
    GetUserHwidDevicesResponseDto,
    GetUserSubscriptionRequestHistoryResponseDto,
    GetUserUsageByRangeResponseDto,
    ResetUserTrafficResponseDto,
    ResolveUserRequestBodyDto,
    ResolveUserResponseDto,
    RevokeUserRequestDto,
    RevokeUserSubscriptionResponseDto,
    TagUserResponseDto,
    TelegramUserResponseDto,
    UpdateUserRequestDto,
    UpdateUserResponseDto,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


# ─────────────────────────────────────────────────────────────────────────────
# Синтетический uuid
# ─────────────────────────────────────────────────────────────────────────────

# Старшие 80 бит синтетического uuid — константа-метка, младшие 48 бит — числовой id
# панели. Такой uuid ОБРАТИМ: id достаётся из него арифметикой, без запроса в базу.
# Это и есть причина, по которой для юзеров, заведённых уже на 3.x, никакой таблицы
# сопоставления не нужно вообще — она нужна только для старых, чьи настоящие uuid
# панель успела забыть.
#
# Метка подобрана так, чтобы получившееся значение оставалось валидным UUID: в байте 6
# лежит 0x40 (версия 4), в байте 8 — 0x80 (вариант RFC 4122). В hex-виде такие uuid
# начинаются с «52573000-0000-4000-8000-» и в логах опознаются на глаз.
#
# Столкнуться со случайным uuid панели метка не может на практике: совпасть должны 74
# незафиксированных бита (2^-74). Порядок проверки ниже это ещё и подстраховывает —
# снимок из базы смотрим ПЕРВЫМ, арифметику вторым.
_SYNTHETIC_PREFIX: Final[int] = 0x52573000000040008000
_SYNTHETIC_ID_MASK: Final[int] = (1 << 48) - 1
_SYNTHETIC_ID_MAX: Final[int] = _SYNTHETIC_ID_MASK

# Размер страницы `/users/stream`: панель разрешает 1..1000. Берём максимум — поиск по
# telegramId/email отдаёт единицы записей, и лишние round-trip'ы тут не нужны.
_STREAM_PAGE_SIZE: Final[int] = 1000

# Сколько страниц `/users/stream` готовы пролистать в поиске. Фильтры точечные, так что
# это защита от бесконечного цикла при неисправном курсоре, а не рабочий предел.
_STREAM_MAX_PAGES: Final[int] = 50


def synthetic_uuid(panel_id: int) -> UUID:
    """Собрать обратимый uuid из числового id панели."""
    if panel_id < 0 or panel_id > _SYNTHETIC_ID_MAX:
        raise ValueError(f"panel_id '{panel_id}' не помещается в синтетический uuid")
    return UUID(int=(_SYNTHETIC_PREFIX << 48) | panel_id)


def synthetic_id(value: UUID) -> Optional[int]:
    """Вынуть числовой id из синтетического uuid; None — если uuid не наш."""
    if (value.int >> 48) != _SYNTHETIC_PREFIX:
        return None
    return value.int & _SYNTHETIC_ID_MASK


def _as_uuid(value: Union[str, UUID]) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


def _not_found(message: str) -> NotFoundError:
    """Ошибка в том же виде, что бросает сам SDK, — вызывающий код её уже ловит."""
    return NotFoundError(404, ApiErrorResponse(message=message, code="USER_NOT_FOUND"))


# ─────────────────────────────────────────────────────────────────────────────
# Транспорт
# ─────────────────────────────────────────────────────────────────────────────


async def _call(
    client: AsyncClient,
    method: str,
    path: str,
    *,
    params: Optional[dict[str, Any]] = None,
    json_body: Optional[dict[str, Any]] = None,
) -> Optional[Any]:
    """Запрос к панели поверх готового httpx-клиента SDK.

    Клиент уже несёт base_url `.../api`, токены и заголовки прокси — поэтому здесь
    только путь. Ошибки переводим тем же `handle_api_error`, что и SDK: вызывающий код
    ловит `NotFoundError`/`ConflictError`, и ветка 3.x не должна ломать эти except'ы.

    Пустое тело (204/202) отдаём как None — в 3.x это штатный ответ на удаление и на
    массовые операции, и молча превращать его в «не получилось» нельзя.
    """
    request = client.build_request(method, path, params=params, json=json_body)
    response = await client.send(request)

    if response.status_code >= 400:
        handle_api_error(response)
        response.raise_for_status()  # страховка: сюда попасть не должны

    if response.status_code == 204 or not response.content:
        return None

    return response.json()


def _unwrap(payload: Optional[Any]) -> Any:
    """Снять конверт `{"response": ...}`, которым панель оборачивает все ответы."""
    if isinstance(payload, dict) and "response" in payload:
        return payload["response"]
    return payload


# ─────────────────────────────────────────────────────────────────────────────
# Карта идентичности
# ─────────────────────────────────────────────────────────────────────────────


class RemnaIdentityMap:
    """Перевод «uuid нашей базы ↔ числовой id панели» для 3.x.

    Снимок `remna_identity_map` грузим в память ЦЕЛИКОМ и один раз за процесс: строк
    там столько же, сколько было пользователей на момент апгрейда (сотни), а ходить в
    базу на каждый рендер пользователя нельзя — список юзеров разом тянет тысячу.
    Идентичность неизменна, так что кэш не протухает.

    В базу ПИШЕМ только то, что не выводится арифметикой, — по сути один случай:
    создание пользователя с заранее заданным uuid (так пересоздаётся юзер под уже
    существующую подписку). Панель 3.x свой uuid принять не может и выдаёт новый id,
    поэтому пару «наш uuid ↔ выданный id» надо запомнить, иначе строка подписки
    осиротеет. Синтетические пары не пишем: они и так восстанавливаются из самого uuid.
    """

    def __init__(self, session_maker: async_sessionmaker[AsyncSession], client: AsyncClient) -> None:
        self._session_maker = session_maker
        self._client = client
        self._by_uuid: dict[UUID, int] = {}
        self._by_id: dict[int, UUID] = {}
        self._loaded = False
        self._lock = asyncio.Lock()

    # ── снимок ───────────────────────────────────────────────────────────────

    async def _ensure_loaded(self) -> None:
        if self._loaded:
            return

        async with self._lock:
            if self._loaded:
                return

            try:
                async with self._session_maker() as session:
                    rows = (
                        await session.execute(
                            text("SELECT panel_uuid, panel_id FROM remna_identity_map")
                        )
                    ).all()
            except Exception as exc:  # noqa: BLE001
                # Таблицы может не быть (миграция не прогнана) или база недоступна.
                # Это не повод падать: синтетические uuid и довосстановление по имени
                # продолжат работать. Но знать об этом надо — пишем ошибкой.
                logger.error(
                    f"remnawave-3x: снимок remna_identity_map не прочитан ({exc}); "
                    f"старые uuid будут восстанавливаться только через панель"
                )
                self._loaded = True
                return

            for panel_uuid, panel_id in rows:
                key = _as_uuid(panel_uuid)
                self._by_uuid[key] = int(panel_id)
                self._by_id[int(panel_id)] = key

            self._loaded = True
            logger.info(f"remnawave-3x: снимок идентичности загружен, записей: {len(self._by_uuid)}")

    def _cache(self, panel_uuid: UUID, panel_id: int) -> None:
        """Запомнить пару в обе стороны.

        НА ОДИН ЧИСЛОВОЙ id МОЖЕТ УКАЗЫВАТЬ НЕСКОЛЬКО НАШИХ uuid, и это норма: у
        человека остаётся старая строка подписки, а рядом появляется новая, либо
        строка резервного доступа ссылается на того же пользователя панели. Поэтому
        прямое направление (uuid → id) только ДОПОЛНЯЕТСЯ: выбрасывать «предыдущий»
        uuid нельзя — он всё ещё лежит и в нашей базе, и в таблице карты, и после
        такой чистки операция по нему падала бы «не сопоставлен с id панели» при
        живом и прекрасно находимом пользователе.

        Обратное направление (id → uuid) однозначно по построению, поэтому там
        побеждает последняя привязка — тот uuid, которым код пользуется сейчас.
        """
        self._by_uuid[panel_uuid] = panel_id
        self._by_id[panel_id] = panel_uuid

    # ── id → uuid ────────────────────────────────────────────────────────────

    async def to_uuid(self, panel_id: int) -> UUID:
        """uuid, который ждёт наверху вызывающий код.

        Снимок ПЕРВЫЙ: у старых юзеров наши строки ссылаются на их настоящий uuid, и
        подменить его синтетическим значит порвать связь с подпиской.
        """
        await self._ensure_loaded()

        known = self._by_id.get(int(panel_id))
        if known is not None:
            return known

        return synthetic_uuid(int(panel_id))

    def is_snapshot_uuid(self, value: UUID) -> bool:
        """Лежит ли пара в таблице (а не выводится арифметикой) — её надо обновлять."""
        return value in self._by_uuid

    # ── uuid → id ────────────────────────────────────────────────────────────

    async def to_id(self, value: Union[str, UUID]) -> int:
        """Числовой id панели по нашему uuid.

        Порядок: снимок → арифметика → свежая строка карты → довосстановление по
        нашей базе через живые ручки панели. Не нашли — громко в лог и NotFoundError.
        """
        try:
            key = _as_uuid(value)
        except (ValueError, AttributeError, TypeError) as exc:
            logger.error(f"remnawave-3x: '{value}' — не uuid, id не определить ({exc})")
            raise _not_found(f"Invalid user uuid '{value}'") from exc

        await self._ensure_loaded()

        known = self._by_uuid.get(key)
        if known is not None:
            return known

        derived = synthetic_id(key)
        if derived is not None:
            return derived

        # Снимок читается один раз за процесс, а процессов у нас четыре (бот, веб,
        # воркер, планировщик) плюс вторая копия веба. Пару, записанную СОСЕДНИМ
        # процессом после нашего старта, в памяти не найти — спрашиваем таблицу
        # точечно, прежде чем идти в тяжёлое довосстановление через панель.
        fresh = await self._lookup(key)
        if fresh is not None:
            return fresh

        recovered = await self._recover(key)
        if recovered is not None:
            return recovered

        logger.error(
            f"remnawave-3x: uuid '{key}' не сопоставлен с id панели — нет в снимке "
            f"remna_identity_map, не синтетический, и восстановить по нашей базе "
            f"(короткий uuid подписки / имя в панели) не удалось"
        )
        raise _not_found(f"Cannot map uuid '{key}' to Remnawave user id")

    async def _lookup(self, key: UUID) -> Optional[int]:
        """Точечный запрос в таблицу карты — на случай записи от соседнего процесса."""
        try:
            async with self._session_maker() as session:
                row = (
                    await session.execute(
                        text(
                            "SELECT panel_id FROM remna_identity_map "
                            "WHERE panel_uuid = CAST(:uuid AS uuid)"
                        ),
                        {"uuid": str(key)},
                    )
                ).first()
        except Exception as exc:  # noqa: BLE001 — таблицы может не быть, база могла отпасть
            logger.warning(f"remnawave-3x: точечный поиск '{key}' в карте не удался: {exc}")
            return None

        if row is None:
            return None

        panel_id = int(row[0])
        self._cache(key, panel_id)
        return panel_id

    async def _recover(self, key: UUID) -> Optional[int]:
        """Довосстановление промаха: наша база → живая ручка панели → запомнить.

        ТОЛЬКО ПО КОРОТКОМУ UUID. Имя в панели (`rs_<telegram_id>`) сюда просится, но
        оно опознаёт ЧЕЛОВЕКА, а не ту запись панели, на которую ссылалась наша строка.
        Репетиция на копии боевой базы показала цену этой разницы: из 317 наших uuid
        десять — осколки старых подписок людей, которых в панели давно завели заново.
        По имени они «восстанавливались» и указывали на ДЕЙСТВУЮЩУЮ запись владельца
        (четыре разных наших uuid сошлись на одном id), то есть операция по мёртвой
        строке — снятие доступа, удаление, выдача резерва — прилетела бы живой подписке.
        Сегодня такая строка честно получает 404, и это правильное поведение.

        Короткий uuid уникален для КОНКРЕТНОЙ записи панели и меняется при отзыве
        подписки, поэтому совпадение по нему — совпадение записи, а не однофамильца.
        Легитимный случай, ради которого ступень и нужна (человек заведён на 2.8.x уже
        ПОСЛЕ снятия снимка), им покрывается полностью: ссылка подписки у него свежая.
        """
        hints = await self._hints(key)
        if hints is None:
            logger.warning(
                f"remnawave-3x: uuid '{key}' не найден ни в снимке, ни в наших таблицах "
                f"(subscriptions / reserve_grants / subscription_freezes)"
            )
            return None

        short_uuid = hints
        if not short_uuid:
            logger.warning(
                f"remnawave-3x: у uuid '{key}' нет сохранённой ссылки подписки — "
                f"опознать запись панели нечем"
            )
            return None

        try:
            found = _unwrap(
                await _call(self._client, "GET", f"/users/by-short-uuid/{short_uuid}")
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                f"remnawave-3x: восстановление '{key}' по shortUuid={short_uuid} "
                f"не вышло: {exc}"
            )
            return None

        panel_id = (found or {}).get("id")
        if panel_id is None:
            return None

        panel_id = int(panel_id)
        logger.warning(
            f"remnawave-3x: uuid '{key}' отсутствовал в снимке и восстановлен "
            f"по shortUuid={short_uuid} → id={panel_id}; пара сохранена"
        )
        await self.remember(
            key,
            panel_id,
            username=found.get("username"),
            short_uuid=found.get("shortUuid"),
        )
        return panel_id

    async def _hints(self, key: UUID) -> Optional[str]:
        """Короткий uuid ЭТОЙ записи — из сохранённой у нас ссылки подписки.

        Имя в панели тут не строится сознательно: см. docstring `_recover` — оно
        опознаёт человека, а не запись, и уводит старые строки на чужую подписку.
        """
        # ДВА разных параметра под одно и то же значение — не дублирование, а
        # необходимость. `subscriptions.user_remna_id` имеет тип uuid, а
        # `reserve_grants.remna_uuid` и `subscription_freezes.remna_uuid` — varchar.
        # С одним параметром Postgres выводит его тип по первому употреблению
        # (`CAST(:x AS uuid)` → uuid) и дальше отказывается сравнивать varchar с uuid:
        # «no operator matches the given name and argument types». Запрос падал бы
        # целиком, а вместе с ним — вся третья ступень восстановления.
        sql = text(
            """
            SELECT
                u.id,
                u.telegram_id,
                (
                    SELECT s.url FROM subscriptions s
                    WHERE s.user_remna_id = CAST(:uuid AS uuid)
                    ORDER BY s.id DESC LIMIT 1
                ) AS sub_url
            FROM users u
            WHERE u.id = COALESCE(
                (
                    SELECT s.user_id FROM subscriptions s
                    WHERE s.user_remna_id = CAST(:uuid AS uuid)
                    ORDER BY s.id DESC LIMIT 1
                ),
                (
                    SELECT r.user_id FROM reserve_grants r
                    WHERE r.remna_uuid = :uuid_text ORDER BY r.id DESC LIMIT 1
                ),
                (
                    SELECT f.user_id FROM subscription_freezes f
                    WHERE f.remna_uuid = :uuid_text LIMIT 1
                )
            )
            LIMIT 1
            """
        )

        try:
            async with self._session_maker() as session:
                row = (
                    await session.execute(sql, {"uuid": str(key), "uuid_text": str(key)})
                ).first()
        except Exception as exc:  # noqa: BLE001
            logger.error(f"remnawave-3x: подсказки для '{key}' из базы не получены: {exc}")
            return None

        if row is None:
            return None

        _user_id, _telegram_id, sub_url = row
        return str(sub_url or "").rstrip("/").rsplit("/", 1)[-1]

    # ── запись ───────────────────────────────────────────────────────────────

    async def remember(
        self,
        panel_uuid: UUID,
        panel_id: int,
        *,
        username: Optional[str] = None,
        short_uuid: Optional[str] = None,
    ) -> None:
        """Сохранить пару, которую нельзя вывести арифметикой.

        В память кладём в любом случае — процесс должен продолжать работать, даже если
        база не приняла запись. В базу пишем с явным commit: overlay-код живёт вне
        UnitOfWork, автокоммита тут нет.
        """
        await self._ensure_loaded()
        self._cache(panel_uuid, panel_id)

        sql = text(
            """
            INSERT INTO remna_identity_map (panel_uuid, panel_id, username, short_uuid)
            VALUES (CAST(:uuid AS uuid), :panel_id, :username, :short_uuid)
            ON CONFLICT (panel_uuid) DO UPDATE SET
                panel_id = EXCLUDED.panel_id,
                username = COALESCE(EXCLUDED.username, remna_identity_map.username),
                short_uuid = COALESCE(EXCLUDED.short_uuid, remna_identity_map.short_uuid)
            """
        )

        try:
            async with self._session_maker() as session:
                await session.execute(
                    sql,
                    {
                        "uuid": str(panel_uuid),
                        "panel_id": int(panel_id),
                        "username": username,
                        "short_uuid": short_uuid,
                    },
                )
                await session.commit()
        except Exception as exc:  # noqa: BLE001
            logger.error(
                f"remnawave-3x: пара uuid='{panel_uuid}' ↔ id={panel_id} НЕ сохранена "
                f"в remna_identity_map ({exc}); после перезапуска связь потеряется"
            )


# ─────────────────────────────────────────────────────────────────────────────
# users
# ─────────────────────────────────────────────────────────────────────────────


class _UsersV3:
    """`sdk.users` для панели 3.x — те же имена и сигнатуры, другие пути."""

    def __init__(self, real: Any, identity: RemnaIdentityMap) -> None:
        self._real = real
        self._client: AsyncClient = real.client
        self._identity = identity

    def __getattr__(self, name: str) -> Any:
        # Всё, чего мы не переопределили (теги и прочее без идентичности), — как было.
        return getattr(self._real, name)

    # ── общее ────────────────────────────────────────────────────────────────

    async def _with_uuid(self, raw: dict[str, Any], *, known: Optional[UUID] = None) -> dict[str, Any]:
        """Вернуть панельному юзеру поле `uuid`, которого в 3.x больше нет."""
        panel_id = int(raw["id"])
        value = known if known is not None else await self._identity.to_uuid(panel_id)
        return {**raw, "uuid": str(value)}

    async def _user(self, raw: dict[str, Any], model: Any, *, known: Optional[UUID] = None) -> Any:
        return model.model_validate(await self._with_uuid(raw, known=known))

    async def _users(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [await self._with_uuid(row) for row in rows]

    async def _stream(self, **filters: Any) -> list[dict[str, Any]]:
        """`/users/stream` — единственная замена удалённым точечным поискам.

        Курсорная постраничность: `nextCursor` из ответа кладём в следующий запрос,
        пока панель говорит `hasMore`.
        """
        params: dict[str, Any] = {"size": _STREAM_PAGE_SIZE}
        params.update({k: v for k, v in filters.items() if v is not None})

        collected: list[dict[str, Any]] = []
        for _ in range(_STREAM_MAX_PAGES):
            page = _unwrap(await _call(self._client, "GET", "/users/stream", params=params)) or {}
            collected.extend(page.get("users") or [])

            cursor = page.get("nextCursor")
            if not page.get("hasMore") or cursor is None:
                return collected
            params = {**params, "cursor": cursor}

        logger.warning(
            f"remnawave-3x: /users/stream не закончился за {_STREAM_MAX_PAGES} страниц "
            f"(фильтры: {filters}) — отдаём то, что успели собрать"
        )
        return collected

    # ── чтение ───────────────────────────────────────────────────────────────

    async def get_user_by_uuid(
        self, uuid: Union[str, UUID]
    ) -> GetUserByUuidResponseDto:
        key = _as_uuid(uuid)
        panel_id = await self._identity.to_id(key)
        raw = _unwrap(await _call(self._client, "GET", f"/users/{panel_id}"))
        # uuid отдаём ровно тот, о котором спросили: вызывающий сравнивает его со
        # своей строкой в базе.
        return await self._user(raw, GetUserByUuidResponseDto, known=key)

    async def get_user_by_id(self, id: Union[str, int]) -> GetUserByIdResponseDto:
        raw = _unwrap(await _call(self._client, "GET", f"/users/{int(id)}"))
        return await self._user(raw, GetUserByIdResponseDto)

    async def get_user_by_short_uuid(self, short_uuid: str) -> GetUserByShortUuidResponseDto:
        raw = _unwrap(await _call(self._client, "GET", f"/users/by-short-uuid/{short_uuid}"))
        return await self._user(raw, GetUserByShortUuidResponseDto)

    async def get_user_by_username(self, username: str) -> GetUserByUsernameResponseDto:
        raw = _unwrap(await _call(self._client, "GET", f"/users/by-username/{username}"))
        return await self._user(raw, GetUserByUsernameResponseDto)

    async def get_users_by_telegram_id(
        self, telegram_id: Union[str, int]
    ) -> TelegramUserResponseDto:
        rows = await self._stream(telegramId=str(telegram_id))
        return TelegramUserResponseDto.model_validate(await self._users(rows))

    async def get_users_by_email(self, email: str) -> EmailUserResponseDto:
        rows = await self._stream(email=email)
        return EmailUserResponseDto.model_validate(await self._users(rows))

    async def get_users_by_tag(self, tag: str) -> TagUserResponseDto:
        rows = await self._stream(tag=tag)
        return TagUserResponseDto.model_validate(await self._users(rows))

    async def get_all_users(
        self, start: Optional[int] = None, size: Optional[int] = None
    ) -> GetAllUsersResponseDto:
        params = {k: v for k, v in {"start": start, "size": size}.items() if v is not None}
        page = _unwrap(await _call(self._client, "GET", "/users", params=params or None)) or {}
        return GetAllUsersResponseDto.model_validate(
            {"users": await self._users(page.get("users") or []), "total": page.get("total", 0)}
        )

    async def get_user_accessible_nodes(
        self, uuid: Union[str, UUID]
    ) -> GetUserAccessibleNodesResponseDto:
        panel_id = await self._identity.to_id(uuid)
        raw = _unwrap(await _call(self._client, "GET", f"/users/{panel_id}/accessible-nodes"))
        return GetUserAccessibleNodesResponseDto.model_validate(raw)

    async def get_user_subscription_request_history(
        self, uuid: Union[str, UUID]
    ) -> GetUserSubscriptionRequestHistoryResponseDto:
        panel_id = await self._identity.to_id(uuid)
        raw = _unwrap(
            await _call(self._client, "GET", f"/users/{panel_id}/subscription-request-history")
        )
        return GetUserSubscriptionRequestHistoryResponseDto.model_validate(raw)

    async def resolve_user(self, body: ResolveUserRequestBodyDto) -> ResolveUserResponseDto:
        payload = body.model_dump(exclude_unset=True, by_alias=True, mode="json")
        # В 3.x поля uuid нет — переводим его в id, остальное панель принимает как было.
        wanted = payload.pop("uuid", None)
        if wanted is not None and "id" not in payload:
            payload["id"] = await self._identity.to_id(wanted)

        raw = _unwrap(await _call(self._client, "POST", "/users/resolve", json_body=payload))
        known = _as_uuid(wanted) if wanted is not None else None
        return await self._user(raw, ResolveUserResponseDto, known=known)

    # ── запись ───────────────────────────────────────────────────────────────

    async def create_user(self, body: CreateUserRequestDto) -> CreateUserResponseDto:
        """Создание. В 3.x свой uuid задать НЕЛЬЗЯ — запоминаем выданный id.

        Мы зовём создание с готовым uuid, когда пересоздаём пользователя под уже
        существующую подписку (`subscription.user_remna_id`). Панель 3.x это поле не
        принимает и выдаёт собственный id, поэтому единственный способ не порвать
        строку подписки — записать пару «наш uuid ↔ выданный id» в remna_identity_map.
        """
        payload = body.model_dump(exclude_unset=True, by_alias=True, mode="json")
        wanted_raw = payload.pop("uuid", None)
        wanted = _as_uuid(wanted_raw) if wanted_raw is not None else None

        raw = _unwrap(await _call(self._client, "POST", "/users", json_body=payload))
        panel_id = int(raw["id"])

        if wanted is not None:
            await self._identity.remember(
                wanted,
                panel_id,
                username=raw.get("username"),
                short_uuid=raw.get("shortUuid"),
            )

        return await self._user(raw, CreateUserResponseDto, known=wanted)

    async def update_user(self, body: UpdateUserRequestDto) -> UpdateUserResponseDto:
        payload = body.model_dump(exclude_unset=True, by_alias=True, mode="json")
        wanted_raw = payload.pop("uuid", None)
        wanted = _as_uuid(wanted_raw) if wanted_raw is not None else None

        if wanted is not None:
            payload["id"] = await self._identity.to_id(wanted)
        elif "username" not in payload:
            raise _not_found("Update requires either uuid or username")

        raw = _unwrap(await _call(self._client, "PATCH", "/users", json_body=payload))
        return await self._user(raw, UpdateUserResponseDto, known=wanted)

    async def delete_user(self, uuid: Union[str, UUID]) -> DeleteUserResponseDto:
        """Удаление. В 3.x — 204 БЕЗ ТЕЛА.

        Вызывающий читает `response.is_deleted`; если отдать сюда пустоту, удаление
        молча посчитается неудачным. Раз панель ответила 2xx (а не 404 — тот прилетит
        исключением из `_call`), значит юзера больше нет: отвечаем True.
        """
        panel_id = await self._identity.to_id(uuid)
        await _call(self._client, "DELETE", f"/users/{panel_id}")
        return DeleteUserResponseDto.model_validate({"isDeleted": True})

    async def enable_user(self, uuid: Union[str, UUID]) -> EnableUserResponseDto:
        key = _as_uuid(uuid)
        panel_id = await self._identity.to_id(key)
        raw = _unwrap(await _call(self._client, "POST", f"/users/{panel_id}/actions/enable"))
        return await self._user(raw, EnableUserResponseDto, known=key)

    async def disable_user(self, uuid: Union[str, UUID]) -> DisableUserResponseDto:
        key = _as_uuid(uuid)
        panel_id = await self._identity.to_id(key)
        raw = _unwrap(await _call(self._client, "POST", f"/users/{panel_id}/actions/disable"))
        return await self._user(raw, DisableUserResponseDto, known=key)

    async def reset_user_traffic(self, uuid: Union[str, UUID]) -> ResetUserTrafficResponseDto:
        key = _as_uuid(uuid)
        panel_id = await self._identity.to_id(key)
        raw = _unwrap(await _call(self._client, "POST", f"/users/{panel_id}/actions/reset-traffic"))
        return await self._user(raw, ResetUserTrafficResponseDto, known=key)

    async def revoke_user_subscription(
        self,
        uuid: Union[str, UUID],
        body: Optional[RevokeUserRequestDto] = None,
    ) -> RevokeUserSubscriptionResponseDto:
        key = _as_uuid(uuid)
        panel_id = await self._identity.to_id(key)
        payload = (
            body.model_dump(exclude_unset=True, by_alias=True, mode="json") if body else None
        )
        raw = _unwrap(
            await _call(
                self._client, "POST", f"/users/{panel_id}/actions/revoke", json_body=payload
            )
        )
        # Отзыв меняет короткий uuid — обновляем его в снимке, чтобы довосстановление
        # по ссылке подписки не искало старое значение. Синтетические пары в таблице
        # не лежат и трогать их незачем.
        if self._identity.is_snapshot_uuid(key):
            await self._identity.remember(key, panel_id, short_uuid=raw.get("shortUuid"))
        return await self._user(raw, RevokeUserSubscriptionResponseDto, known=key)


# ─────────────────────────────────────────────────────────────────────────────
# hwid
# ─────────────────────────────────────────────────────────────────────────────


class _HwidV3:
    """`sdk.hwid` для 3.x: путь и тела переехали с `userUuid` на числовой `userId`."""

    def __init__(self, real: Any, identity: RemnaIdentityMap) -> None:
        self._real = real
        self._client: AsyncClient = real.client
        self._identity = identity

    def __getattr__(self, name: str) -> Any:
        # Статистика и топы идентичности не касаются — отдаём настоящему контроллеру.
        return getattr(self._real, name)

    def _devices(self, raw: Optional[dict[str, Any]], owner: UUID) -> dict[str, Any]:
        """Дополняем устройства полем `userUuid`.

        Панель его больше не отдаёт (в 2.8 уже отдавала `userId`), но код, который
        когда-то на него смотрел, здесь получит осмысленное значение вместо None —
        мы владельца и так знаем, потому что сами его спрашивали.
        """
        data = raw or {"total": 0, "devices": []}
        devices = [{**d, "userUuid": str(owner)} for d in (data.get("devices") or [])]
        return {"total": data.get("total", len(devices)), "devices": devices}

    async def get_hwid_user(self, uuid: Union[str, UUID]) -> GetUserHwidDevicesResponseDto:
        key = _as_uuid(uuid)
        panel_id = await self._identity.to_id(key)
        raw = _unwrap(await _call(self._client, "GET", f"/hwid/devices/{panel_id}"))
        return GetUserHwidDevicesResponseDto.model_validate(self._devices(raw, key))

    async def add_hwid_to_users(
        self, body: CreateUserHwidDeviceRequestDto
    ) -> CreateUserHwidDeviceResponseDto:
        payload = body.model_dump(exclude_unset=True, by_alias=True, mode="json")
        key = _as_uuid(payload.pop("userUuid"))
        payload["userId"] = await self._identity.to_id(key)
        raw = _unwrap(await _call(self._client, "POST", "/hwid/devices", json_body=payload))
        return CreateUserHwidDeviceResponseDto.model_validate(self._devices(raw, key))

    async def delete_hwid_to_user(
        self, body: DeleteUserHwidDeviceRequestDto
    ) -> DeleteUserHwidDeviceResponseDto:
        payload = body.model_dump(exclude_unset=True, by_alias=True, mode="json")
        key = _as_uuid(payload.pop("userUuid"))
        payload["userId"] = await self._identity.to_id(key)
        raw = _unwrap(await _call(self._client, "POST", "/hwid/devices/delete", json_body=payload))
        return DeleteUserHwidDeviceResponseDto.model_validate(self._devices(raw, key))

    async def delete_all_hwid_user(
        self, body: DeleteUserAllHwidDeviceRequestDto
    ) -> DeleteUserHwidDeviceResponseDto:
        payload = body.model_dump(exclude_unset=True, by_alias=True, mode="json")
        key = _as_uuid(payload.pop("userUuid"))
        payload["userId"] = await self._identity.to_id(key)
        raw = _unwrap(
            await _call(self._client, "POST", "/hwid/devices/delete-all", json_body=payload)
        )
        return DeleteUserHwidDeviceResponseDto.model_validate(self._devices(raw, key))


# ─────────────────────────────────────────────────────────────────────────────
# connections (бывший ip-control)
# ─────────────────────────────────────────────────────────────────────────────


class _ConnectionsV3:
    """`sdk.ip_control` для 3.x: модуль переименован в `connections`.

    Имя атрибута оставляем прежним (`ip_control`) — иначе пришлось бы править все
    вызывающие места, а смысл обёртки ровно в том, чтобы их не трогать.
    """

    def __init__(self, real: Any, identity: RemnaIdentityMap) -> None:
        self._real = real
        self._client: AsyncClient = real.client
        self._identity = identity

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)

    async def drop_connections(
        self, body: DropConnectionsRequestDto
    ) -> DropConnectionsResponseDto:
        """`POST /connections/drop`, 202 БЕЗ ТЕЛА.

        Селектор `userUuids` в 3.x стал `userIds` с числами; выбор нод не менялся.
        Ответ пустой, а вызывающий ждёт `eventSent` — раз панель приняла (2xx),
        событие отправлено.
        """
        payload = body.model_dump(exclude_unset=True, by_alias=True, mode="json")
        drop_by = dict(payload.get("dropBy") or {})

        if drop_by.get("by") == "userUuids":
            uuids = drop_by.pop("userUuids", []) or []
            drop_by = {
                "by": "userIds",
                "userIds": [await self._identity.to_id(u) for u in uuids],
            }
            payload["dropBy"] = drop_by

        await _call(self._client, "POST", "/connections/drop", json_body=payload)
        return DropConnectionsResponseDto.model_validate({"eventSent": True})


# ─────────────────────────────────────────────────────────────────────────────
# bandwidth-stats
# ─────────────────────────────────────────────────────────────────────────────


class _BandwidthStatsV3:
    """`sdk.bandwidthstats` для 3.x: адресация по числовому id, legacy-ручки нет."""

    # Сколько нод просим в разбивке. Ручка ограничивает «топ», а нам для истории по дням
    # нужны все — берём заведомо больше, чем узлов у нас бывает.
    _TOP_NODES: Final[int] = 100

    def __init__(self, real: Any, identity: RemnaIdentityMap) -> None:
        self._real = real
        self._client: AsyncClient = real.client
        self._identity = identity

    def __getattr__(self, name: str) -> Any:
        # Ноды и реалтайм идентичности не касаются — как было.
        return getattr(self._real, name)

    async def _user_usage(
        self, uuid: Union[str, UUID], start: str, end: str, top_nodes_limit: int
    ) -> dict[str, Any]:
        panel_id = await self._identity.to_id(uuid)
        return (
            _unwrap(
                await _call(
                    self._client,
                    "GET",
                    f"/bandwidth-stats/users/{panel_id}",
                    params={"start": start, "end": end, "topNodesLimit": top_nodes_limit},
                )
            )
            or {}
        )

    async def get_stats_user_usage(
        self,
        uuid: Union[str, UUID],
        top_nodes_limit: int,
        start: str,
        end: str,
    ) -> GetStatsUserUsageResponseDto:
        data = await self._user_usage(uuid, start, end, top_nodes_limit)
        return GetStatsUserUsageResponseDto.model_validate(data)

    async def get_user_usage_legacy_old(
        self,
        user_uuid: str,
        start: str,
        end: str,
    ) -> GetUserUsageByRangeResponseDto:
        """Замена удалённой legacy-ручки — разворачиваем «графиковый» ответ в строки.

        В 3.x `/bandwidth-stats/users/{userUuid}/legacy` нет вовсе. Живая ручка отдаёт
        подписи периодов (`categories`) и по ряду на ноду (`series[].data`) — из этого
        однозначно собираются те же записи «дата + нода + байты», которые раньше
        приходили готовыми. Вызывающий (график истории трафика в кабинете) остаётся
        нетронутым.
        """
        key = _as_uuid(user_uuid)
        data = await self._user_usage(key, start, end, self._TOP_NODES)

        categories = data.get("categories") or []
        rows: list[dict[str, Any]] = []

        for node in data.get("series") or []:
            node_uuid = node.get("uuid")
            node_name = node.get("name")
            for index, total in enumerate(node.get("data") or []):
                if index >= len(categories):
                    break
                rows.append(
                    {
                        "userUuid": str(key),
                        "nodeUuid": node_uuid,
                        "nodeName": node_name,
                        "total": int(total or 0),
                        "date": str(categories[index]),
                    }
                )

        return GetUserUsageByRangeResponseDto.model_validate(rows)


# ─────────────────────────────────────────────────────────────────────────────
# Сам SDK
# ─────────────────────────────────────────────────────────────────────────────


class RemnawaveSDKv3(RemnawaveSDK):
    """`RemnawaveSDK` для панели 3.x.

    Наследуемся от настоящего SDK, а не оборачиваем его: так объект остаётся
    `RemnawaveSDK` и для аннотаций, и для isinstance, и все контроллеры, которых
    смена идентичности не коснулась (ноды, хосты, инбаунды, сквады, system, настройки
    подписки), достаются даром и остаются ЕДИНСТВЕННЫМ источником правды.

    Подменяем ровно четыре: `users`, `hwid`, `ip_control`, `bandwidthstats` — только их
    маршруты и тела переехали с uuid на числовой id.
    """

    def __init__(
        self,
        client: AsyncClient,
        session_maker: async_sessionmaker[AsyncSession],
        panel_version: Optional[str] = None,
    ) -> None:
        super().__init__(client=client)

        self.panel_version = panel_version
        self.identity = RemnaIdentityMap(session_maker, client)

        # Настоящие контроллеры, собранные базовым __init__, отдаём шимам: непокрытые
        # методы они проксируют туда же.
        self.users = _UsersV3(self.users, self.identity)
        self.hwid = _HwidV3(self.hwid, self.identity)
        self.ip_control = _ConnectionsV3(self.ip_control, self.identity)
        self.bandwidthstats = _BandwidthStatsV3(self.bandwidthstats, self.identity)
