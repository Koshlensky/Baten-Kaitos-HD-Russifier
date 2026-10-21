"""Установка пилота в игру для теста (D-028) и откат.

Пишет в папку игры только при разрешении installs.pilot_test в base/progress/approvals.json,
только файлы из плана (текстовый бандл и шрифты из разрешения) и только после того, как
резервные копии созданы и сверены по SHA-256. Общий запрет записи в папку игры
(paths.guard_write) для остальных инструментов не меняется.

Русский текст пишется только в слот TextData_en и только для строк пилота; все прочие записи
документа и все другие TextAsset бандла остаются побайтно прежними.

    python -m tools.install.pilot check
    python -m tools.install.pilot dry-run
    python -m tools.install.pilot install
    python -m tools.install.pilot verify  --log reports/install/pilot_<метка>.json
    python -m tools.install.pilot restore --log reports/install/pilot_<метка>.json
"""
import argparse
import datetime
import io
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import UnityPy
from fontTools.ttLib import TTFont

from tools.bkloc import pipeline
from tools.bkloc.extract import id_map, parse, roundtrip_identical, script_bytes
from tools.bkloc.fonts import load_legacy_patch
from tools.bkloc.paths import (BASE, FONT_BUNDLES, GAME_ROOT, GAMES, REPORTS, ROOT, SOURCE, WORK, bundle_path,
                               sha1_text, sha256_bytes, sha256_file, text_bundle_path, write_bytes, write_json,
                               write_text)

APPROVALS = BASE / "progress" / "approvals.json"
INSTALL = "pilot_test"
BACKUPS = ROOT / "backups"
LOGS = REPORTS / "install"
EXE = "batenkaitos.exe"
CYRILLIC_SAMPLE = "АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯабвгдеёжзийклмнопрстуфхцчшщъыьэюя"
LATIN_SAMPLE = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"


def authorization():
    data = json.loads(APPROVALS.read_text(encoding="utf-8"))
    auth = (data.get("installs") or {}).get(INSTALL)
    if not auth:
        raise SystemExit(f"Нет разрешения installs.{INSTALL} в {APPROVALS}: установка в игру запрещена.")
    return auth


def running_games():
    out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True,
                         encoding="mbcs", errors="replace").stdout
    return [line for line in out.splitlines() if line.lower().startswith(f'"{EXE}"')]


def locked(path):
    try:
        with open(path, "r+b"):
            return False
    except PermissionError:
        return True


def pilot_records(auth):
    """Строки пилота по играм: единицы допуска минус заблокированные; все должны быть в нужном статусе."""
    by_game = {game: [] for game in auth["games"]}
    scope = json.loads(APPROVALS.read_text(encoding="utf-8"))["scopes"][auth["scope"]]
    for unit_id in scope["units"]:
        game, _scope, unit, blocked = pipeline.unit_info(unit_id)
        by_game[game].extend(pipeline.unit_records(game, unit, blocked))
    problems = [r["key"] for recs in by_game.values() for r in recs
                if r.get("status") != auth["min_status"] or not r.get("ru")]
    total = sum(len(recs) for recs in by_game.values())
    if problems or total != auth["expected_lines"]:
        raise SystemExit(f"Строки пилота не готовы: всего {total} (ожидается {auth['expected_lines']}), "
                         f"не в статусе {auth['min_status']}: {problems[:10]}")
    return by_game


def plan(auth):
    targets = []
    for game in auth["games"]:
        manifest = json.loads((SOURCE / game / "manifest.json").read_text(encoding="utf-8"))
        fonts = {f["bundle"]: f["sha256"] for f in manifest["font_bundles"]}
        tb = text_bundle_path(game)
        targets.append({"game": game, "kind": "text", "bundle": tb.name, "path": tb,
                        "original_sha256": manifest["text_bundle"]["sha256"]})
        for bundle in auth["font_bundles"]:
            path = bundle_path(game, bundle + ".bundle")
            targets.append({"game": game, "kind": "font", "bundle": path.name, "font": FONT_BUNDLES[bundle],
                            "path": path, "original_sha256": fonts[path.name]})
    for t in targets:
        t["relative"] = str(t["path"].relative_to(GAME_ROOT))
    return targets


def preflight(targets):
    """Проверки перед записью: игра закрыта, файлы на месте, совпадают с эталоном и не заняты."""
    problems = []
    running = running_games()
    if running:
        problems.append(f"игра запущена: {running}")
    for t in targets:
        if not t["path"].exists():
            problems.append(f"нет файла {t['relative']}")
            continue
        if sha256_file(t["path"]) != t["original_sha256"]:
            problems.append(f"файл не совпадает с эталоном (уже изменён?): {t['relative']}")
        if locked(t["path"]):
            problems.append(f"файл занят другим процессом: {t['relative']}")
        if t["path"].suffix != ".bundle" or "StreamingAssets" not in t["path"].parts:
            problems.append(f"в плане файл вне бандлов Addressables: {t['relative']}")
    return problems


def assembly_hashes(games):
    """GameAssembly.dll не входит в план; хеши до и после установки — доказательство в журнале."""
    return {game: sha256_file(GAME_ROOT / GAMES[game]["folder"] / "GameAssembly.dll") for game in games}


def backup(targets, stamp):
    """Полные копии изменяемых файлов в новую папку; существующие копии не перезаписываются."""
    folder = BACKUPS / f"pilot_{stamp}"
    if folder.exists():
        raise SystemExit(f"Папка резервных копий уже существует, не перезаписываю: {folder}")
    for t in targets:
        dst = folder / t["relative"]
        if dst.exists():
            raise SystemExit(f"Резервная копия уже существует, не перезаписываю: {dst}")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(t["path"], dst)
        copy_sha = sha256_file(dst)
        if copy_sha != t["original_sha256"] or copy_sha != sha256_file(t["path"]):
            raise SystemExit(f"Резервная копия не совпадает с оригиналом: {dst}")
        os.chmod(dst, stat.S_IREAD)
        t["backup"] = dst
        t["backup_sha256"] = copy_sha
    return folder


def text_assets(env):
    assets = {}
    for obj in env.objects:
        if obj.type.name == "TextAsset":
            data = obj.read()
            assets[data.m_Name] = data
    return assets


def expected_text(original_assets, records):
    """Карта индекс → итоговый текст TextData_en: русский для строк пилота, оригинал для остальных."""
    ids = parse(script_bytes(original_assets["TextIDList"]))[0]["textId"]
    index_of = {text_id: i for i, text_id in enumerate(ids)}
    en_map = id_map(parse(script_bytes(original_assets["TextData_en"]))[0])
    ru = {}
    for r in records:
        i = index_of[r["text_id"]]
        if r["en_sha1"] != sha1_text(en_map[i][0]):
            raise SystemExit(f"Английский текст изменился с момента перевода: {r['key']}")
        ru[i] = r["ru"]
    expected = {i: (ru[i] if i in ru else text) for i, (text, _entry) in en_map.items()}
    return expected, ru


def build_text(original, records, out):
    env = UnityPy.load(str(original))
    assets = text_assets(env)
    doc, has_bom, text = parse(script_bytes(assets["TextData_en"]))
    if not roundtrip_identical(doc, text):
        raise SystemExit("Документ TextData_en не пересериализуется без изменений — сборка остановлена.")
    _expected, ru = expected_text(assets, records)
    array = []
    for entry in doc["array"]:
        keep = [i for i in entry["i"] if i not in ru]
        moved = [i for i in entry["i"] if i in ru]
        if keep:
            array.append(entry if not moved else {**entry, "i": keep})
        array.extend({"i": [i], "t": ru[i]} for i in moved)
    new_doc = {**doc, "array": array}
    assets["TextData_en"].m_Script = ("\ufeff" if has_bom else "") + json.dumps(new_doc, ensure_ascii=False,
                                                                               separators=(",", ":"))
    assets["TextData_en"].save()
    write_bytes(out, env.file.save(packer="original"))
    return {"lines": len(ru), "entries_before": len(doc["array"]), "entries_after": len(array)}


def check_text(original, built, records):
    """Сверка собранного бандла с оригиналом: по ID и побайтно для всех прочих TextAsset."""
    orig_assets = text_assets(UnityPy.load(str(original)))
    new_assets = text_assets(UnityPy.load(str(built)))
    expected, ru = expected_text(orig_assets, records)
    original_en = {i: text for i, (text, _entry) in id_map(parse(script_bytes(orig_assets["TextData_en"]))[0]).items()}
    got = {i: text for i, (text, _entry) in id_map(parse(script_bytes(new_assets["TextData_en"]))[0]).items()}
    errors = []
    if set(orig_assets) != set(new_assets):
        errors.append("набор TextAsset изменился")
    if got != expected:
        diff = [i for i in set(expected) | set(got) if expected.get(i) != got.get(i)]
        errors.append(f"тексты по ID не совпадают: {len(diff)}")
    changed = sum(1 for i in original_en if got.get(i) != original_en[i])
    should_change = sum(1 for i in ru if ru[i] != original_en[i])
    if changed != should_change:
        errors.append(f"изменено строк {changed}, ожидалось {should_change}")
    for name, data in orig_assets.items():
        if name != "TextData_en" and script_bytes(data) != script_bytes(new_assets[name]):
            errors.append(f"изменился {name}")
    return {"ok": not errors, "errors": errors, "changed_lines": changed, "ids": len(expected)}


def advances(raw, sample):
    font = TTFont(io.BytesIO(raw))
    cmap, hmtx = font.getBestCmap(), font["hmtx"]
    return {ch: hmtx[cmap[ord(ch)]][0] for ch in sample if ord(ch) in cmap}


def font_data(path):
    env = UnityPy.load(str(path))
    return env, next(o.read() for o in env.objects if o.type.name == "Font")


def build_font(original, out, pin_modified=None):
    """pin_modified — значение head.modified из compat/*.json: даёт побайтное совпадение с выпуском."""
    env, font = font_data(original)
    patched, changed = load_legacy_patch()(bytes(font.m_FontData))
    if pin_modified is not None:
        from tools.install.font_pin import pin_head_modified
        patched = pin_head_modified(patched, pin_modified)
    if not changed:
        raise SystemExit(f"Патч не изменил шрифт {font.m_Name}: в плане его быть не должно.")
    font.m_FontData = patched
    font.save()
    write_bytes(out, env.file.save(packer="original"))
    return {"glyphs_narrowed": changed}


def check_font(original, built):
    raw_before = bytes(font_data(original)[1].m_FontData)
    raw_after = bytes(font_data(built)[1].m_FontData)
    cyr_before, cyr_after = advances(raw_before, CYRILLIC_SAMPLE), advances(raw_after, CYRILLIC_SAMPLE)
    lat_before, lat_after = advances(raw_before, LATIN_SAMPLE), advances(raw_after, LATIN_SAMPLE)
    errors = []
    if len(cyr_after) != len(CYRILLIC_SAMPLE) or set(cyr_before) != set(cyr_after):
        errors.append("в шрифте не хватает кириллических глифов")
    mean_before = sum(cyr_before.values()) / max(1, len(cyr_before))
    mean_after = sum(cyr_after.values()) / max(1, len(cyr_after))
    widest = max((cyr_after[ch] / cyr_before[ch] for ch in cyr_after if cyr_before.get(ch)), default=0)
    # Патч v1.0 ставит ширину по контуру: отдельные широкие буквы (Ш, Щ, Ж) могут стать чуть шире кегля.
    if mean_after >= mean_before * 0.95:
        errors.append(f"кириллица в среднем не сужена: {mean_before:.0f} → {mean_after:.0f}")
    if widest > 1.05:
        errors.append(f"глиф стал шире оригинала более чем на 5%: {widest:.2f}")
    if lat_before != lat_after:
        errors.append("латиница или цифры изменились")
    return {"ok": not errors, "errors": errors, "cyrillic_mean_before": round(mean_before), "cyrillic_mean_after": round(mean_after),
            "widest_ratio": round(widest, 3), "sample_after": {ch: cyr_after[ch] for ch in "оАыШ" if ch in cyr_after}}


def write_into_game(target, data_path, allowed):
    """Единственная запись в папку игры: файл из плана, через временный файл и атомарную замену."""
    path = Path(target)
    if path.resolve() not in allowed:
        raise SystemExit(f"Файл не входит в план установки: {path}")
    path.resolve().relative_to(GAME_ROOT.resolve())
    tmp = path.with_name(path.name + ".bkloc_tmp")
    shutil.copyfile(data_path, tmp)
    os.replace(tmp, path)


def file_rows(targets):
    rows = []
    for t in targets:
        rows.append({k: (str(v) if isinstance(v, Path) else v) for k, v in t.items()
                     if k not in ("path",)})
    return rows


def write_log(log, stamp):
    json_path = LOGS / f"pilot_{stamp}.json"
    write_json(json_path, log)
    lines = [f"# Установка пилота в игру — {log['installed_at']}", "",
             f"Разрешение: {log['decision']}. Слоты текста: {', '.join(log['text_slots'])}. "
             f"Строк пилота: {log['lines']['bk1']} (BK1) + {log['lines']['bk2']} (BK2).", "",
             f"Резервные копии: `{log['backup_folder']}` (только чтение, сверены по SHA-256).", "",
             "| Игра | Файл | Что | SHA-256 до | SHA-256 после | Размер до → после |", "|---|---|---|---|---|---|"]
    for f in log["files"]:
        what = "текст (TextData_en)" if f["kind"] == "text" else f"шрифт {f['font']}"
        lines.append(f"| {f['game']} | `{f['bundle']}` | {what} | `{f['original_sha256'][:12]}…` | "
                     f"`{f['new_sha256'][:12]}…` | {f['size_before']} → {f['size_after']} |")
    same_dll = log["game_assembly_sha256"]["before"] == log["game_assembly_sha256"]["after"]
    lines += ["", "Проверки после установки: " + ("пройдены." if log["verified"]["ok"] else "ОШИБКИ: "
                                                  + "; ".join(log["verified"]["errors"])),
              f"GameAssembly.dll: {'не изменён (хеши до и после совпадают)' if same_dll else 'ИЗМЕНИЛСЯ'}.", "",
              "## Откат", "",
              f"1. Закройте обе игры.",
              f"2. Выполните в папке проекта: `.\\.venv\\Scripts\\python.exe -m tools.install.pilot restore --log {json_path.relative_to(ROOT)}`",
              "3. Команда вернёт файлы из резервных копий и сверит их с эталонными хешами.",
              "", "Вручную: скопируйте каждый файл из папки резервных копий (путь внутри неё повторяет путь "
              "относительно папки игры) обратно в папку игры с заменой. Крайний случай — «Проверить целостность "
              "файлов игры» в Steam: изменённые файлы — это оригиналы Steam, их хеши совпадали с эталоном."]
    write_text(json_path.with_suffix(".md"), "\n".join(lines) + "\n")
    return json_path


def verify_files(files, records_by_game):
    errors = []
    for f in files:
        path = GAME_ROOT / f["relative"]
        if sha256_file(path) != f["new_sha256"]:
            errors.append(f"хеш не совпадает с собранным: {f['relative']}")
            continue
        if f["kind"] == "text":
            result = check_text(f["backup"], path, records_by_game[f["game"]])
        else:
            result = check_font(f["backup"], path)
        errors.extend(f"{f['relative']}: {e}" for e in result["errors"])
    return {"ok": not errors, "errors": errors}


def cmd_check(_args):
    auth = authorization()
    records = pilot_records(auth)
    targets = plan(auth)
    problems = preflight(targets)
    print(f"Разрешение {auth['decision']} от {auth['approved']}; строк пилота: "
          + ", ".join(f"{g} {len(r)}" for g, r in records.items()))
    for t in targets:
        print(f"  {t['game']} {t['kind']:4s} {t.get('font', ''):22s} {t['relative']}")
    print("Проверки: " + ("пройдены" if not problems else "; ".join(problems)))
    return 1 if problems else 0


def cmd_install(_args):
    auth = authorization()
    records = pilot_records(auth)
    targets = plan(auth)
    problems = preflight(targets)
    if problems:
        raise SystemExit("Установка остановлена: " + "; ".join(problems))
    assembly_before = assembly_hashes(auth["games"])
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    folder = backup(targets, stamp)
    print(f"Резервные копии: {folder} ({len(targets)} файлов, SHA-256 сверены)")

    build_dir = WORK / "install" / stamp
    for t in targets:
        out = build_dir / t["relative"]
        if t["kind"] == "text":
            t["build"] = build_text(t["backup"], records[t["game"]], out)
            t["check"] = check_text(t["backup"], out, records[t["game"]])
        else:
            t["build"] = build_font(t["backup"], out)
            t["check"] = check_font(t["backup"], out)
        if not t["check"]["ok"]:
            raise SystemExit(f"Сборка {t['relative']} не прошла проверку: {t['check']['errors']} — в игру ничего не записано.")
        t["built"] = out
        t["new_sha256"] = sha256_file(out)
        t["size_before"] = t["path"].stat().st_size
        t["size_after"] = out.stat().st_size
    print("Сборка проверена: текст по ID и побайтно, шрифты по метрикам")

    if running_games():
        raise SystemExit("Игра запущена — в игру ничего не записано.")
    allowed = {t["path"].resolve() for t in targets}
    for t in targets:
        write_into_game(t["path"], t["built"], allowed)
    files = file_rows(targets)
    verified = verify_files(files, records)
    assembly_after = assembly_hashes(auth["games"])
    if assembly_after != assembly_before:
        verified["ok"] = False
        verified["errors"].append("GameAssembly.dll изменился")
    log = {"install": INSTALL, "decision": auth["decision"], "installed_at": datetime.datetime.now().isoformat(timespec="seconds"),
           "game_root": str(GAME_ROOT), "text_slots": auth["text_slots"],
           "lines": {g: len(r) for g, r in records.items()}, "keys": {g: [r["key"] for r in recs] for g, recs in records.items()},
           "backup_folder": str(folder), "build_folder": str(build_dir), "files": files, "verified": verified,
           "game_assembly_sha256": {"before": assembly_before, "after": assembly_after}}
    path = write_log(log, stamp)
    print(f"Установлено файлов: {len(files)}; проверка после установки: {'пройдена' if verified['ok'] else verified['errors']}")
    print(f"Журнал: {path}")
    return 0 if verified["ok"] else 1


def cmd_dry_run(_args):
    """Сборка и проверка на копиях в work/install/dry_<метка>/ без резервных копий и без записи в игру."""
    auth = authorization()
    records = pilot_records(auth)
    targets = plan(auth)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    root = WORK / "install" / f"dry_{stamp}"
    ok = True
    for t in targets:
        original = root / "original" / t["relative"]
        original.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(t["path"], original)
        out = root / "built" / t["relative"]
        if t["kind"] == "text":
            build = build_text(original, records[t["game"]], out)
            check = check_text(original, out, records[t["game"]])
        else:
            build = build_font(original, out)
            check = check_font(original, out)
        ok = ok and check["ok"]
        print(f"  {t['game']} {t['kind']:4s} {t.get('font', ''):22s} {t['path'].stat().st_size:>9} → {out.stat().st_size:>9} байт; "
              f"{build}; проверка: {'ok' if check['ok'] else check['errors']}")
    print(f"Пробная сборка в {root}: {'все проверки пройдены' if ok else 'есть ошибки'}; в игру ничего не записано")
    return 0 if ok else 1


def load_log(path):
    log = json.loads(Path(path).read_text(encoding="utf-8"))
    if log.get("install") != INSTALL:
        raise SystemExit("Это не журнал установки пилота.")
    return log


def cmd_verify(args):
    log = load_log(args.log)
    records = pilot_records(authorization())
    result = verify_files(log["files"], records)
    print("Проверка установленных файлов: " + ("пройдена" if result["ok"] else "; ".join(result["errors"])))
    return 0 if result["ok"] else 1


def cmd_restore(args):
    log = load_log(args.log)
    if running_games():
        raise SystemExit("Игра запущена: закройте обе игры и повторите откат.")
    allowed = {(GAME_ROOT / f["relative"]).resolve() for f in log["files"]}
    for f in log["files"]:
        if sha256_file(f["backup"]) != f["original_sha256"]:
            raise SystemExit(f"Резервная копия повреждена, откат остановлен: {f['backup']}")
    restored = []
    for f in log["files"]:
        path = GAME_ROOT / f["relative"]
        write_into_game(path, f["backup"], allowed)
        ok = sha256_file(path) == f["original_sha256"]
        restored.append({"relative": f["relative"], "ok": ok})
    log.setdefault("restores", []).append({"at": datetime.datetime.now().isoformat(timespec="seconds"), "files": restored})
    write_json(Path(args.log), log)
    bad = [r["relative"] for r in restored if not r["ok"]]
    print(f"Откат: восстановлено {len(restored) - len(bad)} из {len(restored)} файлов" + (f"; ошибки: {bad}" if bad else ", хеши совпадают с эталоном"))
    return 1 if bad else 0


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    sub.add_parser("dry-run")
    sub.add_parser("install")
    for name in ("verify", "restore"):
        p = sub.add_parser(name)
        p.add_argument("--log", required=True)
    args = parser.parse_args()
    return {"check": cmd_check, "dry-run": cmd_dry_run, "install": cmd_install, "verify": cmd_verify,
            "restore": cmd_restore}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
