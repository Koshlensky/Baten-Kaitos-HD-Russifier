"""Аудит всей истории Git-репозитория перед публикацией (в том числе чужого/старого): удалённые файлы остаются в старых коммитах.

    python -m tools.publish.audit_history <папка репозитория>

Проверяет: файлы ресурсов игры и бинарные файлы, большие объекты, каталоги с текстами игры (source/, runs/), личные пути,
адреса почты авторов, шаблоны ключей. Репозиторий не изменяется.
"""
import re
import subprocess
import sys
from pathlib import Path

BAD_EXT = re.compile(r"\.(bundle|resS|assets|dll|exe|zip|7z|ttf|otf|ttc|png|tga|jpg|webp|dat|bin|pdf)$", re.I)
BAD_DIRS = re.compile(r"^(source|runs|work|backups|dist|exports)/")
SECRETS = re.compile(r"sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{12,}|xai-[A-Za-z0-9]{20,}|BEGIN [A-Z ]*PRIVATE KEY")
PERSONAL = re.compile(r"C:\\Users\\[^\\\s\"']+")


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8", errors="replace").stdout


def main():
    repo = Path(sys.argv[1]).resolve()
    commits = git(repo, "rev-list", "--all").split()
    print(f"коммитов во всех ветках и тегах: {len(commits)}")
    paths = {}
    for line in git(repo, "log", "--all", "--name-only", "--pretty=format:").splitlines():
        if line.strip():
            paths[line.strip().strip('"')] = True
    bad_ext = sorted(p for p in paths if BAD_EXT.search(p))
    bad_dirs = sorted(p for p in paths if BAD_DIRS.match(p))
    print(f"файлов в истории (уникальных путей): {len(paths)}")
    print(f"бинарные/ресурсные расширения: {len(bad_ext)}", bad_ext[:8])
    print(f"каталоги с локальными данными (source/runs/work/backups/dist/exports): {len(bad_dirs)}", bad_dirs[:5])
    big = []
    for line in git(repo, "rev-list", "--objects", "--all").splitlines():
        parts = line.split(" ", 1)
        if len(parts) == 2:
            big.append((parts[0], parts[1]))
    sizes = git(repo, "cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)", ).splitlines() if False else []
    out = subprocess.run(["git", "-C", str(repo), "cat-file", "--batch-check"], input="\n".join(o for o, _ in big), capture_output=True, text=True).stdout.splitlines()
    names = dict(big)
    large = sorted(((int(l.split()[2]), names.get(l.split()[0], "?")) for l in out if len(l.split()) == 3 and l.split()[1] == "blob" and int(l.split()[2]) > 5_000_000), reverse=True)
    print(f"объектов крупнее 5 МБ: {len(large)}", [(f"{s >> 20} МБ", n) for s, n in large[:5]])
    authors = sorted(set(git(repo, "log", "--all", "--format=%an <%ae>").splitlines()))
    print("авторы и почты в истории:", authors)
    hits = git(repo, "grep", "-I", "-l", "-E", SECRETS.pattern, *commits[:200]).splitlines() if commits else []
    print("шаблоны ключей/секретов (первые 200 коммитов):", len(hits))
    personal = git(repo, "grep", "-I", "-l", "-E", PERSONAL.pattern, *commits[:200]).splitlines() if commits else []
    print("личные пути пользователя Windows:", len(personal))
    problems = bool(bad_ext or bad_dirs or large or hits)
    print("ИТОГ:", "в истории есть нежелательные данные — публиковать эту историю нельзя" if problems else "явных нежелательных данных не найдено (проверьте вручную перевод v1.0 и авторов)")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
