"""Семейные профили: проводка — токен для кабинета, раздел прав, бэкап настроек.

Каждая строка здесь — место, которое при добавлении функции легко забыть, и
забытое не ломает ни один тест самой функции:
  * нет токена — кабинет, обновлённый отдельно от бота, спрячет «Семью» навсегда;
  * нет раздела прав — делегированный админ с «Настройками» получит 403;
  * нет файла в бэкапе — восстановленная установка молча выключит семью.
"""

import importlib

caps = importlib.import_module("src.web.cabinet_capabilities")


def test_capability_token_is_published():
    assert "family_profiles" in caps.CABINET_CAPABILITIES
    assert "family_profiles" in caps.public_capabilities()
    assert "family_profiles" not in caps.BOT_ONLY_CHANGES


def test_admin_page_belongs_to_settings_section():
    permissions = importlib.import_module("src.web.permissions")
    assert permissions.section_for_path("/api/v1/admin/family") == "settings"
    assert permissions.section_for_path("/api/v1/admin/family/plans/3") == "settings"


def test_family_config_is_part_of_settings_backup():
    settings_io = importlib.import_module("src.web.endpoints.admin.settings_io")
    assert "family.json" in settings_io.CONFIG_FILES
