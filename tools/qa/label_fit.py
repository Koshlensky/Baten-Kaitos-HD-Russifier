"""Проверка глифов и ширины новых подписей характеристик BK1 по собранным (пропатченным) шрифтам.

Сравниваются: английская подпись, русская подпись и эталонные русские надписи той же панели,
которые заведомо видны в игре (скриншот владельца): «Магнусы в руке», «Макс. Комбо».
Отчёт: reports/build/stat_labels_fit.md.

    python -m tools.qa.label_fit
"""
import io
import sys

import UnityPy
from fontTools.ttLib import TTFont

from tools.bkloc import tags
from tools.bkloc.matrix import load as load_matrix
from tools.bkloc.paths import FONT_BUNDLES, REPORTS, bundle_path, write_text
from tools.install import full

KEYS = [f"bk1:SYS_{n:04d}" for n in (385, 387, 391, 395, 397, 398, 399, 400, 411, 416, 481)] + \
       [f"bk1:Help_info_{n:03d}" for n in (105, 106, 107, 108, 109, 110, 113, 117)] + ["bk1:SYS_0319", "bk1:SYS_0320"]
REFERENCE = {"bk1:Help_info_038": "Магнусы в руке (виден на панели)", "bk1:SYS_0405": "Макс. Комбо (виден на панели)"}


def load_font(path):
    env = UnityPy.load(str(path))
    font = next(o.read() for o in env.objects if o.type.name == "Font")
    tt = TTFont(io.BytesIO(bytes(font.m_FontData)))
    return tt.getBestCmap(), tt["hmtx"], tt["head"].unitsPerEm


def width(font, text):
    cmap, hmtx, upm = font
    return sum(hmtx[cmap[ord(ch)]][0] for ch in text if ord(ch) in cmap) / upm


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    rows = {r["key"]: r for r in load_matrix("bk1")}
    fonts = {}
    for t in full.plan():
        if t["game"] == "bk1" and t["kind"] == "font":
            fonts[t["font"]] = load_font(full.DIST / t["relative"])
    out = ["# Новые подписи характеристик BK1: глифы и ширина", "",
           "Шрифты — собранные (пропатченные) FOT из `dist/test_v2.0`. Ширина в «эм» (сумма advance / unitsPerEm). "
           "Какой именно из четырёх шрифтов рисует панель, без запуска игры неизвестно, поэтому приведены все.", ""]
    used = set()
    for k in KEYS:
        used |= set(tags.strip_tags(rows[k]["ru"]))
    used |= set("Магнусы в руке Макс. Комбо")
    used.discard("\n")
    out += ["## Глифы", "", f"Символов в подписях: {len(used)} ({''.join(sorted(used))})", "",
            "| Шрифт | Нет символов |", "|---|---|"]
    ok = True
    for name, f in fonts.items():
        missing = sorted(ch for ch in used if ord(ch) not in f[0])
        ok = ok and not missing
        out.append(f"| {name} | {''.join(missing) or '—'} |")
    out += ["", "## Ширина", "", "| Ключ | EN | RU | " + " | ".join(f"{n.replace('FOT-', '')} EN→RU" for n in fonts) + " |",
            "|---|---|---|" + "---|" * len(fonts)]
    for k in KEYS + list(REFERENCE):
        r = rows[k]
        en, ru = tags.strip_tags(r["en"]), tags.strip_tags(r["ru"])
        cells = [f"{width(f, en):.2f} → {width(f, ru):.2f}" for f in fonts.values()]
        label = ru if k not in REFERENCE else f"{ru} (эталон)"
        out.append(f"| `{k}` | {en} | {label} | " + " | ".join(cells) + " |")
    ref = max(width(next(iter(fonts.values())), tags.strip_tags(rows[k]["ru"])) for k in REFERENCE)
    worst = max((width(next(iter(fonts.values())), tags.strip_tags(rows[k]["ru"])), k) for k in KEYS[:11])
    out += ["", f"Самая широкая подпись панели (SYS): {worst[1]} — {worst[0]:.2f} эм; эталон, уже помещающийся на панели: {ref:.2f} эм."]
    write_text(REPORTS / "build" / "stat_labels_fit.md", "\n".join(out) + "\n")
    print("\n".join(out))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
