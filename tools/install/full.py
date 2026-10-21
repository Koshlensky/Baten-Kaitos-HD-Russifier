"""Тестовая сборка русификатора v2.0 (полный перевод BK1 + BK2) и безопасная установка.

Сборка и проверка идут только на копиях: результат лежит в dist/test_v2.0/ (вне папки игры).
В игру команда `install` пишет только после отдельного разрешения владельца
(base/progress/approvals.json → installs.full_test) и только файлы из плана:
текстовый бандл и 4 диалоговых шрифта FOT на каждую игру (10 файлов). GameAssembly.dll,
каталог Addressables, сохранения и прочие файлы не затрагиваются.

    python -m tools.install.full originals     # найти и сохранить эталонные оригиналы
    python -m tools.install.full build         # собрать dist/test_v2.0
    python -m tools.install.full verify-build  # обратное извлечение и сверка с матрицей
    python -m tools.install.full status        # состояние файлов в игре (оригинал / пилот / v2.0 / неизвестно)
    python -m tools.install.full check         # предустановочные проверки без записи
    python -m tools.install.full install       # требует installs.full_test в approvals.json
    python -m tools.install.full verify        # проверка установленных файлов
    python -m tools.install.full restore       # полный возврат оригиналов
"""
import argparse
import collections
import datetime
import json
import os
import re
import shutil
import stat
import sys
from pathlib import Path

import UnityPy

from tools.bkloc import tags
from tools.bkloc.extract import id_map, parse, roundtrip_identical, script_bytes
from tools.bkloc.matrix import load as load_matrix
from tools.bkloc.paths import (BASE, FONT_BUNDLES, GAME_ROOT, GAMES, REPORTS, ROOT, SOURCE, WORK, bundle_path,
                               game_dir, sha1_text, sha256_file, text_bundle_path, write_bytes, write_json,
                               write_text)
from tools.install import pilot, ui_patch

APPROVALS = BASE / "progress" / "approvals.json"
INSTALL = "full_test"
DIST = ROOT / "dist" / "test_v2.0"
ORIGINALS = ROOT / "backups" / "originals_v2"
BACKUPS = ROOT / "backups"
LOGS = REPORTS / "install"
FONT_IDS = ["3f5054c359dec0113387d921efa3d18e", "f848a65bf8b4bbfcc47553c458cf7161",
            "a9d38e7719f3248029b1f8c84bb9c237", "0d9a198edca9e12232362a07dc95a843"]
# Слоты с полными языками хранят свой документ; слоты-заглушки основаны на английском документе.
FULL_SLOTS = {"bk1": {"en", "jp", "kr", "sc", "tc", "fr", "de", "es", "it"}, "bk2": {"en", "jp", "kr", "sc", "tc"}}
APPMANIFEST = GAME_ROOT.parent.parent / "appmanifest_2146170.acf"


def steam_buildid():
    text = APPMANIFEST.read_text(encoding="utf-8", errors="replace")
    match = re.search(r'"buildid"\s+"(\d+)"', text)
    return match.group(1) if match else None


def plan():
    """Файлы плана: текстовый бандл и 4 шрифта на каждую игру, с эталонными хешами оригиналов."""
    targets = []
    for game in GAMES:
        manifest = json.loads((SOURCE / game / "manifest.json").read_text(encoding="utf-8"))
        fonts = {f["bundle"]: f["sha256"] for f in manifest["font_bundles"]}
        tb = text_bundle_path(game)
        targets.append({"game": game, "kind": "text", "bundle": tb.name, "path": tb,
                        "original_sha256": manifest["text_bundle"]["sha256"]})
        for bundle in FONT_IDS:
            path = bundle_path(game, bundle + ".bundle")
            targets.append({"game": game, "kind": "font", "bundle": path.name, "font": FONT_BUNDLES[bundle],
                            "path": path, "original_sha256": fonts[path.name]})
    ui = json.loads(ui_patch.TARGETS.read_text(encoding="utf-8"))
    seen = set()
    for game in GAMES:
        for r in ui[game]["bundles"]:
            if r["relative"] in seen:
                continue
            seen.add(r["relative"])
            targets.append({"game": game, "kind": "asset", "bundle": r["bundle"], "path": GAME_ROOT / r["relative"],
                            "original_sha256": r["original_sha256"], "crc": r["crc"]})
        for r in ui[game].get("atlases", []):
            targets.append({"game": game, "kind": "atlas", "bundle": r["bundle"], "path": GAME_ROOT / r["relative"],
                            "original_sha256": r["original_sha256"], "crc": r["crc"], "atlas": r["kind"]})
        c = ui[game]["catalog"]
        targets.append({"game": game, "kind": "catalog", "bundle": "catalog.json", "path": GAME_ROOT / c["relative"],
                        "original_sha256": c["original_sha256"]})
    for t in targets:
        t["relative"] = str(t["path"].relative_to(GAME_ROOT))
        t["label"] = t.get("font") or {"text": "TextData", "asset": "UI " + t["bundle"][:8], "atlas": "графика " + t["bundle"][:8], "catalog": "каталог"}[t["kind"]]
    return targets


def font_pin(relative):
    """head.modified выпуска для шрифта (из compat/buildid_*.json, есть в публичном дереве), иначе None."""
    for path in sorted((ROOT / "compat").glob("buildid_*.json")):
        for f in json.loads(path.read_text(encoding="utf-8")).get("files", []):
            if f["relative"] == relative.replace("\\", "/"):
                return f.get("head_modified")
    return None


def catalog_bundles(game):
    """Бандлы игры с ненулевым CRC, правленые в этой сборке (их CRC в каталоге обнуляется)."""
    return [t["bundle"] for t in plan() if t["game"] == game and t["kind"] in ("asset", "atlas") and t.get("crc")]


def build_extra(t, original, out):
    """Сборка и проверка файла вида asset/catalog. Возвращает (info, check)."""
    if t["kind"] == "asset":
        changes = ui_patch.patch_bundle(t["game"], original, out)
        check = ui_patch.verify_bundle(t["game"], original, out, changes)
        return {"changes": [{k: c[k] for k in ("go", "old", "new")} for c in changes]}, check
    if t["kind"] == "atlas":
        from tools.gfx import build as gfx_build
        info, jobs = gfx_build.build_atlas(t["atlas"], original, out)
        check = gfx_build.verify_atlas(t["atlas"], original, out, jobs)
        return info, check
    bundles = catalog_bundles(t["game"])
    done = ui_patch.patch_catalog(t["game"], bundles, out, original)
    check = ui_patch.verify_catalog(t["game"], out, bundles, original)
    return {"crc_zeroed": done}, check


def pilot_hashes():
    """Хеши файлов, записанных пилотом D-028 и предыдущими установками v2.0 (допустимое исходное состояние игры)."""
    hashes = {}
    for pattern in ("pilot_*.json", "full_*.json"):
        for path in sorted(LOGS.glob(pattern)):
            log = json.loads(path.read_text(encoding="utf-8"))
            for f in log.get("files", []):
                hashes.setdefault(f["relative"], set()).add(f["new_sha256"])
    return hashes


def dist_manifest():
    path = DIST / "manifest.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


# ---------------------------------------------------------------- оригиналы

def cmd_originals(_args):
    ORIGINALS.mkdir(parents=True, exist_ok=True)
    for t in plan():
        dst = ORIGINALS / t["relative"]
        if dst.exists():
            ok = sha256_file(dst) == t["original_sha256"]
            print(("уже есть  " if ok else "ПОВРЕЖДЁН ") + t["relative"])
            if not ok:
                return 1
            continue
        source = None
        # источники оригинала: файл игры, копии пилота и оригиналы, сохранённые установщиком релиза (_rus_v2/originals)
        candidates = ([t["path"]] + [b / t["relative"] for b in sorted(BACKUPS.glob("pilot_*"))]
                      + [GAME_ROOT / "_rus_v2" / "originals" / t["relative"]])
        for c in candidates:
            if c.exists() and sha256_file(c) == t["original_sha256"]:
                source = c
                break
        if source is None:
            print("НЕТ ЭТАЛОНА " + t["relative"])
            return 1
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dst)
        if sha256_file(dst) != t["original_sha256"]:
            raise SystemExit(f"Копия не совпадает: {dst}")
        os.chmod(dst, stat.S_IREAD)
        print(f"сохранён   {t['relative']}  (из {source.parent.name})")
    return 0


def original_path(t):
    path = ORIGINALS / t["relative"]
    if not path.exists() or sha256_file(path) != t["original_sha256"]:
        raise SystemExit(f"Нет проверенного оригинала {t['relative']}: сначала `originals`.")
    return path


# ---------------------------------------------------------------- текст

def ru_by_index(game, assets):
    """Индекс → русский текст из матрицы: все `edited` и `needs_context` с готовым вариантом."""
    ids = parse(script_bytes(assets["TextIDList"]))[0]["textId"]
    index_of = {tid: i for i, tid in enumerate(ids)}
    en = {i: t for i, (t, _e) in id_map(parse(script_bytes(assets["TextData_en"]))[0]).items()}
    ru, kinds, stale = {}, {}, []
    for r in load_matrix(game):
        if r["status"] == "edited" or (r["status"] == "needs_context" and r.get("ru")):
            i = index_of[r["text_id"]]
            if r["en_sha1"] != sha1_text(en[i]):
                stale.append(r["key"])
                continue
            ru[i] = r["ru"]
            kinds[i] = (r["key"], r["status"])
    return ids, en, ru, kinds, stale


def slot_texts(game, slot, docs, en, ru, count):
    """Итоговые тексты слота: русский для переведённых ID, иначе оригинал слота (для заглушек — английский)."""
    own = {i: t for i, (t, _e) in id_map(docs[f"TextData_{slot}"][0]).items()} if slot in FULL_SLOTS[game] else en
    texts = []
    for i in range(count):
        texts.append(ru[i] if i in ru else own.get(i, en[i]))
    return texts


def regroup(texts):
    groups, order = {}, []
    for i, text in enumerate(texts):
        if text not in groups:
            groups[text] = []
            order.append(text)
        groups[text].append(i)
    return [{"i": groups[t], "t": t} for t in order]


def build_text_bundle(game, original, out):
    env = UnityPy.load(str(original))
    assets = pilot.text_assets(env)
    slots = sorted(n[len("TextData_"):] for n in assets if n.startswith("TextData_"))
    docs = {n: parse(script_bytes(assets[n])) for n in assets if n.startswith("TextData_")}
    for n, (doc, _bom, text) in docs.items():
        if not roundtrip_identical(doc, text):
            raise SystemExit(f"{n}: документ не пересериализуется без изменений — сборка остановлена.")
    ids, en, ru, kinds, stale = ru_by_index(game, assets)
    if stale:
        raise SystemExit(f"Английский оригинал изменился с момента перевода: {stale[:10]}")
    for slot in slots:
        doc, has_bom, _text = docs[f"TextData_{slot}"]
        texts = slot_texts(game, slot, docs, en, ru, len(ids))
        new_doc = {**doc, "array": regroup(texts)}
        assets[f"TextData_{slot}"].m_Script = ("\ufeff" if has_bom else "") + json.dumps(
            new_doc, ensure_ascii=False, separators=(",", ":"))
        assets[f"TextData_{slot}"].save()
    write_bytes(out, env.file.save(packer="original"))
    return {"slots": slots, "lines_included": len(ru), "ids": len(ids),
            "needs_context_included": sorted(k for k, s in kinds.values() if s == "needs_context")}


STRUCT = re.compile(r"<[^>]*>|\$\$[A-Z_]+\$\$|%[0-9.]*[sdfxX%]")


def signature(text):
    return STRUCT.findall(text)


def verify_text_bundle(game, original, built):
    """Обратное извлечение из собранного бандла и сверка с матрицей и оригиналом."""
    orig_assets = pilot.text_assets(UnityPy.load(str(original)))
    new_assets = pilot.text_assets(UnityPy.load(str(built)))
    errors, report = [], {"slots": {}}
    if set(orig_assets) != set(new_assets):
        errors.append("набор TextAsset изменился")
    for name, data in orig_assets.items():
        if not name.startswith("TextData_") and script_bytes(data) != script_bytes(new_assets[name]):
            errors.append(f"изменился {name}")
    ids, en, ru, kinds, stale = ru_by_index(game, orig_assets)
    docs = {n: parse(script_bytes(orig_assets[n])) for n in orig_assets if n.startswith("TextData_")}
    changed_expected = sum(1 for i in ru if ru[i] != en[i])
    for name in sorted(n for n in new_assets if n.startswith("TextData_")):
        slot = name[len("TextData_"):]
        got_doc, _bom, _t = parse(script_bytes(new_assets[name]))
        got = {i: t for i, (t, _e) in id_map(got_doc).items()}
        covered = sorted(got)
        if covered != list(range(len(ids))):
            errors.append(f"{name}: покрыты не все ID")
            continue
        expected = slot_texts(game, slot, docs, en, ru, len(ids))
        diff = [i for i in range(len(ids)) if got[i] != expected[i]]
        if diff:
            errors.append(f"{name}: расхождений с ожидаемым {len(diff)}")
        base = {i: t for i, (t, _e) in id_map(docs[name][0]).items()} if slot in FULL_SLOTS[game] else en
        changed = sum(1 for i in range(len(ids)) if got[i] != base.get(i, en[i]))
        untouched_bad = [i for i in range(len(ids)) if i not in ru and got[i] != base.get(i, en[i])]
        if untouched_bad:
            errors.append(f"{name}: изменены непереведённые ID: {len(untouched_bad)}")
        report["slots"][slot] = {"changed_lines": changed}
    en_got = {i: t for i, (t, _e) in id_map(parse(script_bytes(new_assets["TextData_en"]))[0]).items()}
    # теги, подстановки и переносы русских строк совпадают с матрицей побайтно (ru взят из матрицы)
    bad_sig = []
    for r in load_matrix(game):
        if r["status"] == "edited" or (r["status"] == "needs_context" and r.get("ru")):
            i = ids.index(r["text_id"]) if False else None
    index_of = {tid: i for i, tid in enumerate(ids)}
    exact = 0
    for r in load_matrix(game):
        if r["status"] == "edited" or (r["status"] == "needs_context" and r.get("ru")):
            i = index_of[r["text_id"]]
            if en_got[i] == r["ru"]:
                exact += 1
            if signature(en_got[i]) != signature(r["ru"]) or en_got[i].count("\n") != r["ru"].count("\n"):
                bad_sig.append(r["key"])
    if exact != len(ru):
        errors.append(f"русских строк, совпавших с матрицей побайтно: {exact} из {len(ru)}")
    if bad_sig:
        errors.append(f"расхождение тегов/подстановок/переносов: {bad_sig[:5]}")
    report.update({"ok": not errors, "errors": errors, "lines_included": len(ru), "exact_match": exact,
                   "changed_expected_en": changed_expected, "ids": len(ids)})
    return report


# ---------------------------------------------------------------- сборка

def cmd_build(_args):
    sys.stdout.reconfigure(encoding="utf-8")
    if cmd_originals(_args):
        return 1
    if DIST.exists():
        shutil.rmtree(DIST)
    manifest = {"build": "test_v2.0", "built_at": datetime.datetime.now().isoformat(timespec="seconds"),
                "steam_buildid": steam_buildid(), "files": []}
    for t in plan():
        original = original_path(t)
        out = DIST / t["relative"]
        if t["kind"] == "text":
            info = build_text_bundle(t["game"], original, out)
            check = verify_text_bundle(t["game"], original, out)
        elif t["kind"] == "font":
            info = pilot.build_font(original, out, font_pin(t["relative"]))
            check = pilot.check_font(original, out)
        else:
            info, check = build_extra(t, original, out)
        if not check["ok"]:
            raise SystemExit(f"{t['relative']}: проверка сборки не пройдена: {check['errors']}")
        row = {"game": t["game"], "kind": t["kind"], "bundle": t["bundle"], "relative": t["relative"],
               "font": t.get("font"), "original_sha256": t["original_sha256"], "built_sha256": sha256_file(out),
               "size_original": original.stat().st_size, "size_built": out.stat().st_size, "info": info,
               "check": {k: v for k, v in check.items() if k != "slots"}}
        manifest["files"].append(row)
        print(f"{t['game']} {t['kind']:7s} {t['label']:22s} {row['size_original']:>10} → {row['size_built']:>10}  проверка: ok")
    write_json(DIST / "manifest.json", manifest)
    print(f"Сборка: {DIST}")
    return 0


def cmd_verify_build(_args):
    sys.stdout.reconfigure(encoding="utf-8")
    manifest = dist_manifest()
    if manifest is None:
        raise SystemExit("Нет dist/test_v2.0: сначала `build`.")
    summary = {"files": [], "ok": True}
    for t in plan():
        built = DIST / t["relative"]
        row = next(f for f in manifest["files"] if f["relative"] == t["relative"])
        problems = []
        if sha256_file(built) != row["built_sha256"]:
            problems.append("хеш не совпадает с manifest")
        original = original_path(t)
        if t["kind"] == "text":
            res = verify_text_bundle(t["game"], original, built)
            problems += res["errors"]
            extra = {"lines_included": res["lines_included"], "exact_match": res["exact_match"],
                     "changed_per_slot": {s: v["changed_lines"] for s, v in res["slots"].items()}}
        elif t["kind"] == "font":
            res = pilot.check_font(original, built)
            problems += res["errors"]
            extra = {k: res[k] for k in ("cyrillic_mean_before", "cyrillic_mean_after", "widest_ratio")}
        else:
            tmp_out = WORK / "build" / "verify_tmp" / t["relative"]
            _info, res = build_extra(t, original, tmp_out)
            problems += res["errors"]
            if sha256_file(tmp_out) != row["built_sha256"]:
                problems.append("повторная сборка даёт другой файл (недетерминированно)")
            extra = {k: v for k, v in res.items() if k not in ("errors", "ok")}
        summary["files"].append({"relative": t["relative"], "kind": t["kind"], "ok": not problems, "problems": problems, **extra})
        summary["ok"] = summary["ok"] and not problems
        print(f"{t['game']} {t['kind']:7s} {t['label']:22s} {'ok' if not problems else problems}  {extra}")
    write_json(WORK / "build" / "verify_build.json", summary)
    return 0 if summary["ok"] else 1


# ---------------------------------------------------------------- состояние и установка

def classify(t, built_sha, pilot_map):
    path = t["path"]
    if not path.exists():
        return "нет файла"
    sha = sha256_file(path)
    if sha == t["original_sha256"]:
        return "оригинал"
    if built_sha and sha == built_sha:
        return "v2.0"
    if sha in pilot_map.get(t["relative"], ()):
        return "пилот или прежняя сборка v2.0"
    return "НЕИЗВЕСТНО"


def cmd_status(_args):
    sys.stdout.reconfigure(encoding="utf-8")
    manifest = dist_manifest() or {"files": []}
    built = {f["relative"]: f["built_sha256"] for f in manifest["files"]}
    pm = pilot_hashes()
    for t in plan():
        print(f"{t['game']} {t['kind']:7s} {t['label']:22s} {classify(t, built.get(t['relative']), pm)}")
    print("Steam buildid:", steam_buildid(), "(в сборке:", manifest.get("steam_buildid"), ")")
    print("Игра запущена:", bool(pilot.running_games()))
    return 0


def authorization():
    data = json.loads(APPROVALS.read_text(encoding="utf-8"))
    auth = (data.get("installs") or {}).get(INSTALL)
    if not auth:
        raise SystemExit(f"Нет разрешения installs.{INSTALL} в {APPROVALS}: установка тестовой сборки в игру запрещена.")
    return auth


def preflight(manifest):
    problems = []
    if pilot.running_games():
        problems.append("игра запущена")
    if steam_buildid() != manifest.get("steam_buildid"):
        problems.append(f"версия игры (buildid {steam_buildid()}) не совпадает с версией сборки ({manifest.get('steam_buildid')})")
    built = {f["relative"]: f["built_sha256"] for f in manifest["files"]}
    pm = pilot_hashes()
    for t in plan():
        state = classify(t, built.get(t["relative"]), pm)
        if state == "НЕИЗВЕСТНО":
            problems.append(f"файл в неизвестном состоянии (не оригинал, не пилот, не v2.0): {t['relative']}")
        elif state == "нет файла":
            problems.append(f"нет файла {t['relative']}")
        elif pilot.locked(t["path"]):
            problems.append(f"файл занят другим процессом: {t['relative']}")
        if (DIST / t["relative"]).exists() is False:
            problems.append(f"нет собранного файла {t['relative']}")
        elif sha256_file(DIST / t["relative"]) != built.get(t["relative"]):
            problems.append(f"собранный файл повреждён: {t['relative']}")
    try:
        for t in plan():
            original_path(t)
    except SystemExit as error:
        problems.append(str(error))
    return problems


def tree_snapshot(exclude):
    """Размер и время изменения всех файлов игры, кроме плана: доказательство «ничего лишнего не тронуто»."""
    snap = {}
    skip = {str(Path(e).resolve()) for e in exclude}
    for root, _dirs, files in os.walk(GAME_ROOT):
        for f in files:
            p = Path(root) / f
            if str(p.resolve()) in skip:
                continue
            st = p.stat()
            snap[str(p.relative_to(GAME_ROOT))] = (st.st_size, int(st.st_mtime))
    return snap


def cmd_check(_args):
    sys.stdout.reconfigure(encoding="utf-8")
    manifest = dist_manifest()
    if manifest is None:
        raise SystemExit("Нет dist/test_v2.0: сначала `build`.")
    problems = preflight(manifest)
    cmd_status(_args)
    print("Предустановочные проверки: " + ("пройдены" if not problems else "; ".join(problems)))
    try:
        authorization()
        print("Разрешение владельца: есть")
    except SystemExit as error:
        print("Разрешение владельца: нет —", error)
    return 1 if problems else 0


def cmd_install(_args):
    sys.stdout.reconfigure(encoding="utf-8")
    auth = authorization()
    manifest = dist_manifest()
    if manifest is None:
        raise SystemExit("Нет dist/test_v2.0: сначала `build`.")
    problems = preflight(manifest)
    if problems:
        raise SystemExit("Установка остановлена: " + "; ".join(problems))
    targets = plan()
    paths = [t["path"] for t in targets]
    snap_before = tree_snapshot(paths)
    assembly_before = pilot.assembly_hashes(list(GAMES))
    built = {f["relative"]: f["built_sha256"] for f in manifest["files"]}
    pm = pilot_hashes()
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    folder = BACKUPS / f"full_{stamp}"
    if folder.exists():
        raise SystemExit(f"Папка копий уже существует: {folder}")
    files = []
    for t in targets:
        state = classify(t, built[t["relative"]], pm)
        dst = folder / t["relative"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(t["path"], dst)
        sha = sha256_file(dst)
        if sha != sha256_file(t["path"]):
            raise SystemExit(f"Копия не совпадает: {dst}")
        os.chmod(dst, stat.S_IREAD)
        files.append({"relative": t["relative"], "game": t["game"], "kind": t["kind"], "state_before": state,
                      "backup": str(dst), "backup_sha256": sha, "original_sha256": t["original_sha256"],
                      "new_sha256": built[t["relative"]]})
    print(f"Резервные копии текущего состояния: {folder}")
    if pilot.running_games():
        raise SystemExit("Игра запущена — в игру ничего не записано.")
    allowed = {p.resolve() for p in paths}
    for t in targets:
        pilot.write_into_game(t["path"], DIST / t["relative"], allowed)
    errors = []
    for f in files:
        if sha256_file(GAME_ROOT / f["relative"]) != f["new_sha256"]:
            errors.append(f"хеш после установки не совпадает: {f['relative']}")
    snap_after = tree_snapshot(paths)
    if snap_after != snap_before:
        errors.append("изменились посторонние файлы игры: " + ", ".join(sorted(set(snap_after) ^ set(snap_before))[:5]))
    assembly_after = pilot.assembly_hashes(list(GAMES))
    if assembly_after != assembly_before:
        errors.append("GameAssembly.dll изменился")
    log = {"install": INSTALL, "decision": auth.get("decision"), "installed_at": datetime.datetime.now().isoformat(timespec="seconds"),
           "backup_folder": str(folder), "originals_folder": str(ORIGINALS), "files": files,
           "other_files_unchanged": snap_after == snap_before, "game_assembly_sha256": {"before": assembly_before, "after": assembly_after},
           "errors": errors}
    write_json(LOGS / f"full_{stamp}.json", log)
    print("Установлено файлов:", len(files), "; проверка:", "пройдена" if not errors else errors)
    return 1 if errors else 0


def cmd_verify(_args):
    sys.stdout.reconfigure(encoding="utf-8")
    manifest = dist_manifest()
    built = {f["relative"]: f["built_sha256"] for f in manifest["files"]}
    bad = [t["relative"] for t in plan() if not t["path"].exists() or sha256_file(t["path"]) != built[t["relative"]]]
    print("Установленные файлы совпадают со сборкой v2.0" if not bad else f"Не совпадают: {bad}")
    return 1 if bad else 0


def cmd_restore(_args):
    sys.stdout.reconfigure(encoding="utf-8")
    if pilot.running_games():
        raise SystemExit("Игра запущена: закройте обе игры и повторите откат.")
    targets = plan()
    for t in targets:
        original_path(t)
    allowed = {t["path"].resolve() for t in targets}
    bad = []
    for t in targets:
        pilot.write_into_game(t["path"], original_path(t), allowed)
        if sha256_file(t["path"]) != t["original_sha256"]:
            bad.append(t["relative"])
    print("Откат: " + (f"все {len(targets)} файлов возвращены к оригиналу, хеши совпадают с эталоном" if not bad else f"ОШИБКИ: {bad}"))
    return 1 if bad else 0


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("originals", "build", "verify-build", "status", "check", "install", "verify", "restore"):
        sub.add_parser(name)
    args = parser.parse_args()
    return {"originals": cmd_originals, "build": cmd_build, "verify-build": cmd_verify_build, "status": cmd_status,
            "check": cmd_check, "install": cmd_install, "verify": cmd_verify, "restore": cmd_restore}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
