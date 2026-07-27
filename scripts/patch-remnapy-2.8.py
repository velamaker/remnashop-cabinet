"""Патчи remnapy 2.7.0 для совместимости с панелью Remnawave 2.8+.

Панель 2.8 сменила ряд контрактов; модели remnapy 2.7.0 требуют старые поля как
обязательные → pydantic ValidationError на ответах панели. Правим модели IN-PLACE
в venv (кабинет эти поля либо не читает, либо читает через обходной путь):

- HWID (models/hwid.py): `userUuid` → `userId` (BigInt = users.t_id). Крашило меню
  «Устройства» бота и device-эндпоинты кабинета. Делаем оба поля опциональными.
- Hosts (models/hosts.py): панель переименовала `tag` → `tags` и сменила регистр
  `xHttpExtraParams` → `xhttpExtraParams` → `GetAllHostsResponseDto` падал, ломая
  «Статус сервиса» кабинета и список хостов в админке. Кабинет `tag`/xHttpExtraParams
  не использует (читает remark/address/inbound/isHidden) → делаем поля опциональными.

Запускается на СБОРКЕ образа (Dockerfile) venv-питоном — правки живут в образе и
переживают пересборку. Идемпотентно (по маркеру, независимо от варианта патча);
fail-closed: если исходный блок не найден (remnapy сменился) — билд падает, чтобы
несовместимость не ушла молча в рантайм.
"""

import glob
import os
import sys

import remnapy

BASE = os.path.dirname(remnapy.__file__)

# (файл, маркер-уже-применён, старый фрагмент, новый фрагмент)
PATCHES = [
    (
        "models/hwid.py",
        'user_id: Optional[int] = Field(None, alias="userId")',
        (
            "class HwidDeviceDto(BaseModel):\n"
            "    hwid: str\n"
            '    user_uuid: UUID = Field(alias="userUuid")'
        ),
        (
            "class HwidDeviceDto(BaseModel):\n"
            "    hwid: str\n"
            "    # Remnawave 2.8: userUuid → userId (BigInt); оба поля опциональны для\n"
            "    # совместимости со старым и новым контрактом панели.\n"
            '    user_uuid: Optional[UUID] = Field(None, alias="userUuid")\n'
            '    user_id: Optional[int] = Field(None, alias="userId")'
        ),
    ),
    (
        "models/hosts.py",
        'x_http_extra_params: Dict[str, Any] | None = Field(None, alias="xHttpExtraParams")',
        'x_http_extra_params: Dict[str, Any] | None = Field(alias="xHttpExtraParams")',
        'x_http_extra_params: Dict[str, Any] | None = Field(None, alias="xHttpExtraParams")',
    ),
    (
        "models/hosts.py",
        'tag: str | None = Field(None, alias="tag")',
        'tag: str | None = Field(alias="tag")',
        'tag: str | None = Field(None, alias="tag")',
    ),
]


def apply(rel: str, marker: str, old: str, new: str) -> None:
    path = os.path.join(BASE, rel)
    with open(path, encoding="utf-8") as f:
        src = f.read()

    if marker in src:
        print(f"  ~ {rel}: уже применён — пропускаю")
        return
    if old not in src:
        sys.exit(
            f"remnapy 2.8 patch: ожидаемый блок не найден в {rel} — версия remnapy "
            f"изменилась, проверь патч ({path})"
        )

    with open(path, "w", encoding="utf-8") as f:
        f.write(src.replace(old, new, 1))

    base = os.path.basename(rel)[:-3]  # без .py
    for pyc in glob.glob(os.path.join(os.path.dirname(path), "__pycache__", f"{base}.*.pyc")):
        os.remove(pyc)
    print(f"  ✓ {rel}: применён")


def main() -> None:
    for rel, marker, old, new in PATCHES:
        apply(rel, marker, old, new)


if __name__ == "__main__":
    main()
