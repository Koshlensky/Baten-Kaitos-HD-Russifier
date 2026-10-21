"""Простые «слова» с обводкой для боевого интерфейса BK2 (D-048): белая заливка, чёрная обводка, мягкая тень.

Используется только для спрайтов на прозрачном фоне с простым стилем (слова card/cards/hit/hits/damage и заголовок ACTION).
Размер и положение: текст вписывается в охватывающий прямоугольник старой надписи; альфа вне надписи не трогается.
"""
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

SUPER = 4
SERIF_BI = r"C:\Windows\Fonts\palab.ttf"   # жирная антиква; наклон добавляется сдвигом (кириллический курсив читается хуже)
SLANT = 0.2
SANS_NARROW_B = r"C:\Windows\Fonts\ARIALNB.TTF"


def footprint(img, thr=16):
    """Охватывающий прямоугольник непрозрачных пикселей (x0, y0, x1, y1) включительно."""
    a = np.asarray(img.convert("RGBA"))[..., 3]
    ys, xs = np.nonzero(a > thr)
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())


def _layer(size, text, font_path, px, left, center_y, slant=0.0):
    font = ImageFont.truetype(font_path, int(px * SUPER))
    layer = Image.new("L", (size[0] * SUPER, size[1] * SUPER), 0)
    d = ImageDraw.Draw(layer)
    box = font.getbbox(text)
    d.text((left * SUPER - box[0], center_y * SUPER - (box[1] + box[3]) / 2.0), text, font=font, fill=255)
    if slant:
        cy = center_y * SUPER
        layer = layer.transform(layer.size, Image.AFFINE, (1, slant, -slant * cy, 0, 1, 0), resample=Image.BICUBIC)
    return layer.resize(size, Image.LANCZOS), (box[2] - box[0]) / SUPER + slant * (box[3] - box[1]) / SUPER, (box[3] - box[1]) / SUPER


def stroked(size, text, font_path, bbox, max_w=None, max_h=None, align="left", fill_top=(255, 255, 255),
            fill_bottom=(226, 226, 232), outline=(0, 0, 0), outline_px=2.0, shadow=(1.0, 2.0, 0.55), slant=0.0, height_limit=None):
    """Возвращает RGBA-слой size с текстом, вписанным в bbox (x0, y0, x1, y1)."""
    x0, y0, x1, y1 = bbox
    max_w = max_w or (x1 - x0 + 1)
    max_h = max_h or height_limit or (y1 - y0 + 1)
    px = max_h * 1.3
    while px > 8:
        _, w, h = _layer((8, 8), text, font_path, px, 0, 4, slant)
        if w <= max_w and h <= max_h and True:
            break
        px -= 0.5
    left = (x0 if align == "left" else x0 + (max_w - w) / 2.0) + slant * h / 2.0
    mask, w, h = _layer(size, text, font_path, px, left, (y0 + y1) / 2.0, slant)
    m = np.asarray(mask).astype(float) / 255.0
    k = int(round(outline_px * 2 + 1)) | 1
    out_mask = np.asarray(mask.filter(ImageFilter.MaxFilter(k)).filter(ImageFilter.GaussianBlur(0.5))).astype(float) / 255.0
    sh = np.asarray(Image.fromarray((out_mask * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(1.2))).astype(float) / 255.0
    sh = np.roll(np.roll(sh, int(round(shadow[1])), axis=0), int(round(shadow[0])), axis=1) * shadow[2]
    H, W = m.shape
    ys = np.nonzero(m.max(axis=1) > 0.05)[0]
    top, bottom = (ys.min(), ys.max()) if len(ys) else (0, H - 1)
    t = np.clip((np.arange(H) - top) / max(1, bottom - top), 0, 1)[:, None, None]
    fill = np.array(fill_top)[None, None, :] * (1 - t) + np.array(fill_bottom)[None, None, :] * t
    rgb = np.zeros((H, W, 3))
    alpha = np.zeros((H, W))
    # тень → обводка → заливка (поверх друг друга)
    alpha = sh.copy()
    rgb[:] = np.array(outline)[None, None, :]
    a2 = np.maximum(alpha, out_mask)
    rgb = rgb  # тень и обводка одного цвета
    alpha = a2
    rgb = rgb * (1 - m[..., None]) + fill * m[..., None]
    alpha = np.maximum(alpha, m)
    img = np.dstack([np.clip(rgb, 0, 255), np.clip(alpha * 255, 0, 255)]).astype(np.uint8)
    return Image.fromarray(img, "RGBA"), px
