"""Технические заготовки для ручной подготовки PNG (D-045): оригинал, сетка-подсказка и паспорт каждого спрайта.

Это надписи, стиль которых (объём, обводка, свечение, фактура камня) нельзя надёжно воспроизвести программно.
Файлы лежат в work/gfx_manual/bk1 (не коммитятся: это графика игры); в git попадает только список.
"""
import json

from PIL import Image, ImageDraw

from tools.bkloc.paths import ROOT, write_json
from tools.gfx.atlas import Atlas

OUT = ROOT / "work" / "gfx_manual" / "bk1"
LIST = ROOT / "docs" / "gfx_manual_list.json"

# (спрайт, английский текст, русский текст, примечание)
ITEMS = [
    ("BTK1_BattleResult_Frame01__UC", "OFFENSE", "АТАКА", "табличка из камня: текст в рельефе, нужен новый рельеф на фактуре"),
    ("BTK1_BattleResult_Frame02__UC", "DEFENSE", "ЗАЩИТА", "то же"),
    ("BTK1_BattleResult_Frame03__UC", "RESULTS", "РЕЗУЛЬТАТЫ", "то же; 10 букв, возможно потребуется сжать буквы по горизонтали"),
    ("BTK1_BattleResult_FinalWord__UC", "FINAL RESULTS", "ИТОГИ БОЯ", "белый курсив с серым объёмом"),
    ("BTK1_BattleResult_FinalDamageWord__UC", "DAMAGE", "УРОН", "белые буквы с серой обводкой"),
    ("BTK1_BattleResult_FinalRecoverWord__UC", "RECOVER", "ВОССТАНОВЛЕНО ОЗ", "зелёный градиент с обводкой; 216×40 — текст придётся сжать, возможен вариант «ВОССТ. ОЗ»"),
    ("BTK1_BattleResult_PRIZE__UC", "PRIZE", "ПРИЗ", "золотые буквы на свитке"),
    ("BTK1_BattleResult_SPCOMBO__UC", "SP COMBO", "СПЕЦКОМБО", "белые буквы с чёрной обводкой на свитке"),
    ("BTK1_Battle_ComboMAX__UC", "COMBO MAX", "КОМБО МАКС", "красно-белый курсив"),
    ("BTK1_Battle_NEXT__UC", "NEXT", "ДАЛЕЕ", "красно-белый курсив"),
    ("BTK1_Battle_MagWord_Attack__UC", "Attack", "Атака", "красный градиент"),
    ("BTK1_Battle_MagWord_Guard__UC", "Guard", "Защита", "синий градиент"),
    ("BTK1_Battle_MagWord_GuardEne__UC", "Guard (enemy)", "Защита", "тёмно-синий вариант"),
    ("BTK1_Battle_ResultFrame_Hit__UC", "Hit", "Удар", "оранжевое свечение"),
    ("BTK1_Battle_ResultFrame_Hits__UC", "Hits", "Удары", "то же"),
    ("BTK1_Battle_ResultFrame_Guard__UC", "Guard", "Защита", "то же"),
    ("BTK1_Battle_ResultFrame_Guards__UC", "Guards", "Защиты", "то же"),
    ("BTK1_BattleResult_PrizeWord01__UC", "Cards", "нужен термин", "золотая подпись комбинации призов"),
    ("BTK1_BattleResult_PrizeWord02__UC", "Cards   Pairs", "нужен термин", "то же"),
    ("BTK1_BattleResult_PrizeWord03__UC", "Cards with 9", "нужен термин", "то же; цифра 9 — часть спрайта"),
    ("BTK1_BattleResult_PrizeWord04__UC", "Straight", "нужен термин", "то же"),
    ("BTK1_BattleResult_PrizeWord05__UC", "Straight with 9", "нужен термин", "то же"),
    ("BTK1_BattleResult_PrizeWord06__UC", "Voice", "нужен термин", "то же"),
    ("BTK1_BattleResult_PrizeWord07__UC", "Final Straight Sunset", "нужен термин", "то же"),
    ("BTK1_BattleResult_PrizeWord08__UC", "Final Straight Sunrise", "нужен термин", "то же"),
]


def grid(img, step=50):
    bg = Image.new("RGBA", img.size, (70, 70, 90, 255))
    bg.alpha_composite(img)
    d = ImageDraw.Draw(bg)
    for x in range(0, img.width, step):
        d.line([(x, 0), (x, img.height)], fill=(255, 255, 0, 90), width=1)
        d.text((x + 2, 2), str(x), fill=(255, 255, 0, 255))
    for y in range(0, img.height, step):
        d.line([(0, y), (img.width, y)], fill=(0, 255, 255, 90), width=1)
        d.text((2, y + 2), str(y), fill=(0, 255, 255, 255))
    return bg


def main():
    (OUT / "originals").mkdir(parents=True, exist_ok=True)
    (OUT / "guides").mkdir(parents=True, exist_ok=True)
    atlas = Atlas("bk1", "b29e1677")
    rows = []
    for name, en, ru, note in ITEMS:
        sprite = atlas.sprites[name]
        img = sprite["sprite"].image.convert("RGBA")
        img.save(OUT / "originals" / f"{name}.png")
        grid(img).save(OUT / "guides" / f"{name}.png")
        r = sprite["data"].textureRect
        rows.append({"sprite": name, "english": en, "russian": ru, "note": note, "size": [img.width, img.height],
                     "atlas_rect": [round(r.x, 3), round(r.y, 3), round(r.width, 3), round(r.height, 3)],
                     "atlas_bundle": "b29e1677d0e548f3b74e980a7fb8b638.bundle", "mode": "RGBA PNG, размер не менять, альфа сохранить"})
    write_json(LIST, {"bundle": "b29e1677d0e548f3b74e980a7fb8b638.bundle", "items": rows})
    print(len(rows), "заготовок →", OUT)


if __name__ == "__main__":
    main()
