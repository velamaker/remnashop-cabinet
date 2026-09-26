# shellcheck shell=bash
# Общее для scripts/db-backup.sh и scripts/db-restore-verify.sh: настройки из .env
# установки. Оба скрипта ходят из крона, а крон .env не читает — без этого включённое
# в .env шифрование бэкапа или отправка наружу молча не работали бы.
#
# Подключается (`. …/_env.sh`), а не запускается. ENV_FILE задаёт подключивший скрипт.
# Один файл на двоих — чтобы бэкап и проверка восстановления читали пароль ОДИНАКОВО:
# раньше проверка не снимала одинарные кавычки и не знала BACKUP_PASSPHRASE_FILE из
# .env, и бэкап, зашифрованный таким паролем, «не восстанавливался» при живом пароле.

# env_default КЛЮЧ — значение из .env, если в окружении ключ не задан (явное важнее).
# Последняя строка `КЛЮЧ=…`, кавычки вокруг значения (двойные или одинарные) снимаются.
env_default() {
    local key="$1" val
    [ -n "${!key:-}" ] && return 0
    [ -r "${ENV_FILE:-}" ] || return 0
    val="$(grep -E "^${key}=" "$ENV_FILE" 2>/dev/null | tail -1 | cut -d= -f2- | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//")" || true
    [ -n "$val" ] && printf -v "$key" '%s' "$val"
    return 0
}

# resolve_backup_passphrase — пароль шифрования бэкапа в BACKUP_PASSPHRASE (экспортирован).
# Порядок: явный BACKUP_PASSPHRASE → явный BACKUP_PASSPHRASE_FILE → то же самое из .env.
# Пусто — бэкап без шифрования.
resolve_backup_passphrase() {
    if [ -z "${BACKUP_PASSPHRASE:-}" ] && [ -z "${BACKUP_PASSPHRASE_FILE:-}" ]; then
        env_default BACKUP_PASSPHRASE
        [ -n "${BACKUP_PASSPHRASE:-}" ] || env_default BACKUP_PASSPHRASE_FILE
    fi
    BACKUP_PASSPHRASE="${BACKUP_PASSPHRASE:-}"
    if [ -z "$BACKUP_PASSPHRASE" ] && [ -n "${BACKUP_PASSPHRASE_FILE:-}" ]; then
        if [ -r "$BACKUP_PASSPHRASE_FILE" ]; then
            BACKUP_PASSPHRASE="$(cat "$BACKUP_PASSPHRASE_FILE")"
        else
            echo "$(date -Is) WARN: BACKUP_PASSPHRASE_FILE=$BACKUP_PASSPHRASE_FILE не читается — пароля нет" >&2
        fi
    fi
    # openssl читает пароль из ОКРУЖЕНИЯ процесса (-pass env:…): прочитанный из файла
    # или .env пароль был бы обычной переменной оболочки, и openssl его не увидел бы.
    # Экспортируем только его: токен бота дочерним процессам не нужен.
    export BACKUP_PASSPHRASE
}
