"""Проверка патча ширины кириллицы v1.0 на копиях шрифтовых бандлов.

Логика патча берётся без изменений из legacy_v1/patch_fonts.py (функция
patch_font). Здесь только ввод-вывод: копии из игры в work/fonts/original,
результат в work/fonts/patched, отчёт с метриками до и после.
"""
import argparse
import importlib.util
import io
import json
import sys

import UnityPy
from fontTools.ttLib import TTFont

from tools.bkloc.paths import FONT_BUNDLES, GAMES, ROOT, WORK, bundle_path, copy_from_game, sha256_file, write_bytes, write_json

SAMPLE = "оАыШ—«»…ab"


def load_legacy_patch():
    spec = importlib.util.spec_from_file_location("legacy_patch_fonts", ROOT / "legacy_v1" / "patch_fonts.py")  # в публичном дереве файл очищен от личных путей
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.patch_font


def advances(raw):
    font = TTFont(io.BytesIO(raw))
    cmap = font.getBestCmap()
    hmtx = font["hmtx"]
    result = {}
    for ch in SAMPLE:
        name = cmap.get(ord(ch))
        result[ch] = hmtx[name][0] if name else None
    return result


def compare_games():
    rows = []
    for bundle in FONT_BUNDLES:
        a = sha256_file(bundle_path("bk1", bundle + ".bundle"))
        b = sha256_file(bundle_path("bk2", bundle + ".bundle"))
        rows.append({"bundle": bundle, "font": FONT_BUNDLES[bundle], "same_in_both_games": a == b, "sha256_bk1": a})
    return rows


def run(game):
    patch_font = load_legacy_patch()
    original_dir = WORK / "fonts" / "original"
    patched_dir = WORK / "fonts" / "patched"
    report = {"game": game, "fonts": [], "games_identical": compare_games()}
    for bundle, label in FONT_BUNDLES.items():
        src = bundle_path(game, bundle + ".bundle")
        copy = copy_from_game(src, original_dir / (bundle + ".bundle"))
        env = UnityPy.load(str(copy))
        font_obj = next((o.read() for o in env.objects if o.type.name == "Font"), None)
        if font_obj is None:
            report["fonts"].append({"bundle": bundle, "error": "Font не найден"})
            continue
        raw = bytes(font_obj.m_FontData)
        patched, changed = patch_font(raw)
        entry = {"bundle": bundle, "font": font_obj.m_Name, "expected": label, "glyphs_narrowed": changed, "before": advances(raw)}
        if changed:
            font_obj.m_FontData = patched
            font_obj.save()
            blob = env.file.save(packer="original")
            out = patched_dir / (bundle + ".bundle")
            write_bytes(out, blob)
            check = UnityPy.load(str(out))
            reread = next(o.read() for o in check.objects if o.type.name == "Font")
            entry["after"] = advances(bytes(reread.m_FontData))
            entry["bundle_size_original"] = copy.stat().st_size
            entry["bundle_size_patched"] = len(blob)
            entry["reload_ok"] = True
        report["fonts"].append(entry)
        print(f"{label}: сужено {changed}, до {entry['before'].get('о')} после {entry.get('after', {}).get('о')}", flush=True)
    write_json(WORK / "fonts" / "report.json", report)
    return report


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--game", default="bk1", choices=sorted(GAMES))
    args = parser.parse_args()
    run(args.game)


if __name__ == "__main__":
    main()
