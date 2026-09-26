"""Синхрон панели не заводит двойников семейным профилям.

ЧТО МЕНЯЕМ. `SyncRemnaUser._execute` — место, где пользователь панели, не найденный
в боте, превращается в новую запись `users`. Синхрон панель→бот
(`SyncAllUsersFromPanel`, наш крон раз в 30 минут и кнопка «Импорт») зовёт его с
`creating=True` для КАЖДОГО пользователя панели без телеграма, которого нет среди
текущих подписок.

ЗАЧЕМ. Семейный профиль — пользователь панели `rs_fam_<id>` без телеграма. Пока
профиль живой, его находят по uuid через подписку теневого аккаунта, и синхрон
обновляет её как обычно. Но между «панель создала профиль» и «мы записали его
подписку» есть окно (и оно становится вечным, если процесс упал посередине), а
профиль, у которого строку уже удалили, остаётся в панели сиротой. В обоих случаях
синхрон завёл бы безымянный аккаунт с живой подпиской семьи — его никто не продлит,
не приостановит вместе с владельцем и не удалит. Недостроенный профиль доводит крон
семьи (по имени), о сироте он же сообщает владельцу бота. Синхрону здесь делать
нечего: он пропускает такого пользователя и пишет об этом в лог.

ПОЧЕМУ ОБЁРТКА, А НЕ КОПИЯ. Всё остальное — поиск по uuid и телеграму, импорт,
обновление подписки — остаётся кодом базы; от нас одна проверка до вызова оригинала.
Сверяем исходник всё равно: мы решаем за метод, кого НЕ заводить, и незамеченная
смена его логики (например, поиск по имени) сделала бы нашу ветку неверной.
"""

from __future__ import annotations

from typing import Any

from . import expect_source

# sha256 метода базы v0.8.2.
BASE_METHODS = {
    "SyncRemnaUser._execute": "31cc1693f0036ffabcb4f74e7aa630655ed874b419e949c52e19bd5300c1079f",
}

# Совпадает с overlay_family.USERNAME_PREFIX. Не импортируем: правка встаёт посреди
# импорта use-case'ов, и тянуть сюда сервисный пакет незачем ради одной строки.
FAMILY_PREFIX = "rs_fam_"


def is_family_orphan_candidate(remna_user: Any) -> bool:
    """Пользователь панели похож на семейный профиль: имя `rs_fam_*`, без телеграма."""
    name = getattr(remna_user, "username", None)
    return (
        isinstance(name, str)
        and name.startswith(FAMILY_PREFIX)
        and not getattr(remna_user, "telegram_id", None)
    )


def apply() -> str:
    from loguru import logger

    import src.application.use_cases.remnawave.commands.synchronization as target

    cls = target.SyncRemnaUser
    if getattr(cls, "_overlay_family_guard", False):
        return "уже применено"

    for qualname, sha in BASE_METHODS.items():
        expect_source(target, qualname, sha, qualname)

    original = cls._execute

    async def _execute(self: Any, actor: Any, data: Any) -> Any:
        remna_user = getattr(data, "remna_user", None)
        if getattr(data, "creating", False) and is_family_orphan_candidate(remna_user):
            # Тот же поиск, с которого начинает база: живой профиль найдётся по uuid
            # через подписку теневого аккаунта и пойдёт обычным путём обновления.
            found = await self.user_dao.get_by_remna_uuid(remna_user.uuid)
            if found is None:
                logger.info(
                    f"Overlay: синхрон пропустил семейный профиль '{remna_user.username}' "
                    "без строки в боте — его доводит крон семьи, двойника не заводим"
                )
                return False
        return await original(self, actor, data)

    _execute.__name__ = original.__name__
    _execute.__qualname__ = original.__qualname__
    _execute.__doc__ = original.__doc__
    cls._execute = _execute
    cls._overlay_family_guard = True
    return "пользователь панели rs_fam_* без строки в боте не превращается в новый аккаунт"
