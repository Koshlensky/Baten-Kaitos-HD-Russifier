"""Общие пути проекта и защита от записи в папку игры."""
import hashlib
import json
import os
import shutil
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
def _detect_game_root():
    """BK_GAME_ROOT, иначе автопоиск Steam-версии, иначе заглушка (команды, которым нужна игра, сообщат об этом)."""
    env = os.environ.get("BK_GAME_ROOT")
    if env:
        return Path(env)
    try:
        from tools.release import core
        found = core.find_games()
        if found:
            return found[0]
    except Exception:
        pass
    return Path("BK_GAME_ROOT_не_задан")


GAME_ROOT = _detect_game_root()
# Публичное дерево (без английских оригиналов) помечено файлом PUBLIC_TREE: матрица там хранится без поля en.
IS_PUBLIC = (ROOT / "PUBLIC_TREE").exists()
BUNDLE_SUB = Path("Batenkaitos_Data") / "StreamingAssets" / "aa" / "StandaloneWindows64"
CATALOG_SUB = Path("Batenkaitos_Data") / "StreamingAssets" / "aa" / "catalog.json"

GAMES = {
    "bk1": {
        "folder": "Batenkaitos1",
        "title": "Baten Kaitos: Eternal Wings and the Lost Ocean",
        "text_bundle": "6a872618f189b35a4039d59de4a885c3.bundle",
    },
    "bk2": {
        "folder": "Batenkaitos2",
        "title": "Baten Kaitos Origins",
        "text_bundle": "a8cc32bba98258300a54327273719c75.bundle",
    },
}

# Одинаковые шрифтовые бандлы в обеих играх (список из v1.0).
FONT_BUNDLES = {
    "3f5054c359dec0113387d921efa3d18e": "FOT-ModeMinAStd-B",
    "f848a65bf8b4bbfcc47553c458cf7161": "FOT-NewRodinPro-DB",
    "a9d38e7719f3248029b1f8c84bb9c237": "FOT-NewRodinProN-M",
    "0d9a198edca9e12232362a07dc95a843": "FOT-NewCinemaAStd-D",
    "ff2a6bb90153c7695b18aed370afc81b": "NotoSerifKR-Bold",
    "864cf696c58d55445406359a1331f4e7": "NanumBarunGothicUltraLight",
    "a47c4183f78d7352fe1602e63a7987a1": "MaruBuri-SemiBold",
    "6811d938951947b5adb0f9a4e11382ca": "TUDShinGoSCGbe-DeB",
    "ac83e961a51c15479fd8f362e1e5ec79": "TUDShinGoSCGbe-Med",
}

# Полные тексты: EN/JP/KR/SC/TC в обеих играх, FR/DE/ES/IT только в BK1.
REFERENCE_LANGS = {
    "bk1": ["jp", "fr", "de", "es", "it", "kr", "sc", "tc"],
    "bk2": ["jp", "kr", "sc", "tc"],
}
ALL_SLOTS = ["en", "jp", "kr", "sc", "tc", "fr", "de", "es", "it", "ru", "ar", "br", "ns", "pl", "th"]

SOURCE = ROOT / "source"
BASE = ROOT / "base"
MATRIX = BASE / "matrix"
SCENES = BASE / "scenes"
WORK = ROOT / "work"
DOCS = ROOT / "docs"
EXPORTS = ROOT / "exports"
REPORTS = ROOT / "reports"
RUNS = ROOT / "runs"


def game_dir(game):
    return GAME_ROOT / GAMES[game]["folder"]


def bundle_path(game, bundle_name):
    return game_dir(game) / BUNDLE_SUB / bundle_name


def text_bundle_path(game):
    return bundle_path(game, GAMES[game]["text_bundle"])


def _inside_game(path):
    try:
        Path(path).resolve().relative_to(GAME_ROOT.resolve())
        return True
    except ValueError:
        return False


def guard_write(path):
    """Запрещает любую запись внутрь установленной игры."""
    if _inside_game(path):
        raise PermissionError(f"Запись в папку игры запрещена: {path}")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    return Path(path)


def write_text(path, text):
    target = guard_write(path)
    # На Windows файл может быть на мгновение занят наблюдателем за файлами (EINVAL/EACCES).
    for attempt in range(5):
        try:
            target.write_text(text, encoding="utf-8", newline="\n")
            return
        except OSError as error:
            if attempt == 4 or error.errno not in (13, 22):
                raise
            time.sleep(0.5 * (attempt + 1))


def write_bytes(path, data):
    guard_write(path).write_bytes(data)


def write_json(path, obj, indent=1):
    write_text(path, json.dumps(obj, ensure_ascii=False, indent=indent) + "\n")


def write_jsonl(path, rows):
    if IS_PUBLIC:
        try:
            Path(path).resolve().relative_to(MATRIX.resolve())
            rows = [{k: v for k, v in r.items() if k != "en"} for r in rows]    # в публичное дерево английский текст не пишется
        except ValueError:
            pass
    write_text(path, "".join(json.dumps(r, ensure_ascii=False, sort_keys=False) + "\n" for r in rows))


def read_jsonl(path):
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def copy_from_game(src, dst):
    """Копирует файл игры в рабочую папку и проверяет, что копия совпадает."""
    guard_write(dst)
    shutil.copy2(src, dst)
    if sha256_file(src) != sha256_file(dst):
        raise RuntimeError(f"Копия не совпадает с оригиналом: {src}")
    return Path(dst)


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha1_text(text):
    return hashlib.sha1(text.encode("utf-8")).hexdigest()
