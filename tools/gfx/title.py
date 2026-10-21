"""Русские надписи для полос титульного меню BK1 (D-045).

Полоса: декоративная рамка и фон сохраняются; старый текст стирается заливкой фона по строкам (интерполяция между
чистыми колонками слева и справа от текста), новый рисуется шрифтом Garamond Bold золотым градиентом с мягкой тенью.
Размер и прозрачность полосы не меняются, правится только центральная зона.
"""
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

FONT = r"C:\Windows\Fonts\GARABD.TTF"
CENTER_X = 320
LEFT_REF, RIGHT_REF = 128, 512        # чистые колонки фона
ZONE = (140, 500)                     # где может быть текст
SUPER = 4


def gold_mask(arr):
    r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
    return (r > 170) & (g > 120) & (b < 150) & (r - b > 70)


def lerp_background(a):
    """Грубая оценка фона: интерполяция по строкам между чистыми колонками слева и справа."""
    w = a.shape[1]
    left = a[:, LEFT_REF - 4:LEFT_REF + 4].mean(axis=1)[:, None, :]
    right = a[:, RIGHT_REF - 4:RIGHT_REF + 4].mean(axis=1)[:, None, :]
    t = np.clip((np.arange(w)[None, :, None] - LEFT_REF) / (RIGHT_REF - LEFT_REF), 0, 1)
    return left * (1 - t) + right * t


def background_template(strips):
    """Чистый фон: в каждой точке берётся полоса, где здесь меньше всего похоже на текст (отличие от грубого фона)."""
    arrs = np.stack([np.asarray(s.convert("RGBA")).astype(np.float64) for s in strips])
    lerp = lerp_background(arrs[0])
    diffs = np.stack([np.abs(x[..., :3] - lerp[..., :3]).max(axis=2) for x in arrs])
    # сглаживаем сравнение, чтобы выбирать полосу целыми пятнами, а не пикселями
    best = diffs.argmin(axis=0)
    out = np.take_along_axis(arrs, best[None, :, :, None].repeat(4, axis=3), axis=0)[0]
    out = out.astype(np.uint8)
    left_over = gold_mask(out.astype(np.float64))
    left_over = cv2.dilate(left_over.astype(np.uint8), np.ones((5, 5), np.uint8))
    if left_over.any():
        out[..., :3] = cv2.inpaint(np.ascontiguousarray(out[..., :3]), left_over, 4, cv2.INPAINT_TELEA)
    return out


def erase_text(img, template):
    """Возвращает (полоса с фоном вместо текста, bbox старого золотого текста)."""
    a = np.asarray(img.convert("RGBA")).copy()
    gm = gold_mask(a.astype(np.float64)) & (np.arange(a.shape[1])[None, :] >= ZONE[0]) & (np.arange(a.shape[1])[None, :] < ZONE[1])
    ys, xs = np.nonzero(gm)
    bbox = (xs.min(), ys.min(), xs.max(), ys.max()) if len(xs) else None
    ramp = np.zeros(a.shape[1])
    ramp[ZONE[0]:ZONE[1]] = 1
    ramp = np.asarray(Image.fromarray((ramp[None, :] * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(3)))[0] / 255.0
    ramp = np.minimum(ramp, 1.0)
    # плавный переход на краях зоны, внутри — чистый шаблон
    inner = np.zeros(a.shape[1]); inner[ZONE[0] + 6:ZONE[1] - 6] = 1
    w = np.maximum(inner, ramp * 0)  # полная замена внутри
    edge = np.linspace(0, 1, 7)
    w[ZONE[0]:ZONE[0] + 6] = edge[:6]
    w[ZONE[1] - 6:ZONE[1]] = edge[:6][::-1]
    out = a.astype(np.float64) * (1 - w[None, :, None]) + template.astype(np.float64) * w[None, :, None]
    return Image.fromarray(out.astype(np.uint8), "RGBA"), bbox

def gradient_from_original(img, bbox):
    """Средний цвет золота по строкам оригинала → список RGB от верха букв к низу."""
    a = np.asarray(img.convert("RGBA")).astype(float)
    gm = gold_mask(a)
    x0, y0, x1, y1 = bbox
    rows = []
    for y in range(y0, y1 + 1):
        m = gm[y]
        rows.append(a[y][m][:, :3].mean(axis=0) if m.any() else (rows[-1] if rows else np.array([230, 190, 80.0])))
    return np.array(rows)


def render(text, orig_bbox, grad, cap_height, max_width, size_px=None):
    """Возвращает RGBA-слои (текст, тень) размера 640×48 и фактический кегль."""
    W, H = 640, 48
    size = size_px or int(round(cap_height / 0.66))
    while True:
        font = ImageFont.truetype(FONT, size * SUPER)
        box = font.getbbox(text)
        width = (box[2] - box[0]) / SUPER
        if width <= max_width or size <= 14:
            break
        size -= 1
    layer = Image.new("L", (W * SUPER, H * SUPER), 0)
    d = ImageDraw.Draw(layer)
    ascent, _ = font.getmetrics()
    baseline = (orig_bbox[3] + 1) * SUPER
    x = CENTER_X * SUPER - (box[0] + box[2]) / 2
    d.text((x, baseline - ascent), text, font=font, fill=255)
    mask = layer.resize((W, H), Image.LANCZOS)
    arr = np.asarray(mask).astype(float) / 255.0
    ys = np.nonzero(arr.max(axis=1) > 0.1)[0]
    top, bottom = ys.min(), ys.max()
    rows = np.linspace(0, len(grad) - 1, bottom - top + 1)
    col = np.zeros((H, W, 3))
    for y in range(H):
        k = min(max(y - top, 0), bottom - top)
        col[y] = np.array([np.interp(rows[k], np.arange(len(grad)), grad[:, c]) for c in range(3)])
    text_rgba = np.dstack([col, arr * 255])
    sh = Image.fromarray((arr * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(1.0))
    sh = np.asarray(sh).astype(float)
    sh = np.roll(np.roll(sh, 2, axis=0), 1, axis=1) * 0.75
    return text_rgba, sh, size


def compose(clean, text_rgba, shadow):
    a = np.asarray(clean).astype(float)
    # тень: затемнение фона
    a[..., :3] *= (1 - (shadow / 255.0)[..., None] * 0.7)
    ta = text_rgba[..., 3:4] / 255.0
    a[..., :3] = a[..., :3] * (1 - ta) + text_rgba[..., :3] * ta
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8), "RGBA")


def make_strip(orig_img, template, text, ref_bbox, grad, cap_height, max_width=340):
    clean, _ = erase_text(orig_img, template)
    tr, sh, size = render(text, ref_bbox, grad, cap_height, max_width)
    return compose(clean, tr, sh), size
