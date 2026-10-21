"""Извлечение текстов обеих игр из копий бандлов (файлы игры только читаются).

Результат для каждой игры:
  source/<game>/raw/<Asset>.json   байт-в-байт содержимое TextAsset
  source/<game>/lines.jsonl        одна строка на ID: en + справочные языки
  source/<game>/manifest.json      хеши бандлов, ассетов, счётчики, проверки
"""
import datetime
import json
import sys
from pathlib import Path

import UnityPy

from tools.bkloc.catalog import Catalog
from tools.bkloc.paths import (
    CATALOG_SUB, FONT_BUNDLES, GAME_ROOT, GAMES, REFERENCE_LANGS, ROOT, SOURCE, WORK, game_dir, bundle_path, copy_from_game, sha1_text,
    sha256_bytes, sha256_file, text_bundle_path, write_bytes, write_json, write_jsonl,
)

PLACEHOLDER = "(empty)"


def pick_original(path):
    """Файл игры или, если игра уже русифицирована установщиком, сохранённый им оригинал (_rus_v2/originals).

    Эталонные хеши берутся из compat/buildid_*.json (есть в публичном дереве); без него возвращается сам файл."""
    path = Path(path)
    comp = sorted((ROOT / "compat").glob("buildid_*.json"))
    if not comp:
        return path
    want = {f["relative"]: f["original_sha256"] for f in json.loads(comp[-1].read_text(encoding="utf-8"))["files"]}
    rel = path.relative_to(GAME_ROOT).as_posix()
    if rel not in want or sha256_file(path) == want[rel]:
        return path
    alt = GAME_ROOT / "_rus_v2" / "originals" / rel
    if alt.exists() and sha256_file(alt) == want[rel]:
        return alt
    raise SystemExit(f"Файл игры изменён и оригинал не найден: {rel}. Верните оригинальные файлы (удаление русификатора или "
                     "«Проверка целостности» в Steam) и повторите.")


def script_bytes(data):
    script = data.m_Script
    if isinstance(script, (bytes, bytearray)):
        return bytes(script)
    return script.encode("utf-8", "surrogateescape")


def parse(raw):
    has_bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw[3:].decode("utf-8") if has_bom else raw.decode("utf-8")
    return json.loads(text), has_bom, text


def roundtrip_identical(obj, text):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")) == text


def id_map(doc):
    mapping = {}
    for entry_index, entry in enumerate(doc["array"]):
        for i in entry["i"]:
            mapping[i] = (entry["t"], entry_index)
    return mapping


def extract(game):
    copy = copy_from_game(pick_original(text_bundle_path(game)), WORK / "copies" / game / GAMES[game]["text_bundle"])
    env = UnityPy.load(str(copy))
    assets = {}
    for obj in env.objects:
        if obj.type.name == "TextAsset":
            data = obj.read()
            assets[data.m_Name] = script_bytes(data)

    out = SOURCE / game
    manifest_assets = []
    docs = {}
    for name in sorted(assets):
        raw = assets[name]
        write_bytes(out / "raw" / f"{name}.json", raw)
        obj, has_bom, text = parse(raw)
        docs[name] = obj
        info = {"name": name, "bytes": len(raw), "sha256": sha256_bytes(raw), "bom": has_bom,
                "roundtrip_identical": roundtrip_identical(obj, text)}
        if name.startswith("TextData_"):
            mapping = id_map(obj)
            filled = sum(1 for t, _ in mapping.values() if t.strip() not in ("", PLACEHOLDER))
            info.update({"entries": len(obj["array"]), "ids_covered": len(mapping), "ids_with_text": filled})
        else:
            info.update({"ids": len(obj["textId"])})
        manifest_assets.append(info)

    ids = docs["TextIDList"]["textId"]
    langs = ["en"] + REFERENCE_LANGS[game]
    maps = {lang: id_map(docs[f"TextData_{lang}"]) for lang in langs}
    lines = []
    for index, text_id in enumerate(ids):
        en, entry_index = maps["en"].get(index, ("", -1))
        row = {"idx": index, "id": text_id, "en_entry": entry_index, "en": en, "en_sha1": sha1_text(en)}
        for lang in langs[1:]:
            row[lang] = maps[lang].get(index, ("", -1))[0]
        lines.append(row)
    write_jsonl(out / "lines.jsonl", lines)

    for slot_name, doc in docs.items():
        if slot_name.startswith("TextData_") and slot_name != "TextData_en":
            missing = len(ids) - len(id_map(doc))
            next(a for a in manifest_assets if a["name"] == slot_name)["ids_missing"] = missing

    catalog = Catalog(game, pick_original(game_dir(game) / CATALOG_SUB))
    fonts = []
    for bundle, label in FONT_BUNDLES.items():
        path = pick_original(bundle_path(game, bundle + ".bundle"))
        fonts.append({"bundle": bundle + ".bundle", "font": label, "bytes": path.stat().st_size, "sha256": sha256_file(path)})
    manifest = {
        "game": game,
        "title": GAMES[game]["title"],
        "extracted_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "unitypy": UnityPy.__version__,
        "text_bundle": {
            "name": GAMES[game]["text_bundle"],
            "relative_path": str(text_bundle_path(game).relative_to(text_bundle_path(game).parents[5])),
            "bytes": copy.stat().st_size,
            "sha256": sha256_file(copy),
            "catalog_options": catalog.bundle_options(GAMES[game]["text_bundle"]),
        },
        "font_bundles": fonts,
        "text_ids": len(ids),
        "reference_langs": REFERENCE_LANGS[game],
        "assets": manifest_assets,
    }
    write_json(out / "manifest.json", manifest)
    return manifest


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ok = True
    for game in GAMES:
        manifest = extract(game)
        en = next(a for a in manifest["assets"] if a["name"] == "TextData_en")
        bad = [a["name"] for a in manifest["assets"] if not a["roundtrip_identical"]]
        print(f"{game}: ID {manifest['text_ids']}, EN записей {en['entries']}, ID с текстом {en['ids_with_text']}, "
              f"ассетов {len(manifest['assets'])}, round-trip не совпал: {bad or 'нет'}")
        ok = ok and en["ids_covered"] == manifest["text_ids"]
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
