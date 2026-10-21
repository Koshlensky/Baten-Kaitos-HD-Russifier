"""Проверка сборки: русский документ по ID записывается в КОПИЮ текстового бандла.

Файлы игры не изменяются: бандл копируется в work/build_check/<game>/ с проверкой хеша,
документ записывается во все слоты TextData_* (как в v1.0), бандл сохраняется с исходным
сжатием и перечитывается; сверяются содержимое и покрытие всех ID.
"""
import json
import sys

import UnityPy

from tools.bkloc.build_text import build_document
from tools.bkloc.extract import script_bytes
from tools.bkloc.paths import GAME_ROOT, GAMES, ROOT, SOURCE, WORK, sha256_file, text_bundle_path, write_bytes


def original_bundle(game):
    """Исходный текстовый бандл: файл игры, если он совпадает с эталоном, иначе сверенная резервная копия."""
    reference = json.loads((SOURCE / game / "manifest.json").read_text(encoding="utf-8"))["text_bundle"]["sha256"]
    path = text_bundle_path(game)
    if sha256_file(path) == reference:
        return path
    relative = path.relative_to(GAME_ROOT)
    for backup in sorted((ROOT / "backups").glob("*"), reverse=True):
        candidate = backup / relative
        if candidate.exists() and sha256_file(candidate) == reference:
            return candidate
    raise SystemExit(f"Нет исходного текстового бандла {game} с эталонным хешем")


def check_game(game, min_status="draft"):
    doc, payload, stats = build_document(game, min_status)
    result = {"game": game, "stats": stats, "errors": []}
    covered = sorted(i for entry in doc["array"] for i in entry["i"])
    if covered != list(range(stats["ids"])):
        result["errors"].append("документ покрывает не все ID или дублирует их")
    source = original_bundle(game)
    result["source"] = str(source)
    copy = WORK / "build_check" / game / GAMES[game]["text_bundle"]
    write_bytes(copy, source.read_bytes())
    env = UnityPy.load(str(copy))
    slots = 0
    for obj in env.objects:
        if obj.type.name == "TextAsset":
            data = obj.read()
            if data.m_Name.startswith("TextData_"):
                data.m_Script = payload
                data.save()
                slots += 1
    blob = env.file.save(packer="original")
    out = WORK / "build_check" / game / ("built_" + GAMES[game]["text_bundle"])
    write_bytes(out, blob)
    check = UnityPy.load(str(out))
    readback = None
    for obj in check.objects:
        if obj.type.name == "TextAsset":
            data = obj.read()
            if data.m_Name == "TextData_ru":
                readback = script_bytes(data).decode("utf-8")
    if readback != payload:
        result["errors"].append("документ после перечитывания не совпадает")
    result.update({"slots_written": slots, "bundle_bytes_original": copy.stat().st_size, "bundle_bytes_built": len(blob)})
    return result


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    min_status = sys.argv[1] if len(sys.argv) > 1 else "draft"
    failed = False
    for game in GAMES:
        res = check_game(game, min_status)
        print(json.dumps(res, ensure_ascii=False))
        failed = failed or bool(res["errors"])
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
