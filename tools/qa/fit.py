"""Оценка ширины строк по метрикам пропатченного диалогового шрифта.

Ширина считается в «эм» (сумма ширин глифов / unitsPerEm). Пороги для окон и полей
интерфейса калибруются на пилоте; до этого сравнивается ширина перевода с шириной
оригинала и с максимумом ширины в той же группе строк.
"""
import io
import re

import UnityPy
from fontTools.ttLib import TTFont

from tools.bkloc import tags
from tools.bkloc.paths import WORK

DIALOGUE_FONT = "f848a65bf8b4bbfcc47553c458cf7161"
PLACEHOLDER_WIDTH = {"<n0>": "ЖЖЖЖЖЖЖЖ", "%s": "ЖЖЖЖЖЖЖЖЖЖ", "%d": "0000"}
FIT_CATEGORIES = {"ui", "ui_battle", "magnus_name", "item_name", "location_name", "npc_name", "enemy_name", "trophy"}
_METRICS = None


def metrics():
    """(advance по коду символа, unitsPerEm) из work/fonts/patched; None, если патч ещё не собран."""
    global _METRICS
    if _METRICS is None:
        path = WORK / "fonts" / "patched" / f"{DIALOGUE_FONT}.bundle"
        if not path.exists():
            _METRICS = (None, None)
            return _METRICS
        env = UnityPy.load(str(path))
        font = next(o.read() for o in env.objects if o.type.name == "Font")
        tt = TTFont(io.BytesIO(bytes(font.m_FontData)))
        cmap, hmtx = tt.getBestCmap(), tt["hmtx"]
        _METRICS = ({cp: hmtx[name][0] for cp, name in cmap.items()}, tt["head"].unitsPerEm)
    return _METRICS


def width(text):
    adv, upm = metrics()
    if adv is None:
        return None
    for token, sample in PLACEHOLDER_WIDTH.items():
        text = text.replace(token, sample)
    text = re.sub(r"\$\$NAME_[A-Z]+\$\$", "ЖЖЖЖЖЖЖЖ", text)
    lines = tags.strip_tags(text).split("\n")
    default = upm // 2
    return max(sum(adv.get(ord(ch), default) for ch in line) for line in lines) / upm


def check_glyphs(r):
    """Символы перевода, которых нет в диалоговом шрифте."""
    adv, _upm = metrics()
    if adv is None or not r.get("ru"):
        return []
    missing = sorted({ch for ch in tags.strip_tags(r["ru"]) if ch not in "\n\t" and ord(ch) not in adv})
    return [("error", "glyph_missing", "нет в шрифте: " + " ".join(missing))] if missing else []


def check_fit(r, group_max):
    """Предупреждение, если строка интерфейса заметно шире оригинала или максимума своей группы."""
    if r["category"] not in FIT_CATEGORIES or not r.get("ru"):
        return []
    w_en, w_ru = width(r["en"]), width(r["ru"])
    if w_en is None or w_ru is None:
        return [("info", "fit_skipped", "нет метрик шрифта: запустите python -m tools.bkloc.fonts")]
    out = []
    if w_en > 0 and w_ru > w_en * 1.25 and w_ru - w_en > 1.0:
        out.append(("warning", "fit_wider", f"шире оригинала: {w_ru:.1f} эм против {w_en:.1f}"))
    limit = group_max.get(r["_group"])
    if limit and w_ru > limit * 1.1:
        out.append(("warning", "fit_group_max", f"шире самой широкой строки группы: {w_ru:.1f} > {limit:.1f} эм"))
    return out
