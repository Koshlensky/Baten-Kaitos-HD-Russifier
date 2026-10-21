"""Make Cyrillic proportional in the game's CJK fonts.

The remaster renders all non-CJK languages with Japanese fonts whose
Cyrillic glyphs are full-width. This narrows their advance widths and
shifts outlines so spacing matches the Latin letters.
"""
import io
import os
import shutil
import sys

import UnityPy
from fontTools.misc.transform import Offset
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.t2CharStringPen import T2CharStringPen
from fontTools.pens.transformPen import TransformPen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont

sys.stdout.reconfigure(encoding="utf-8")

GAME_ROOT = None   # пути задаёт tools/bkloc/fonts.py
SUB = None
BACKUP = None
OUT_DIR = None

FONT_BUNDLES = [
    "3f5054c359dec0113387d921efa3d18e",  # FOT-ModeMinAStd-B (serif subtitles)
    "f848a65bf8b4bbfcc47553c458cf7161",  # FOT-NewRodinPro-DB (dialogue)
    "a9d38e7719f3248029b1f8c84bb9c237",  # FOT-NewRodinProN-M
    "0d9a198edca9e12232362a07dc95a843",  # FOT-NewCinemaAStd-D
    "ff2a6bb90153c7695b18aed370afc81b",  # NotoSerifKR-Bold
    "864cf696c58d55445406359a1331f4e7",  # NanumBarunGothicUltraLight
    "a47c4183f78d7352fe1602e63a7987a1",  # MaruBuri-SemiBold
    "6811d938951947b5adb0f9a4e11382ca",  # TUDShinGoSCGbe-DeB
    "ac83e961a51c15479fd8f362e1e5ec79",  # TUDShinGoSCGbe-Med
]

# Characters that appear in the translation and must become proportional.
TARGET_CODEPOINTS = set(range(0x0400, 0x0500))
TARGET_CODEPOINTS.update(ord(c) for c in "«»—–…№")


def glyph_bounds(font, glyph_set, name):
    pen = BoundsPen(glyph_set)
    glyph_set[name].draw(pen)
    return pen.bounds


def latin_margins(font, glyph_set, cmap, upm):
    """Average side bearings of Latin lowercase as the spacing target."""
    samples = []
    hmtx = font["hmtx"]
    for ch in "nohmeasuvz":
        gname = cmap.get(ord(ch))
        if not gname:
            continue
        bounds = glyph_bounds(font, glyph_set, gname)
        if not bounds:
            continue
        xmin, _, xmax, _ = bounds
        adv, _ = hmtx[gname]
        samples.append((xmin, adv - xmax))
    if not samples:
        return upm * 0.06, upm * 0.06
    left = sum(s[0] for s in samples) / len(samples)
    right = sum(s[1] for s in samples) / len(samples)
    left = max(10.0, left)
    right = max(10.0, right)
    return left, right


def shift_cff_glyph(font, name, dx, new_advance):
    cff = font["CFF "].cff
    top = cff[cff.fontNames[0]]
    charstrings = top.CharStrings
    glyph_set = font.getGlyphSet()
    private = charstrings[name].private
    pen = T2CharStringPen(new_advance, glyph_set)
    glyph_set[name].draw(TransformPen(pen, Offset(-dx, 0)))
    charstrings[name] = pen.getCharString(private=private, globalSubrs=charstrings.globalSubrs)


def shift_glyf_glyph(font, name, dx):
    glyph_set = font.getGlyphSet()
    pen = TTGlyphPen(glyph_set)
    glyph_set[name].draw(TransformPen(pen, Offset(-dx, 0)))
    font["glyf"][name] = pen.glyph()


def patch_font(raw):
    font = TTFont(io.BytesIO(raw), recalcTimestamp=False)   # D-052: не менять head.modified, сборка детерминирована
    cmap = font.getBestCmap()
    upm = font["head"].unitsPerEm
    glyph_set = font.getGlyphSet()
    hmtx = font["hmtx"]
    left_margin, right_margin = latin_margins(font, glyph_set, cmap, upm)
    is_cff = "CFF " in font
    full = upm * 0.85
    changed = 0
    seen = set()
    for cp in sorted(TARGET_CODEPOINTS):
        gname = cmap.get(cp)
        if not gname or gname in seen:
            continue
        seen.add(gname)
        adv, lsb = hmtx[gname]
        if adv < full:
            continue
        bounds = glyph_bounds(font, glyph_set, gname)
        if not bounds:
            continue
        xmin, _, xmax, _ = bounds
        width = xmax - xmin
        if width <= 0:
            continue
        dx = xmin - left_margin
        new_advance = int(round(width + left_margin + right_margin))
        if is_cff:
            shift_cff_glyph(font, gname, dx, new_advance)
        else:
            shift_glyf_glyph(font, gname, dx)
        hmtx[gname] = (new_advance, int(round(left_margin)))
        changed += 1
    if changed and "hhea" in font:
        font["hhea"].advanceWidthMax = max(adv for adv, _lsb in hmtx.metrics.values())
    if changed and not is_cff:
        # keep glyf bounding boxes and loca consistent
        font["glyf"].compile(font)
    buf = io.BytesIO()
    font.save(buf)
    return buf.getvalue(), changed


def verify(raw, label):
    font = TTFont(io.BytesIO(raw))
    cmap = font.getBestCmap()
    hmtx = font["hmtx"]

    def adv(ch):
        gname = cmap.get(ord(ch))
        return hmtx[gname][0] if gname else None

    print(f"  {label}: о={adv('о')} А={adv('А')} ы={adv('ы')} Ш={adv('Ш')} —={adv('—')}")


def main():
    os.makedirs(BACKUP, exist_ok=True)
    os.makedirs(OUT_DIR, exist_ok=True)
    for game_dir in ("Batenkaitos1", "Batenkaitos2"):
        folder = os.path.join(GAME_ROOT, game_dir, SUB)
        print(game_dir)
        for bundle_hash in FONT_BUNDLES:
            path = os.path.join(folder, bundle_hash + ".bundle")
            if not os.path.isfile(path):
                print("  missing", bundle_hash)
                continue
            backup = os.path.join(BACKUP, bundle_hash + ".bundle.bak")
            if not os.path.isfile(backup):
                shutil.copy2(path, backup)
            env = UnityPy.load(path)
            font_obj = None
            for obj in env.objects:
                if obj.type.name == "Font":
                    font_obj = obj.read()
                    break
            if font_obj is None:
                print("  no Font in", bundle_hash)
                continue
            raw = bytes(font_obj.m_FontData)
            patched, changed = patch_font(raw)
            if changed == 0:
                print(f"  {font_obj.m_Name}: already proportional")
                continue
            font_obj.m_FontData = patched
            font_obj.save()
            # Keep the original LZ4HC compression. The default resave writes
            # an uncompressed bundle, which this game fails to load.
            blob = env.file.save(packer="original")
            with open(path, "wb") as handle:
                handle.write(blob)
            cache = os.path.join(OUT_DIR, bundle_hash + ".bundle")
            shutil.copy2(path, cache)
            print(f"  {font_obj.m_Name}: {changed} glyphs narrowed, bundle {len(blob) // 1024} KB")
            verify(patched, font_obj.m_Name)


if __name__ == "__main__":
    main()
