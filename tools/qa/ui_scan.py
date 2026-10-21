"""Скан TMP-текстов UI с размерами: textId, текст, кегль, rect, перенос, переполнение, автоподбор.\n\nИспользование: python -m tools.qa.ui_scan bk1|bk2  → work/build/ui_scan2_<игра>.json (нужен для tools.qa.ui_fit и ui_patch_fit).\n"""
import json, re, sys
import UnityPy
from tools.bkloc.catalog import Catalog
from tools.bkloc.paths import bundle_path

game = sys.argv[1]
cat = Catalog(game)
bundles = {}
for i in cat.internal_ids:
    if i.endswith((".prefab", ".unity")) and ("/UI/" in i or "/Scenes/GameModes/" in i or "BattleMaps/BATTLE.unity" in i):
        b = cat.bundle_of(i)
        if b:
            bundles.setdefault(b, []).append(i.split("Assets/")[-1])
out = []
for n, (b, names) in enumerate(bundles.items()):
    try:
        env = UnityPy.load(str(bundle_path(game, b)))
    except Exception as e:
        continue
    objs = {(o.assets_file.name, o.path_id): o for o in env.objects}
    for o in env.objects:
        if o.type.name != "GameObject":
            continue
        d = o.read()
        tmp = tid = rt = None
        for c in d.m_Components:
            oo = objs.get((o.assets_file.name, c.m_PathID))
            if oo is None:
                continue
            if oo.type.name == "RectTransform":
                rt = oo.read_typetree()
            elif oo.type.name == "MonoBehaviour":
                try:
                    t = oo.read_typetree()
                except Exception:
                    continue
                if "m_text" in t:
                    tmp = t
                if "textId" in t and len(t) <= 8:
                    tid = t["textId"]
        if tmp is None:
            continue
        out.append({
            "asset": names[0], "bundle": b, "go": d.m_Name, "text": tmp["m_text"], "textId": tid,
            "size": tmp.get("m_fontSize"), "wrap": tmp.get("m_enableWordWrapping"), "overflow": tmp.get("m_overflowMode"),
            "auto": tmp.get("m_enableAutoSizing"), "smin": tmp.get("m_fontSizeMin"), "smax": tmp.get("m_fontSizeMax"),
            "align": tmp.get("m_HorizontalAlignment", tmp.get("m_textAlignment")),
            "w": rt and rt["m_SizeDelta"]["x"], "h": rt and rt["m_SizeDelta"]["y"],
            "ax": rt and (rt["m_AnchorMin"]["x"], rt["m_AnchorMax"]["x"]),
        })
json.dump(out, open(f"work/build/ui_scan2_{game}.json", "w", encoding="utf-8"), ensure_ascii=False)
print(game, len(bundles), "бандлов,", len(out), "TMP,", sum(1 for x in out if x["textId"]), "с textId")
