"""Оценка рисков по ширине и высоте текста для тестовой сборки (без запуска игры).

Реальные размеры окон и колонок неизвестны (они в префабах Unity), поэтому порог берётся из
самого английского оригинала: окна и поля, в которые помещается английский текст.
  * Диалоговые облачки: ширина окна W — максимальная ширина английской строки с явным переносом
    (99,9-й перцентиль); высота — максимальное число строк в английском облачке после переноса по W.
  * Интерфейс и названия: ширина русской строки против максимума английских строк той же категории
    и против английского оригинала.
Это оценка рисков для проверки в игре, а не доказанные ошибки. Перевод автоматически не сокращается.

    python -m tools.qa.fit_report
"""
import collections
import json
import re
import sys

from tools.bkloc import tags
from tools.bkloc.matrix import load as load_matrix
from tools.bkloc.paths import REPORTS, WORK, write_json, write_text
from tools.qa import fit

DIALOGUE = {"dialogue", "letter", "credits"}  # облачки диалогов; справка, описания и энциклопедия имеют свои окна с автопереносом
UI = {"ui", "ui_battle", "magnus_name", "item_name", "location_name", "npc_name", "enemy_name", "trophy", "party_name"}
PLACEHOLDER_SAMPLE = {"<n0>": "ЖЖЖЖЖЖЖЖ", "%s": "ЖЖЖЖЖЖЖЖЖЖ", "%d": "0000"}


def line_width(adv, upm, text):
    for token, sample in PLACEHOLDER_SAMPLE.items():
        text = text.replace(token, sample)
    text = re.sub(r"\$\$NAME_[A-Z]+\$\$", "ЖЖЖЖЖЖЖЖ", text)
    default = upm // 2
    return sum(adv.get(ord(ch), default) for ch in text) / upm


def wrap_lines(adv, upm, text, limit):
    """Число строк после жадного переноса по словам с учётом явных переносов."""
    count = 0
    for line in tags.strip_tags(text).split("\n"):
        words = line.split(" ")
        cur = 0.0
        rows = 1
        space = line_width(adv, upm, " ")
        for w in words:
            ww = line_width(adv, upm, w)
            if cur and cur + space + ww > limit:
                rows += 1
                cur = ww
            else:
                cur = cur + (space if cur else 0) + ww
        count += rows
    return count


def percentile(values, q):
    values = sorted(values)
    return values[min(len(values) - 1, int(len(values) * q))] if values else 0


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    adv, upm = fit.metrics()
    if adv is None:
        raise SystemExit("Нет метрик пропатченного шрифта: python -m tools.bkloc.fonts")
    out = {}
    lines_md = ["# Риски по ширине и высоте текста — тестовая сборка v2.0", "",
                "Оценка по метрикам пропатченного диалогового шрифта FOT-NewRodinPro-DB (сумма advance в «эм»). "
                "Пороги выведены из английского оригинала: то, что помещается в английскую версию, считается допустимым "
                "размером окна/поля. **Это предупреждения для проверки в игре, а не доказанные ошибки.** "
                "Перевод не сокращался.", ""]
    for game in ("bk1", "bk2"):
        recs = [r for r in load_matrix(game) if r["status"] in ("edited", "needs_context") and r.get("ru")]
        # --- диалоги
        en_explicit, en_bubble_rows = [], []
        for r in recs:
            if r["category"] not in DIALOGUE:
                continue
            for b in tags.bubbles(r["en"]):
                text = tags.strip_tags(b)
                if "\n" in text.strip("\n"):
                    en_explicit += [line_width(adv, upm, ln) for ln in text.split("\n")]
        W = percentile(en_explicit, 0.999)
        for r in recs:
            if r["category"] not in DIALOGUE:
                continue
            for b in tags.bubbles(r["en"]):
                en_bubble_rows.append(wrap_lines(adv, upm, b, W))
        L = percentile(en_bubble_rows, 0.999)
        too_wide, too_tall = [], []
        for r in recs:
            if r["category"] not in DIALOGUE:
                continue
            for n, b in enumerate(tags.bubbles(r["ru"])):
                text = tags.strip_tags(b)
                explicit = [line_width(adv, upm, ln) for ln in text.split("\n")]
                over = max(explicit) - W
                rows = wrap_lines(adv, upm, b, W)
                if over > 0.5 and "\n" in text.strip("\n"):
                    too_wide.append((over, r["key"], n, text[:100].replace("\n", " / ")))
                if rows > L:
                    too_tall.append((rows - L, r["key"], n, rows, text[:100].replace("\n", " / ")))
        # --- интерфейс
        ui_rows = collections.defaultdict(list)
        for r in recs:
            if r["category"] in UI:
                en_w, ru_w = fit.width(r["en"]), fit.width(r["ru"])
                ui_rows[r["category"]].append((r["key"], en_w, ru_w, r["en"], r["ru"]))
        ui_summary, ui_risks = {}, []
        for cat, rows in ui_rows.items():
            en_max = percentile([x[1] for x in rows], 0.999)
            wide = [x for x in rows if x[2] > en_max * 1.05]
            ui_summary[cat] = {"lines": len(rows), "en_max_em": round(en_max, 1), "ru_wider_than_en_max": len(wide),
                               "ru_max_em": round(max(x[2] for x in rows), 1)}
            ui_risks += [(x[2] - en_max, cat, x[0], round(x[1], 1), round(x[2], 1), x[3][:50], x[4][:60]) for x in wide]
        too_wide.sort(reverse=True)
        too_tall.sort(reverse=True)
        ui_risks.sort(reverse=True)
        out[game] = {"window_width_em": round(W, 1), "window_max_rows": L,
                     "dialogue_lines_wider_than_window": len(too_wide), "dialogue_bubbles_taller_than_window": len(too_tall),
                     "ui": ui_summary, "ui_risk_count": len(ui_risks)}
        lines_md += [f"## {game.upper()}", "",
                     f"- Ширина окна по английскому оригиналу: **{W:.1f} эм**, максимум строк в облачке: **{L}**.",
                     f"- Явные строки русских облачков шире окна (> +0,5 эм): **{len(too_wide)}**.",
                     f"- Русские облачки выше окна после переноса: **{len(too_tall)}**.",
                     f"- Строк интерфейса и названий шире максимума своей категории в оригинале: **{len(ui_risks)}**.", "",
                     "| Категория | Строк | Макс. EN, эм | Макс. RU, эм | RU шире EN-макс. |", "|---|---:|---:|---:|---:|"]
        for cat, s in sorted(ui_summary.items()):
            lines_md.append(f"| {cat} | {s['lines']} | {s['en_max_em']} | {s['ru_max_em']} | {s['ru_wider_than_en_max']} |")
        lines_md += ["", "Самые широкие явные строки диалогов:", ""]
        for over, key, n, text in too_wide[:15]:
            lines_md.append(f"- `{key}` (+{over:.1f} эм): {text}")
        lines_md += ["", "Самые высокие облачки после переноса:", ""]
        for extra, key, n, rows, text in too_tall[:15]:
            lines_md.append(f"- `{key}` ({rows} строк, +{extra}): {text}")
        lines_md += ["", "Самые широкие строки интерфейса и названий:", ""]
        for over, cat, key, en_w, ru_w, en, ru in ui_risks[:25]:
            lines_md.append(f"- `{key}` [{cat}] {ru_w} эм против {en_w} (EN): «{en}» → «{ru}»")
        lines_md.append("")
        json.dump({"too_wide": [(o, k, n, t) for o, k, n, t in too_wide], "too_tall": [(e, k, n, r, t) for e, k, n, r, t in too_tall],
                   "ui_risks": ui_risks}, open(WORK / "build" / f"fit_{game}.json", "w", encoding="utf-8"), ensure_ascii=False)
    write_json(WORK / "build" / "fit_summary.json", out)
    write_text(REPORTS / "build" / "fit_risks.md", "\n".join(lines_md) + "\n")
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
