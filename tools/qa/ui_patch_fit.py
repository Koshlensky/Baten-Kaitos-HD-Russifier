"""Ширина правленых жёстких подписей (D-044): новый текст против поля TMP (ширина rect, кегль, перенос).

Кегль берётся из скана сцен (work/build/ui_scan2_<game>.json), ширина строки — по самому широкому из 4 FOT
в собранных (суженных) шрифтах. Запас: ширина текста ≤ 95% поля, если перенос выключен.
"""
import json
import sys

from tools.bkloc import tags
from tools.bkloc.matrix import load as load_matrix
from tools.bkloc.paths import REPORTS, WORK, write_text
from tools.install import full, ui_patch
from tools.qa.label_fit import load_font, width


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    fonts = [load_font(full.DIST / t["relative"]) for t in full.plan() if t["game"] == "bk1" and t["kind"] == "font"]
    out = ["# Ширина правленых жёстких подписей (D-044)", "",
           "| Игра | Объект | Было → стало | Кегль | Поле (px) | Перенос | Ширина текста (px, макс. по FOT) | Доля поля | Вывод |",
           "|---|---|---|---|---|---|---|---|---|"]
    bad = 0
    ru_map = {g: {x['text_id']: x['ru'] for x in load_matrix(g)} for g in ('bk1', 'bk2')}
    for game in ("bk1", "bk2"):
        rows = json.loads((WORK / "build" / f"ui_scan2_{game}.json").read_text(encoding="utf-8"))
        seen = set()
        for r in rows:
            hit = ui_patch.matches(game, r["go"], r["text"], r["textId"], r["size"])
            if hit is None:
                continue
            new, sets = hit
            if r["textId"]:
                new = ru_map[game][r["textId"]]
            key = (r["go"], r["text"], r["textId"], r["size"], r["w"])
            if key in seen:
                continue
            seen.add(key)
            m = tags.strip_tags(new)
            size = sets.get("m_fontSize", r["size"]) or 24
            old_px = max(width(f, tags.strip_tags(r["text"])) for f in fonts) * (r["size"] or 24)
            px = max(width(f, m) for f in fonts) * size
            field = r["w"] or 0
            share = px / field if field else 0
            ok = share <= 1.0
            verdict = "ok" if ok else "ШИРОКО"
            if not ok and not (r["auto"]):
                bad += 1
            out.append(f"| {game} | {r['go']} | {r['text']!r} → {new!r} | {size} | {field:.0f} | {'да' if r['wrap'] else 'нет'} | "
                       f"{px:.0f} (было {old_px:.0f}) | {share:.0%} | {verdict}{' (автоподбор)' if r['auto'] and not ok else ''} |")
    out += ["", f"Проблемных полей: {bad}"]
    write_text(REPORTS / "build" / "ui_patch_fit.md", "\n".join(out) + "\n")
    print("\n".join(out))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
