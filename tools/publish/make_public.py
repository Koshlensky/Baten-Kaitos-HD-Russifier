"""Сборка публичного дерева проекта в отдельную папку с новой, чистой Git-историей.

Берёт из рабочего репозитория только разрешённые файлы (белый список), превращает матрицу в публичный формат
(без поля `en` — английского оригинала игры), чистит личные пути, проверяет результат на утечки (сверка 6-словных
фрагментов английского оригинала) и создаёт один коммит. Рабочий репозиторий и его история не изменяются.

    python -m tools.publish.make_public --out ../bk_loc_public [--commit]
"""
import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

TEXT_EXT = {".md", ".json", ".jsonl", ".yaml", ".yml", ".txt", ".csv", ".py", ".toml", ".cfg", ".gitignore", ".gitattributes"}

# ---- белый список: (путь в рабочем репозитории, исключения-подстроки)
COPY_DIRS = {
    "tools": ["__pycache__", "third_party", "recover_v1_code.py", "qa/legacy_guard.py"],
    "prompts": [],
    "base/glossary": ["candidates_"],
    "base/characters": ["_evidence", "candidates_"],
    "base/memory": ["official_outline"],
    "docs": [],
    "release": [],
}
COPY_FILES = ["base/progress/open_questions.md", "base/matrix/schema.json", "base/matrix/bk1/_index.json", "base/matrix/bk2/_index.json",
              ".gitattributes", "tools/third_party/README.md"]
SCENE_FILES = ["_batches.json", "_order.json", "_queue.json", "_family_chapters.json"]   # только структура (ID), без текста
DOC_EXCLUDE_DEFAULT = ["docs/examples/", "docs/11a_pilot_scenes.md", "docs/03c_evidence_prototype.md", "docs/10_pilot_review.md"]
PUBLIC_DOCS = ROOT / "public_docs"      # README, BUILDING, CONTRIBUTING, LICENSE… публичной версии (хранятся в рабочем репозитории)


def sha256(p):
    h = hashlib.sha256()
    h.update(Path(p).read_bytes())
    return h.hexdigest()


def should_skip(rel, excludes):
    return any(x in rel for x in excludes)


def matrix_public_rows(path):
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        r.pop("en", None)
        out.append(json.dumps(r, ensure_ascii=False))
    return "\n".join(out) + "\n"


def scrub_patch_fonts(text):
    """Из восстановленного кода v1.0 остаётся только функция патча; личные пути убираются."""
    text = re.sub(r'^GAME_ROOT = .*$', 'GAME_ROOT = None   # пути задаёт tools/bkloc/fonts.py', text, flags=re.M)
    text = re.sub(r'^SUB = .*$', 'SUB = None', text, flags=re.M)
    text = re.sub(r'^BACKUP = .*$', 'BACKUP = None', text, flags=re.M)
    text = re.sub(r'^OUT_DIR = .*$', 'OUT_DIR = None', text, flags=re.M)
    return text


PATH_SCRUB = [("D:\\\\SteamLibrary\\\\bk_loc_v2", "<репозиторий>"), ("<репозиторий>", "<репозиторий>"),
              ("D:\\\\SteamLibrary\\\\steamapps\\\\common\\\\BatenKaitos HD Remaster", "<папка игры>"),
              ("<папка игры>", "<папка игры>"),
              ("D:\\\\SteamLibrary\\\\bk_rusifikator", "<папка проекта v1.0>"), ("<папка проекта v1.0>", "<папка проекта v1.0>"),
              ("D:\\\\SteamLibrary\\\\bk_extract", "<папка извлечения v1.0>"), ("<папка извлечения v1.0>", "<папка извлечения v1.0>"),
              ("<библиотека Steam>", "<библиотека Steam>"), ("D:\\\\SteamLibrary", "<библиотека Steam>"), ("C:\\Work\\github", "<локальный клон v1.0>")]
CJK_RUN = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]{14,}")


def scrub_paths(text):
    for a, b in PATH_SCRUB:
        text = text.replace(a, b)
    return text


def build_original_cjk():
    win = set()
    for game in ("bk1", "bk2"):
        for line in (ROOT / "source" / game / "lines.jsonl").read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                for lang in ("jp", "kr", "sc", "tc"):
                    for m in CJK_RUN.finditer(r.get(lang) or ""):
                        s = m.group(0)
                        win |= {s[i:i + 14] for i in range(len(s) - 13)}
    return win


def mask_cjk(text, windows):
    def repl(m):
        s = m.group(0)
        return "[…]" if any(s[i:i + 14] in windows for i in range(len(s) - 13)) else s
    return CJK_RUN.sub(repl, text)


def collect(out):
    copied = []

    def put(src, rel, data=None):
        dst = out / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if data is None:
            shutil.copyfile(src, dst)
        else:
            dst.write_text(data, encoding="utf-8", newline="\n")
        copied.append(rel)

    for d, excl in COPY_DIRS.items():
        base = ROOT / d
        for p in sorted(base.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(ROOT).as_posix()
            if should_skip(rel, excl) or p.suffix in (".pyc", ".zip", ".exe", ".dll") or "/__pycache__/" in rel:
                continue
            if d == "docs" and should_skip(rel, DOC_EXCLUDE_DEFAULT):
                continue
            put(p, rel)
    for f in COPY_FILES:
        if (ROOT / f).exists():
            put(ROOT / f, f)
    for game in ("bk1", "bk2"):
        for s in SCENE_FILES:
            p = ROOT / "base" / "scenes" / f"{game}{s}"
            if p.exists():
                put(p, f"base/scenes/{game}{s}")
        for p in sorted((ROOT / "base" / "matrix" / game).glob("*.jsonl")):
            put(p, f"base/matrix/{game}/{p.name}", matrix_public_rows(p))
    if (ROOT / "base/scenes/tiers.json").exists():
        put(ROOT / "base/scenes/tiers.json", "base/scenes/tiers.json")
    put(None, "legacy_v1/patch_fonts.py", scrub_patch_fonts((ROOT / "legacy_v1/patch_fonts.py").read_text(encoding="utf-8")))
    put(ROOT / "legacy_v1/cyrillic_allowlist.json", "legacy_v1/cyrillic_allowlist.json")
    for name in ("README.md", "BUILDING.md", "CONTRIBUTING.md", "CHANGELOG.md", "LICENSE", "NOTICE.md", "DATA_LICENSE.md", ".gitignore",
                 "requirements.txt", "RELEASE_NOTES_v2.0.md"):
        if (PUBLIC_DOCS / name).exists():
            put(PUBLIC_DOCS / name, name)
    return copied


def compat_manifest():
    """Совместимость: хеши оригинальных файлов Steam-версии и эталонных результатов сборки (без самих файлов)."""
    m = json.loads((ROOT / "work/release_stage/data/manifest.json").read_text(encoding="utf-8"))
    import UnityPy
    from tools.install.font_pin import read_head_modified
    for e in m["files"]:
        if e["kind"] == "font":     # метка времени выпуска: по ней сборка шрифта воспроизводится побайтно
            env = UnityPy.load(str(ROOT / "work/release_stage/data/payload" / e["payload"]))
            font = [o.read() for o in env.objects if o.type.name == "Font"][0]
            e["head_modified"] = read_head_modified(bytes(font.m_FontData))
    return {"game": "Baten Kaitos I & II HD Remaster (Steam AppID 2146170)", "steam_buildid": m["steam_buildid"], "version": m["version"],
            "note": "В шрифтах выпуска v2.0 записано время сборки (head.modified); оно сохранено в head_modified и накладывается "
                    "при сборке, поэтому хеши шрифтов воспроизводятся побайтно.",
            "files": [{"relative": e["relative"], "kind": e["kind"], "original_sha256": e["original_sha256"], "new_sha256": e["new_sha256"],
                       "reproducible": True, **({"head_modified": e["head_modified"]} if "head_modified" in e else {})} for e in m["files"]]}


# ---- проверка утечек
WORD = re.compile(r"[A-Za-z']+")


IGNORE_PHRASES = ("baten kaitos", "kaitos eternal wings", "eternal wings and the lost ocean", "wings and the lost ocean")


def shingles(text, n=6):
    """6-словные фрагменты; пропускаются шингли из одиночных букв (теги <p><w>) и названия игр."""
    w = [x.lower() for x in WORD.findall(text)]
    out = set()
    for i in range(len(w) - n + 1):
        part = w[i:i + n]
        if sum(len(x) > 2 for x in part) < 4:
            continue
        s = " ".join(part)
        if any(ph in s for ph in IGNORE_PHRASES):
            continue
        out.add(s)
    return out


def build_original_shingles():
    sh = set()
    for game in ("bk1", "bk2"):
        for line in (ROOT / "source" / game / "lines.jsonl").read_text(encoding="utf-8").splitlines():
            if line.strip():
                sh |= shingles(json.loads(line)["en"])
    return sh


def mask_english(text, original, n=6):
    """Закрывает куски английского оригинала (токены, входящие хотя бы в один 6-словный фрагмент оригинала) пометкой [...]."""
    toks = [(m.start(), m.end(), m.group(0).lower()) for m in WORD.finditer(text)]
    covered = [False] * len(toks)
    for i in range(len(toks) - n + 1):
        part = [x[2] for x in toks[i:i + n]]
        if sum(len(x) > 2 for x in part) < 4:
            continue
        s = " ".join(part)
        if s in original and not any(ph in s for ph in IGNORE_PHRASES):
            for j in range(i, i + n):
                covered[j] = True
    out, pos, i = [], 0, 0
    while i < len(toks):
        if covered[i]:
            j = i
            while j + 1 < len(toks) and covered[j + 1]:
                j += 1
            out.append(text[pos:toks[i][0]])
            out.append("[…]")
            pos = toks[j][1]
            i = j + 1
        else:
            i += 1
    out.append(text[pos:])
    return "".join(out)


KEEP_FIELDS = {"ru", "ru_before", "key", "text_id", "source_file", "prev_key", "next_key", "en_sha1", "dedup_group", "tags", "scene_id", "batch_id"}


def mask_obj(obj, original, field=None):
    """Маскирует цитаты оригинала во всех строковых полях строки матрицы, кроме русского текста и служебных ключей."""
    if isinstance(obj, dict):
        return {k: mask_obj(v, original, k) for k, v in obj.items()}
    if isinstance(obj, list):
        return [mask_obj(v, original, field) for v in obj]
    if isinstance(obj, str) and field not in KEEP_FIELDS:
        return mask_english(obj, original)
    return obj


def leak_scan(out, original):
    hits = {}
    for p in sorted(out.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in TEXT_EXT or ".git" in p.parts:
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        found = shingles(text) & original
        if found:
            hits[p.relative_to(out).as_posix()] = (len(found), sorted(found)[:2])
    return hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--drop-leaky-docs", action="store_true", help="удалить документы, в которых найдены куски английского оригинала")
    args = ap.parse_args()
    out = Path(args.out).resolve()
    if out.exists():
        subprocess.run(["cmd", "/c", "attrib", "-R", str(out / "*"), "/S", "/D"], capture_output=True)
        shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    copied = collect(out)
    (out / "compat").mkdir(exist_ok=True)
    (out / "compat" / "buildid_20890367.json").write_text(json.dumps(compat_manifest(), ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "PUBLIC_TREE").write_text("Публичное дерево: матрица без английского оригинала. Создано tools/publish/make_public.py.\n", encoding="utf-8")
    # личные пути и японский/корейский/китайский текст оригинала в документах
    cjk = build_original_cjk()
    for p in sorted(out.rglob("*")):
        if p.is_file() and p.suffix.lower() in (".md", ".json", ".jsonl", ".yaml", ".csv", ".txt", ".py") and ".git" not in p.parts:
            old = p.read_text(encoding="utf-8")
            new = scrub_paths(old)
            if p.suffix.lower() in (".md", ".yaml", ".csv", ".txt") or p.relative_to(out).parts[0] in ("docs", "prompts"):
                new = mask_cjk(new, cjk)
            if new != old:
                p.write_text(new, encoding="utf-8", newline="\n")
    # точное число файлов в публичном дереве подставляется в документы (число не меняется от подстановки)
    n_files = sum(1 for q in out.rglob("*") if q.is_file() and ".git" not in q.parts)
    for q in out.rglob("*.md"):
        s = q.read_text(encoding="utf-8")
        if "{{PUBLIC_FILES}}" in s:
            q.write_text(s.replace("{{PUBLIC_FILES}}", str(n_files)), encoding="utf-8", newline="\n")
    original = build_original_shingles()
    hits = leak_scan(out, original)
    masked = []
    for rel in sorted(hits):
        if rel.startswith(("base/matrix/", "base/glossary/", "base/memory/", "base/characters/", "prompts/", "docs/", "base/progress/")):
            p = out / rel
            if rel.startswith("base/matrix/") and rel.endswith(".jsonl"):
                rows = [mask_obj(json.loads(l), original) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
                new = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
            else:
                new = mask_english(p.read_text(encoding="utf-8"), original)
            p.write_text(new, encoding="utf-8", newline="\n")
            masked.append(rel)
    print("файлов с закрытыми цитатами оригинала:", len(masked))
    hits = leak_scan(out, original)
    dropped = []
    for rel, (n, sample) in sorted(hits.items(), key=lambda kv: -kv[1][0]):
        print(f"УТЕЧКА? {rel}: совпадающих фрагментов {n}, пример: {sample[0]!r}")
        if args.drop_leaky_docs and rel.startswith(("docs/", "base/memory/", "base/progress/", "base/glossary/disputes", "prompts/")):
            (out / rel).unlink()
            dropped.append(rel)
    if dropped:
        hits = leak_scan(out, original)
        print("удалено документов:", len(dropped), "; осталось с совпадениями:", len(hits))
    files = [p for p in out.rglob("*") if p.is_file() and ".git" not in p.parts]
    total = sum(p.stat().st_size for p in files)
    report = {"files": len(files), "bytes": total, "masked_files": masked, "dropped_for_leaks": dropped, "leak_hits": {k: v[0] for k, v in hits.items()},
              "game_assets": [p.relative_to(out).as_posix() for p in files if p.suffix.lower() in (".bundle", ".dll", ".exe", ".resS", ".ttf", ".otf", ".png", ".tga")]}
    (ROOT / "work" / "build").mkdir(parents=True, exist_ok=True)
    (ROOT / "work" / "build" / "public_tree_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"публичное дерево: {len(files)} файлов, {total / 1e6:.1f} МБ → {out}")
    if report["game_assets"]:
        raise SystemExit("В дереве есть бинарные файлы игры/графики: " + ", ".join(report["game_assets"][:5]))
    if args.commit:
        env = {"GIT_AUTHOR_NAME": "Baten Kaitos RU", "GIT_AUTHOR_EMAIL": "noreply@example.invalid",
               "GIT_COMMITTER_NAME": "Baten Kaitos RU", "GIT_COMMITTER_EMAIL": "noreply@example.invalid"}
        import os
        env = {**os.environ, **env}
        run = lambda *a: subprocess.run(["git", *a], cwd=out, check=True, env=env, capture_output=True, text=True)
        run("init", "-b", "main")
        run("config", "core.autocrlf", "false")
        run("add", "-A")
        run("commit", "-m", "Русификатор Baten Kaitos I & II HD Remaster v2.0: исходники (чистая публичная история)")
        print("коммит:", run("rev-parse", "--short", "HEAD").stdout.strip())


if __name__ == "__main__":
    main()
