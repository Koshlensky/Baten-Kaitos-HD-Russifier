"""Замена «HP» → «ОЗ» в спрайте панели персонажа боя BK1 (D-048).

Фон панели ровный (63,18,21): область под надписью заливается этим цветом (с тем же лёгким градиентом по строкам),
затем рисуется «ОЗ» белым шрифтом Segoe UI Semibold с тёмной обводкой, как у оригинала. Правится только прямоугольник вокруг надписи.
"""
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

FONT = r"C:\Windows\Fonts\seguisb.ttf"
BOX = (134, 56, 176, 80)        # x0, y0, x1, y1 (сверху вниз), без полосы индикатора ниже y=81
TEXT = "ОЗ"
SUPER = 4


def render(img):
    a = np.asarray(img.convert("RGBA")).copy()
    x0, y0, x1, y1 = BOX
    arr = a.astype(float)
    # фон: среднее по строкам из чистых колонок справа от надписи (x 178..200)
    ref = arr[y0:y1, 178:200, :3].mean(axis=1)
    arr[y0:y1, x0:x1, :3] = ref[:, None, :]
    # оригинальная надпись: ядро x140..168, y63..76 (14 px)
    cap = 14
    size = int(round(cap / 0.7))
    font = ImageFont.truetype(FONT, size * SUPER)
    layer = Image.new("L", (img.width * SUPER, img.height * SUPER), 0)
    d = ImageDraw.Draw(layer)
    box = font.getbbox(TEXT)
    tw = (box[2] - box[0]) / SUPER
    left = 140 + (28 - tw) / 2.0
    top = 63
    d.text((left * SUPER - box[0], top * SUPER - box[1]), TEXT, font=font, fill=255)
    m = layer.resize(img.size, Image.LANCZOS)
    core = np.asarray(m).astype(float) / 255.0
    halo = np.asarray(m.filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.GaussianBlur(0.9))).astype(float) / 255.0
    zone = np.zeros(core.shape, bool)
    zone[y0:y1, x0:x1] = True
    core *= zone
    halo *= zone
    outline = np.array([88.0, 62.0, 58.0])
    white = np.array([255.0, 250.0, 248.0])
    base = arr[..., :3]
    base = base * (1 - halo[..., None] * 0.85) + outline * (halo[..., None] * 0.85)
    base = base * (1 - core[..., None]) + white * core[..., None]
    arr[..., :3] = base
    out = np.clip(arr, 0, 255).astype(np.uint8)
    out[..., 3] = a[..., 3]
    mask = zone
    return Image.fromarray(out, "RGBA"), mask
