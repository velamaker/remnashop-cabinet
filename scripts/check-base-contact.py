#!/usr/bin/env python3
"""Сколько overlay соприкасается с исходниками бота — и не выросло ли это молча.

ЗАЧЕМ. Overlay кладётся поверх базового образа одной строкой Dockerfile
(`COPY admin_src/src/ /opt/remnashop/src/`). Файл, путь которого совпал с базовым,
не дополняет его, а ЗАМЕЩАЕТ целиком. Пока апстрим стоит на месте, это незаметно.
В день, когда выйдет новая версия базы, каждая такая копия молча откатит всё, что
авторы бота поправили в этом файле, — без единой строчки в логе.

Проверка делает две вещи, и вторая важнее первой:

  1. Считает контакт: какие файлы базы мы замещаем, а что является чистым
     добавлением (добавления безопасны — базы они не касаются вовсе).
  2. Сверяет КАЖДЫЙ замещённый файл с его записью в `admin_src/BASE-CONTACT.json`
     по sha256 базовой версии. Апстрим тронул файл — проверка падает и называет
     его поимённо: значит, там появились чужие правки, которые наша копия
     затрёт, и их надо перенести руками, а потом обновить хэш в манифесте.

Замещение без записи в манифесте — тоже отказ: список контакта заводится
осознанно, а не «случайно совпало имя файла».

    ./scripts/check-base-contact.py            # тег базы из Dockerfile
    ./scripts/check-base-contact.py v0.8.3     # проверить перед бампом базы

Выход: 0 — контакт совпал с манифестом; 1 — расхождение (подробности в выводе).
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OVERLAY = ROOT / "admin_src" / "src"
MANIFEST = ROOT / "admin_src" / "BASE-CONTACT.json"
IMAGE = "ghcr.io/snoups/remnashop"

GRN, RED, YLW, RST = "\033[32m", "\033[31m", "\033[33m", "\033[0m"


def ok(msg: str) -> None:
    print(f"{GRN}✓{RST} {msg}")


def bad(msg: str) -> None:
    print(f"{RED}✗ {msg}{RST}", file=sys.stderr)


def info(msg: str) -> None:
    print(f"{YLW}➜{RST} {msg}")


def base_tag_from_dockerfile() -> str:
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    m = re.search(r"^ARG BASE_TAG=(\S+)", text, re.M)
    if not m:
        bad("в Dockerfile не нашёлся ARG BASE_TAG")
        sys.exit(1)
    return m.group(1)


def extract_base_src(tag: str, dest: Path) -> None:
    """Достать `src/` базового образа, не запуская его."""
    ref = f"{IMAGE}:{tag}"
    if subprocess.run(["docker", "image", "inspect", ref],
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode != 0:
        info(f"тяну {ref}")
        subprocess.run(["docker", "pull", "-q", ref], check=True, stdout=subprocess.DEVNULL)

    cid = subprocess.run(["docker", "create", ref, "true"],
                         check=True, capture_output=True, text=True).stdout.strip()
    try:
        subprocess.run(["docker", "cp", f"{cid}:/opt/remnashop/src", str(dest)],
                       check=True, stdout=subprocess.DEVNULL)
    finally:
        subprocess.run(["docker", "rm", "-f", cid], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    tag = sys.argv[1] if len(sys.argv) > 1 else base_tag_from_dockerfile()
    info(f"базовый образ: {IMAGE}:{tag}")

    if not MANIFEST.exists():
        bad(f"нет манифеста {MANIFEST.relative_to(ROOT)}")
        return 1
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    declared = {e["path"]: e for e in manifest.get("files", [])}

    tmp = Path(tempfile.mkdtemp(prefix="base-contact-"))
    try:
        base_src = tmp / "src"
        extract_base_src(tag, base_src)

        base_files = {f"src/{p.relative_to(base_src)}"
                      for p in base_src.rglob("*.py")}
        our_files = {f"src/{p.relative_to(OVERLAY)}"
                     for p in OVERLAY.rglob("*.py")
                     if "__pycache__" not in p.parts}

        replaced = sorted(base_files & our_files)
        added = sorted(our_files - base_files)

        print()
        ok(f"чистых добавлений: {len(added)} — базы не касаются")
        info(f"замещаем файлов базы: {len(replaced)}")

        problems: list[str] = []

        # 1. Замещение, о котором манифест не знает.
        for path in replaced:
            if path not in declared:
                problems.append(
                    f"{path}: замещает файл базы, но в манифесте его нет.\n"
                    f"      Либо переименуйте наш файл (тогда это станет добавлением),\n"
                    f"      либо заведите запись в {MANIFEST.name} с причиной."
                )

        # 2. Апстрим тронул файл, который мы замещаем, — наша копия его затрёт.
        for path, entry in sorted(declared.items()):
            src = base_src / path[len("src/"):]
            if not src.exists():
                problems.append(
                    f"{path}: в базе {tag} такого файла БОЛЬШЕ НЕТ.\n"
                    f"      Наша копия останется висеть мёртвым грузом — проверьте, "
                    f"куда переехала эта логика."
                )
                continue
            now = sha256(src)
            if now != entry["base_sha256"]:
                problems.append(
                    f"{path}: апстрим ИЗМЕНИЛ этот файл в {tag}.\n"
                    f"      Наша копия затрёт их правки молча. Перенесите изменения "
                    f"в нашу версию,\n      затем обновите base_sha256 на {now}.\n"
                    f"      Наша правка тут: {entry['reason']}"
                )
            if path not in replaced:
                problems.append(
                    f"{path}: числится в манифесте, но у нас такого файла нет — "
                    f"уберите запись."
                )

        print()
        if problems:
            bad(f"расхождений: {len(problems)}")
            for p in problems:
                print(f"  • {p}", file=sys.stderr)
            print()
            bad("контакт с базой разошёлся с манифестом — см. выше")
            return 1

        ok(f"все {len(replaced)} замещений объявлены, апстрим их не трогал")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
