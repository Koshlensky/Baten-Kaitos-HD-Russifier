"""Сопоставление русских подписей UI с реальными размерами TMP-объектов (скан work/build/ui_scan2_<game>.json).

Для каждого TMP с textId берётся русский текст из матрицы, ширина считается по метрикам собранного
(пропатченного) диалогового шрифта; сравнивается с шириной rect объекта и переносом/автоподбором.

    python -m tools.qa.ui_fit
"""
import json
import re
import sys

from tools.bkloc import tags
from tools.bkloc.matrix import load as load_matrix
from tools.bkloc.paths import REPORTS, WORK, write_json, write_text
from tools.qa import fit

LATIN = re.compile(r"[A-Za-z]{2,}")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    adv, upm = fit.metrics()
    md = ["# Русские подписи UI против реальных размеров TMP-объектов", "",
          "Ширина по метрикам FOT-NewRodinPro-DB; rect — `sizeDelta` объекта в сцене/префабе; кегль — `m_fontSize`. "
          "Это оценка (родительские масштабы и точный шрифтовой ассет TMP не учитывались, но текст и rect лежат в одной иерархии).", ""]
    summary = {}
    for game in ("bk1", "bk2"):
        recs = {r["text_id"]: r for r in load_matrix(game)}
        rows = json.load(open(WORK / "build" / f"ui_scan2_{game}.json", encoding="utf-8"))
        flagged, hardcoded = [], []
        for x in rows:
            tid = x["textId"]
            text = x["text"] or ""
            if tid and tid in recs:
                ru = recs[tid]["ru"]
                en = recs[tid]["en"]
                w_ru = fit.width(ru) or 0
                w_en = fit.width(en) or 0
                px_ru, px_en = w_ru * (x["size"] or 0), w_en * (x["size"] or 0)
                rect = x["w"] or 0
                wrap = bool(x["wrap"])
                lines = 1 + ru.count("\n")
                import math
                hgt = x["h"] or 0
                if wrap and rect:
                    need = math.ceil(px_ru / rect) * (x["size"] or 0) * 1.25
                    bad = need > hgt and math.ceil(px_ru / rect) > math.ceil(px_en / rect)
                elif x["auto"] and x["smin"] and rect:
                    bad = (px_ru * (x["smin"] / x["size"])) > rect
                else:
                    bad = px_ru > rect and px_ru > px_en * 1.05
                if bad:
                    flagged.append({"h": hgt, "lines": math.ceil(px_ru / rect) if rect else None, "go": x["go"], "asset": x["asset"], "textId": tid, "en": en[:40], "ru": ru[:50], "px_en": round(px_en), "px_ru": round(px_ru),
                                    "rect": rect, "wrap": wrap, "overflow": x["overflow"], "auto": x["auto"], "size": x["size"],
                                    "over_pct": round(100 * (px_ru / rect - 1)) if rect else None})
            elif not tid and LATIN.search(text):
                hardcoded.append({"go": x["go"], "asset": x["asset"], "text": text[:30], "size": x["size"], "rect": x["w"]})
        seen_f = set()
        uniq = []
        for f in flagged:
            k = (f["go"], f["textId"], f["rect"], f["size"])
            if k not in seen_f:
                seen_f.add(k)
                uniq.append(f)
        flagged = sorted(uniq, key=lambda f: -(f["over_pct"] or 0))
        summary[game] = {"tmp_with_textid": sum(1 for x in rows if x["textId"]), "flagged": len(flagged), "hardcoded_latin": len(hardcoded)}
        md += [f"## {game.upper()}", "", f"- TMP с textId: {summary[game]['tmp_with_textid']}; русский текст шире rect: **{len(flagged)}**; "
               f"зашитая латиница без textId: **{len(hardcoded)}**.", "",
               "| Объект | Сцена/префаб | textId | EN → RU | px EN → RU | rect | перенос | автоподбор | превышение |", "|---|---|---|---|---|---|---|---|---|"]
        for f in flagged[:60]:
            md.append(f"| {f['go']} | {f['asset'].split('/')[-1]} | {f['textId']} | {f['en']} → {f['ru']} | {f['px_en']} → {f['px_ru']} | {f['rect']:.0f} | "
                      f"{'да' if f['wrap'] else 'нет'} | {'да' if f['auto'] else 'нет'} | {f['over_pct']}% |")
        md += ["", "Зашитые английские TMP без textId:", ""]
        seen = set()
        for h in hardcoded:
            k = (h["text"], h["go"])
            if k in seen or h["text"].strip() in ("DummyDummy", "Dummy Dummy", "Dummy", "(Dummy)"):
                continue
            seen.add(k)
            md.append(f"- `{h['go']}` «{h['text']}» ({h['asset'].split('/')[-1]}, кегль {h['size']}, rect {h['rect']})")
        md.append("")
        json.dump({"flagged": flagged, "hardcoded": hardcoded}, open(WORK / "build" / f"ui_fit_{game}.json", "w", encoding="utf-8"), ensure_ascii=False)
    write_text(REPORTS / "build" / "ui_fit.md", "\n".join(md) + "\n")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
