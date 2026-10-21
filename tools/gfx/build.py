"""Сборка графических замен BK1 (D-045): титульное меню и экран поражения. Только на копиях бандлов."""
import numpy as np
from PIL import Image

from tools.gfx import hp, neon, title, words
from tools.gfx.atlas import Atlas, decode_bc7

TITLE_PREFIX = "d4ec5297"
BATTLE_PREFIX = "b29e1677"
TITLE2_PREFIX = "4f9acb8c"
BATTLE2_PREFIX = "ed7a9de9"
ACTION_PREFIX = "24fd687e"
# вид замены → (игра, префикс бандла, имя шаблона полос)
KINDS = {"title": ("bk1", TITLE_PREFIX), "battle": ("bk1", BATTLE_PREFIX), "title2": ("bk2", TITLE2_PREFIX),
         "battle2": ("bk2", BATTLE2_PREFIX), "action": ("bk2", ACTION_PREFIX)}

TITLE_TEXTS = {
    0: "Нажмите любую кнопку",
    1: "Новая игра",
    2: "Расширенная новая игра",
    3: "Загрузить",
    4: "Лицензия",
    5: "Настройки",
    7: "Выход",
    8: "Условия использования",
    9: "Конфиденциальность",
}
TITLE_NAME = "BTK1_Title_%02d_EN__Ex__UC"
TITLE_NAME2 = "BTK2_Title_%02d_EN__Ex__UC"
TITLE_ZONE = (136, 504)

# (спрайт, слова, цвета ореола, высота заглавной, макс. ширина)
NEON_JOBS = [
    ("BTK1_Battle_TRYAGAIN__UC", ["ПОПРОБОВАТЬ", "СНОВА?"], [neon.BLUE, neon.PINK], 30, 420),
    ("BTK1_Battle_TRYAGAIN_YES__UC", ["ДА"], [neon.BLUE], 30, 120),
    ("BTK1_Battle_TRYAGAIN_NO__UC", ["НЕТ"], [neon.PINK], 30, 120),
    ("BTK1_Battle_GAMEOVER__UC", ["ИГРА", "ОКОНЧЕНА"], [neon.BLUE, neon.PINK], 30, 400),
]


def title_jobs(atlas, name_fmt=TITLE_NAME):
    strips = {k: atlas.read(name_fmt % k) for k in TITLE_TEXTS}
    template = title.background_template(list(strips.values()))
    ref = strips[3]
    _, bbox = title.erase_text(ref, template)
    grad = title.gradient_from_original(ref, bbox)
    cap = bbox[3] - bbox[1] + 1
    jobs = {}
    for k, text in TITLE_TEXTS.items():
        img, size = title.make_strip(strips[k], template, text, bbox, grad, cap)
        img.putalpha(strips[k].getchannel("A"))
        mask = np.zeros((48, 640), bool)
        mask[:, TITLE_ZONE[0]:TITLE_ZONE[1]] = True
        jobs[name_fmt % k] = {"image": img, "mask": mask, "text": text, "size": size, "original": strips[k]}
    return jobs


def neon_jobs(atlas):
    jobs = {}
    for name, words, colors, cap, mw in NEON_JOBS:
        orig = atlas.read(name)
        img, size = neon.render(orig.size, words, orig, cap, mw, colors)
        jobs[name] = {"image": img, "mask": None, "text": " ".join(words), "size": size, "original": orig}
    name = "BTK1_Battle_StatusFrame__UC"
    orig = atlas.read(name)
    img, mask = hp.render(orig)
    jobs[name] = {"image": img, "mask": mask, "text": hp.TEXT, "size": 20, "original": orig}
    return jobs


# BK2: слова-единицы после чисел (картам/ударам/урону). Форм «1/2–4/5+» в игре две (единственное и множественное число)
WORDS2 = {"BTK2_Battle_WordB_card__UC": "карта", "BTK2_Battle_WordB_cards__UC": "карт",
          "BTK2_Battle_WordB_hit__UC": "удар", "BTK2_Battle_WordB_hits__UC": "ударов", "BTK2_Battle_WordB_damage__UC": "урона"}
ACTION_NAME = "BTK2_Battle_ActionFrame__UC"
ACTION_TEXT = "ДЕЙСТВИЕ"
ACTION_ERASE = (40, 2, 196, 38)      # x0, y0, x1, y1 в координатах спрайта; стрелки рамки (x≤36 и x≥198) не затрагиваются


def words_jobs(atlas):
    jobs = {}
    for name, ru in WORDS2.items():
        orig = atlas.read(name)
        bb = words.footprint(orig)
        img, px = words.stroked(orig.size, ru, words.SERIF_BI, bb, slant=words.SLANT, max_h=orig.height - 5)
        jobs[name] = {"image": img, "mask": None, "text": ru, "size": round(px, 1), "original": orig, "mesh_check": True, "keep_alpha": False}
    return jobs


def action_jobs(atlas):
    orig = atlas.read(ACTION_NAME)
    x0, y0, x1, y1 = ACTION_ERASE
    arr = np.asarray(orig).copy()
    arr[y0:y1, x0:x1] = 0
    base = Image.fromarray(arr, "RGBA")
    layer, px = words.stroked(orig.size, ACTION_TEXT, words.SANS_NARROW_B, (44, 4, 192, 34), max_w=148, max_h=28, align="center",
                              fill_bottom=(214, 214, 220), outline_px=2.0, shadow=(0.0, 1.0, 0.35))
    base.alpha_composite(layer)
    mask = np.zeros((orig.height, orig.width), bool)
    mask[y0:y1, x0:x1] = True
    return {ACTION_NAME: {"image": base, "mask": mask, "text": ACTION_TEXT, "size": round(px, 1), "original": orig, "mesh_check": True, "keep_alpha": False}}


def build_atlas(kind, original, out):
    """kind: 'title' | 'battle'. Возвращает (info, jobs)."""
    game, prefix = KINDS[kind]
    atlas = Atlas(game, prefix, source=original)
    if kind == "battle":
        jobs = neon_jobs(atlas)
    elif kind == "battle2":
        jobs = words_jobs(atlas)
    elif kind == "action":
        jobs = action_jobs(atlas)
    else:
        jobs = title_jobs(atlas, TITLE_NAME2 if kind == "title2" else TITLE_NAME)
    for name, job in jobs.items():
        atlas.write(name, job["image"], job["mask"])
    stats = atlas.save(out)
    info = {"sprites": {n: {"text": j["text"], "font_px": j["size"]} for n, j in jobs.items()}, "blocks_reencoded": stats}
    return info, jobs


def verify_atlas(kind, original, built, jobs):
    """Проверка: объекты, кроме ресурсного файла, равны; нетронутые блоки текстуры равны побитно; новые спрайты совпадают с задуманными."""
    game, prefix = KINDS[kind]
    a = Atlas(game, prefix, source=original)
    b = Atlas(game, prefix, source=built)
    errors = []
    oa = {o.path_id: o for o in a.env.objects}
    ob = {o.path_id: o for o in b.env.objects}
    if set(oa) != set(ob):
        errors.append("набор объектов изменился")
    if [d["sprite"].m_Name for d in a.sprites.values()] != [d["sprite"].m_Name for d in b.sprites.values()]:
        errors.append("порядок или состав спрайтов изменился")
    for pid, o in oa.items():
        if pid in ob and o.get_raw_data() != ob[pid].get_raw_data():
            errors.append(f"объект {pid} ({o.type.name}) изменился")
    ra = {k: bytes(v.bytes) if hasattr(v, "bytes") else b"" for k, v in a.env.file.files.items() if k.endswith(".resS")}
    rb = {k: bytes(v.bytes) if hasattr(v, "bytes") else b"" for k, v in b.env.file.files.items() if k.endswith(".resS")}
    for k in ra:
        if len(ra[k]) != len(rb.get(k, b"")):
            errors.append(f"размер {k} изменился")
    # блоки, которые вправе измениться
    expected = {}
    for name in jobs:
        pid, x, y, w, h = a.region(name)
        if jobs[name]["mask"] is not None:
            m = jobs[name]["mask"][::-1]
            rows, cols = np.nonzero(m.any(axis=1))[0], np.nonzero(m.any(axis=0))[0]
            x, y, w, h = x + int(cols.min()), y + int(rows.min()), int(cols.max() - cols.min() + 1), int(rows.max() - rows.min() + 1)
        expected.setdefault(pid, []).append((x, y, w, h))
    report = {}
    for pid, rects in expected.items():
        pa, pb = a.texture(pid)["pix"].astype(int), b.texture(pid)["pix"].astype(int)
        allowed = np.zeros(pa.shape[:2], bool)
        for x, y, w, h in rects:
            allowed[(y // 4) * 4:((y + h - 1) // 4 + 1) * 4, (x // 4) * 4:((x + w - 1) // 4 + 1) * 4] = True
        diff = np.abs(pa - pb).max(axis=2)
        outside = int((diff[~allowed] > 0).sum())
        if outside:
            errors.append(f"изменились пиксели вне допустимых блоков: {outside}")
        # соседи: пиксели в затронутых блоках вне записанных масок
        own = np.zeros(pa.shape[:2], bool)
        for name, job in jobs.items():
            _, x, y, w, h = a.region(name)
            m = np.ones((h, w), bool) if job["mask"] is None else job["mask"][::-1]
            own[y:y + h, x:x + w] |= m
        stray = allowed & ~own
        report = {"pixels_changed_outside_blocks": outside,
                  "neighbour_pixels_in_touched_blocks_changed": int((diff[stray] > 0).sum()),
                  "neighbour_max_diff": int(diff[stray].max()) if stray.any() else 0}
    # новые спрайты равны задуманным
    worst = 0.0
    for name, job in jobs.items():
        got = np.asarray(b.read(name)).astype(int)
        want = np.asarray(job["image"].convert("RGBA")).astype(int)
        m = np.ones(got.shape[:2], bool) if job["mask"] is None else job["mask"]
        err = np.abs(got - want).max(axis=2)[m]
        worst = max(worst, float(err.mean()))
        if err.mean() > (10.0 if job.get("mesh_check") else 4.0):
            errors.append(f"{name}: средняя ошибка кодирования {err.mean():.2f}")
        orig_alpha = np.asarray(job["original"].convert("RGBA"))[..., 3].astype(int)
        if job.get("keep_alpha", True) and np.abs(got[..., 3] - orig_alpha)[m].max() > 8:
            errors.append(f"{name}: альфа-канал отличается от оригинала")
    report["mean_encoding_error_worst"] = round(worst, 3)
    # плотная сетка спрайта: отрисованный игрой спрайт должен сохранить почти всю новую надпись
    for name, job in jobs.items():
        if not job.get("mesh_check"):
            continue
        rendered = np.asarray(b.sprites[name]["sprite"].image.convert("RGBA"))[..., 3].astype(float).sum()
        if job["mask"] is None:
            intended = np.asarray(job["image"].convert("RGBA"))[..., 3].astype(float).sum()
        else:
            m = job["mask"]
            intended = np.asarray(job["image"].convert("RGBA"))[..., 3].astype(float)[m].sum() + np.asarray(job["original"].convert("RGBA"))[..., 3].astype(float)[~m].sum()
        ratio = rendered / max(intended, 1.0)
        report.setdefault("mesh_ratio", {})[name] = round(float(ratio), 3)
        if ratio < 0.95:
            errors.append(f"{name}: сетка спрайта обрезает надпись (сохранено {ratio:.0%})")
    return {"ok": not errors, "errors": errors[:10], **report}
