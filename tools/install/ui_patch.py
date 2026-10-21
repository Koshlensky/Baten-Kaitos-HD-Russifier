"""Правка жёстко заданных TMP-подписей в сценах и префабах (D-044), только на копиях.

Меняется один параметр — `m_text` у TextMeshPro-компонента, у которого нет `textId`, а имя
GameObject и исходный текст точно совпадают со спецификацией. Все остальные объекты бандла
сверяются побайтно. Для префабов с ненулевым `m_Crc` в каталоге Addressables CRC обнуляется
заменой одинаковой длины (значение дополняется пробелами), чтобы смещения таблицы не менялись.
"""
import base64
import json
import re
from pathlib import Path

import UnityPy

from tools.bkloc.catalog import Catalog
from tools.bkloc.paths import CATALOG_SUB, GAME_ROOT, ROOT, game_dir, sha256_file

TARGETS = ROOT / "tools" / "install" / "ui_targets.json"
ORIGINALS = ROOT / "backups" / "originals_v2"


def original_or_game(path):
    """Эталон из backups/originals_v2, если он есть (игра может быть уже обновлена), иначе файл игры."""
    path = Path(path)
    cand = ORIGINALS / path.relative_to(GAME_ROOT)
    return cand if cand.exists() else path

# (имя GameObject, исходный текст, новый текст[, условия и правки размера])
SPEC = {
    "bk1": [("Gold_Text", "G", " з."), ("Info_Text", "info", "Инфо"),
            ("DeckText", "<size=28>Deck", "<size=28>Колода"),
            ("TOTALText", "TOTAL", "ИТОГО"), ("AmountHPsText", "Amount of HPs Recovered", "Восстановлено ОЗ")],
    "bk2": [("Info_Text", "info", "Инфо"), ("StockText", "Stock", "Запас"), ("TargetText", "TARGET", "ЦЕЛЬ"),
            ("TotalText", "TOTAL", "ИТОГО"), ("Unit", "GOLD", " з."),
            ("Gold", "GOLD", "Золото", {"only_size": 20.0, "size": 18.0}),
            ("Gold", "GOLD", "Золото", {"only_size": 30.0})],
}

# Правка размера шрифта у подписей с textId (текст берётся из TextData): (имя объекта, textId, был кегль, стал кегль)
SIZES = {
    "bk1": [],
    "bk2": [("JoinText", "CMNSTR_239", 20.0, 18.0), ("ActiveDeckText", "TREE_VIEW_01", 33.0, 31.0)],
}

def tmp_objects(env):
    """[(path_id, имя GameObject, typetree TMP, есть ли textId)]"""
    objs = {(o.assets_file.name, o.path_id): o for o in env.objects}
    rows = []
    for o in env.objects:
        if o.type.name != "GameObject":
            continue
        d = o.read()
        tmp = None
        tmp_obj = None
        tid = None
        for c in d.m_Components:
            oo = objs.get((o.assets_file.name, c.m_PathID))
            if oo is None or oo.type.name != "MonoBehaviour":
                continue
            try:
                t = oo.read_typetree()
            except Exception:
                continue
            if "m_text" in t:
                tmp, tmp_obj = t, oo
            if "textId" in t and len(t) <= 8:
                tid = t["textId"]
        if tmp is not None:
            rows.append((tmp_obj, d.m_Name, tmp, tid))
    return rows


def matches(game, name, text, tid, size=None):
    """Возвращает (новый текст, {поле: значение}) или None."""
    if tid:
        for go, tid_, was, now in SIZES[game]:
            if name == go and tid == tid_ and size == was:
                return text, {"m_fontSize": now, "m_fontSizeBase": now}
        return None
    for entry in SPEC[game]:
        go, old, new = entry[:3]
        cond = entry[3] if len(entry) > 3 else {}
        if name != go or text != old:
            continue
        if "only_size" in cond and size != cond["only_size"]:
            continue
        sets = {"m_fontSize": cond["size"], "m_fontSizeBase": cond["size"]} if "size" in cond else {}
        return new, sets
    return None

def find_targets(game):
    """Бандлы с совпадениями (читается из каталога и самой игры; результат записывается в ui_targets.json)."""
    from tools.bkloc.paths import bundle_path
    cat = Catalog(game, original_or_game(game_dir(game) / CATALOG_SUB))
    bundles = {}
    for i in cat.internal_ids:
        if i.endswith((".prefab", ".unity")) and ("/UI/" in i or "/Scenes/GameModes/" in i or "BattleMaps/BATTLE.unity" in i):
            b = cat.bundle_of(i)
            if b:
                bundles.setdefault(b, []).append(i.split("Assets/")[-1])
    found = []
    for b, names in bundles.items():
        env = UnityPy.load(str(original_or_game(bundle_path(game, b))))
        hits = [(n, t["m_text"]) for _o, n, t, tid in tmp_objects(env) if matches(game, n, t["m_text"], tid, t.get("m_fontSize"))]
        if hits:
            opts = cat.bundle_options(b) or {}
            found.append({"bundle": b, "assets": names[:2], "hits": len(hits), "crc": opts.get("m_Crc", 0)})
    return found


def patch_bundle(game, original, out):
    """Возвращает список изменений; сохраняет бандл. Проверка — verify_bundle."""
    env = UnityPy.load(str(original))
    changes = []
    for obj, name, tmp, tid in tmp_objects(env):
        hit = matches(game, name, tmp["m_text"], tid, tmp.get("m_fontSize"))
        if hit is None:
            continue
        new, sets = hit
        if sets and tmp.get("m_enableAutoSizing"):
            continue
        old = tmp["m_text"]
        tmp["m_text"] = new
        for key, value in sets.items():
            if key in tmp:
                tmp[key] = value
        obj.save_typetree(tmp)
        changes.append({"path_id": obj.path_id, "file": obj.assets_file.name, "go": name, "old": old, "new": new, "set": sets})
    if not changes:
        raise SystemExit(f"{original.name}: нечего менять")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(env.file.save(packer="original"))
    return changes


def verify_bundle(game, original, built, expected_changes):
    """Все объекты, кроме правленых TMP, побайтно равны оригиналу; у правленых изменены только m_text и кегль."""
    errors = []
    a = UnityPy.load(str(original))
    b = UnityPy.load(str(built))
    ao = {(o.assets_file.name, o.path_id): o for o in a.objects}
    bo = {(o.assets_file.name, o.path_id): o for o in b.objects}
    if set(ao) != set(bo):
        errors.append("набор объектов изменился")
    changed = {(c["file"], c["path_id"]): c for c in expected_changes}
    for pid, o in ao.items():
        x = bo.get(pid)
        if x is None:
            continue
        if pid in changed:
            ta, tb = o.read_typetree(), x.read_typetree()
            expect = dict(ta)
            expect["m_text"] = changed[pid]["new"]
            for key, value in changed[pid]["set"].items():
                if key in expect:
                    expect[key] = value
            if tb != expect:
                errors.append(f"объект {pid}: изменилось не только m_text/кегль")
        elif o.get_raw_data() != x.get_raw_data():
            errors.append(f"объект {pid} ({o.type.name}) изменился")
    names_a = sorted(a.file.files) if hasattr(a.file, "files") else []
    names_b = sorted(b.file.files) if hasattr(b.file, "files") else []
    if names_a != names_b:
        errors.append("состав файлов бандла изменился")
    return {"ok": not errors, "errors": errors[:10], "changed_objects": len(changed)}

# ---------------------------------------------------------------- каталог

def patch_catalog(game, bundles, out, source):
    """Обнуляет m_Crc выбранных бандлов заменой одинаковой длины. Возвращает {bundle: старый CRC}."""
    cat = Catalog(game, source)
    path = source
    text = path.read_text(encoding="utf-8")
    extra = bytearray(base64.b64decode(json.loads(text)["m_ExtraDataString"]))
    old_b64 = re.search(r'"m_ExtraDataString"\s*:\s*"([^"]*)"', text).group(1)
    done = {}
    for b in bundles:
        opts = cat.bundle_options(b)
        crc = opts.get("m_Crc", 0)
        if not crc:
            continue
        needle = ('"m_Crc":%d' % crc).encode("utf-16le")
        hash_needle = ('"m_Hash":"%s"' % opts["m_Hash"]).encode("utf-16le")
        pos = bytes(extra).find(hash_needle)
        if pos < 0:
            raise SystemExit(f"{b}: не найден блок параметров")
        start = bytes(extra).rfind(b"{\x00", 0, pos)
        end = bytes(extra).find(b"}\x00", pos) + 2
        block = bytes(extra[start:end])
        if block.count(needle) != 1:
            raise SystemExit(f"{b}: m_Crc не найден однозначно")
        repl = ('"m_Crc":0' + " " * (len('"m_Crc":%d' % crc) - len('"m_Crc":0'))).encode("utf-16le")
        extra[start:end] = block.replace(needle, repl)
        done[b] = crc
    new_b64 = base64.b64encode(bytes(extra)).decode("ascii")
    if len(new_b64) != len(old_b64):
        raise SystemExit("длина m_ExtraDataString изменилась")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text.replace(old_b64, new_b64, 1), encoding="utf-8", newline="")
    return done


def verify_catalog(game, built, bundles, source):
    """Каталог читается, отличается только m_Crc нужных бандлов (стали 0), остальное равно."""
    errors = []
    orig = json.loads(Path(source).read_text(encoding="utf-8"))
    new = json.loads(Path(built).read_text(encoding="utf-8"))
    for k in orig:
        if k != "m_ExtraDataString" and orig[k] != new[k]:
            errors.append(f"изменилось поле {k}")
    if len(orig["m_ExtraDataString"]) != len(new["m_ExtraDataString"]):
        errors.append("длина ExtraData изменилась")
    cat_o = Catalog(game, source)
    cat_n = Catalog.__new__(Catalog)
    cat_n.__dict__ = dict(cat_o.__dict__)
    cat_n._extra = base64.b64decode(new["m_ExtraDataString"])
    for b in bundles:
        o, n = cat_o.bundle_options(b), cat_n.bundle_options(b)
        if n is None or n.get("m_Crc") != 0:
            errors.append(f"{b}: CRC не обнулён")
        for k in o:
            if k != "m_Crc" and o[k] != n.get(k):
                errors.append(f"{b}: изменилось {k}")
    # все остальные бандлы без изменений
    for entry in cat_o.entries:
        if entry["data_index"] >= 0:
            p = cat_o.entry_path(entry).split("\\")[-1]
            if p in bundles:
                continue
            if cat_o._read_object(cat_o._extra, entry["data_index"]) != cat_n._read_object(cat_n._extra, entry["data_index"]):
                errors.append(f"{p}: параметры изменились")
                break
    return {"ok": not errors, "errors": errors[:10]}


def relative(path):
    return str(Path(path).relative_to(GAME_ROOT))
