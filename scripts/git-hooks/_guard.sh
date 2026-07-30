#!/usr/bin/env bash
# Общий список запретных к публикации путей (инцидент 2026-06-30: дампы БД в публичном GitHub).
# Читает пути со stdin, печатает запретные. См. incident-db-dumps-leaked в памяти проекта.
#
# Запрещено: дампы и базы (*.sql, *.sql.gz, *.dump, *.db), любые backup-*, секреты (.env и .env.*).
# Разрешено: .env.example.
forbidden_paths() {
  grep -E '(\.sql(\.gz)?$|\.dump$|\.db$|(^|/)backup-|(^|/)\.env($|\.)|\.bak($|\.)|\.orig$)' \
    | grep -vE '(^|/)\.env\.example$' \
    || true
}
