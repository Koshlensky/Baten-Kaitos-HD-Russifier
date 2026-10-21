"""Неоновые надписи экрана поражения BK1 (TRY AGAIN? / YES / NO / GAME OVER), D-045.

Оригинал: непрозрачный чёрный фон (alpha 253–255), белое ядро букв, цветной ореол (сине-фиолетовый → розовый).
Новая надпись: тот же принцип — белое ядро шрифта Trebuchet Bold, ореол двух размытий, цвет по словам.
Размер спрайта и альфа-канал оригинала сохраняются.
"""
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

FONT = r"C:\Windows\Fonts\trebucbd.ttf"
SUPER = 4
BLUE = np.array([120, 100, 255.0])
PINK = np.array([245, 100, 215.0])


def render(size, words, original, cap_height, max_width, colors, tracking=2):
    """words: список слов; colors: цвет ореола для каждого слова. original: RGBA оригинал (для alpha и позиции)."""
    W, H = size
    fsize = int(round(cap_height / 0.72))
    while True:
        font = ImageFont.truetype(FONT, fsize * SUPER)
        space = font.getlength(" ") / SUPER
        widths = [font.getlength(w) / SUPER + tracking * len(w) for w in words]
        total = sum(widths) + space * (len(words) - 1)
        if total <= max_width or fsize <= 12:
            break
        fsize -= 1
    ascent, descent = font.getmetrics()
    ys = np.nonzero((np.asarray(original)[..., :3].min(axis=2) > 200).any(axis=1))[0]
    cy = (ys.min() + ys.max()) / 2.0
    x = (W - total) / 2.0
    layers = []
    for word, width, color in zip(words, widths, colors):
        layer = Image.new("L", (W * SUPER, H * SUPER), 0)
        d = ImageDraw.Draw(layer)
        cx = x
        for ch in word:
            d.text((cx * SUPER, cy * SUPER - (ascent - descent * 0.0) * 0.5 - fsize * SUPER * 0.04), ch, font=font, fill=255,
                   stroke_width=int(SUPER * 0.9), anchor="lm" if False else "la")
            cx += font.getlength(ch) / SUPER + tracking
        layers.append((layer.resize((W, H), Image.LANCZOS), color))
        x += width + space
    out = np.zeros((H, W, 3))
    core_total = np.zeros((H, W))
    for mask, color in layers:
        m = np.asarray(mask).astype(float) / 255.0
        g1 = np.asarray(mask.filter(ImageFilter.GaussianBlur(2.2))).astype(float) / 255.0
        g2 = np.asarray(mask.filter(ImageFilter.GaussianBlur(6.5))).astype(float) / 255.0
        glow = np.clip(g1 * 1.15 + g2 * 1.0, 0, 1.4)
        out += glow[..., None] * color[None, None, :]
        core_total = np.maximum(core_total, m)
    core = np.clip(core_total, 0, 1)
    white = np.array([255.0, 252.0, 255.0])
    out = out * (1 - core[..., None]) + white[None, None, :] * core[..., None]
    out = np.clip(out, 0, 255).astype(np.uint8)
    alpha = np.asarray(original)[..., 3:4]
    return Image.fromarray(np.concatenate([out, alpha], axis=2), "RGBA"), fsize
