"""Редактируемый шаблон письма с кодом подтверждения (правится из админки).

Хранится в assets/email_template.json (том переживает пересоздание контейнера).
Читается при отправке КАЖДОГО письма — изменения применяются сразу.

Подстановки в текстах: {brand} — имя из EMAIL_FROM_NAME, {code} — код,
{minutes} — срок действия кода в минутах.
"""

import json
import os
from pathlib import Path
from typing import Any

ASSETS_DIR = Path(os.environ.get("APP_ASSETS_DIR", "/opt/remnashop/assets"))
EMAIL_TEMPLATE_PATH = ASSETS_DIR / "email_template.json"

# Поля = редактируемые куски письма. Дефолт совпадает с прежним текстом.
EMAIL_TEMPLATE_DEFAULTS: dict[str, str] = {
    "subject": "Код подтверждения — {brand}",
    "heading": "Подтверждение почты",
    "intro": "Используйте этот код, чтобы подтвердить ваш email:",
    "expire_note": "Код действителен {minutes} минут.",
    "ignore_note": "Если вы не запрашивали подтверждение, просто проигнорируйте это письмо.",
}


def load_email_template() -> dict[str, str]:
    data = dict(EMAIL_TEMPLATE_DEFAULTS)
    try:
        if EMAIL_TEMPLATE_PATH.exists():
            with EMAIL_TEMPLATE_PATH.open(encoding="utf-8") as fh:
                stored = json.load(fh)
            if isinstance(stored, dict):
                for k in EMAIL_TEMPLATE_DEFAULTS:
                    v = stored.get(k)
                    if isinstance(v, str) and v.strip():
                        data[k] = v
    except Exception:
        # Битый файл не должен ломать отправку — отдаём дефолты.
        pass
    return data


def save_email_template(values: dict[str, Any]) -> dict[str, str]:
    data = load_email_template()
    for k in EMAIL_TEMPLATE_DEFAULTS:
        v = values.get(k)
        if v is not None:
            v = str(v).strip()
            # Пустое значение → возврат к дефолту этого поля.
            data[k] = v if v else EMAIL_TEMPLATE_DEFAULTS[k]
    EMAIL_TEMPLATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with EMAIL_TEMPLATE_PATH.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    return data


def fill(template: str, *, brand: str, code: str = "", minutes: str = "") -> str:
    """Подставляет {brand}/{code}/{minutes} без падений на других скобках."""
    return (
        template.replace("{brand}", brand)
        .replace("{code}", code)
        .replace("{minutes}", minutes)
    )
