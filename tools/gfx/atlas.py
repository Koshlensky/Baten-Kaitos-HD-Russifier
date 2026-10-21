"""Правка пикселей спрайтов в BC7-атласах Unity (D-045), только на копиях.

Атлас читается без потерь для нетронутых блоков: перекодируются лишь блоки 4×4, пересекающиеся с изменённой областью.
Блоки кодируются режимом 6 BC7 (один раздел, RGBA 7+1 бит, 4-битные индексы). Остальные байты resS остаются прежними.
Строки текстуры в памяти идут снизу вверх; textureRect.y у Unity отсчитывается снизу.
"""
import math

import numpy as np
import texture2ddecoder
import UnityPy
from PIL import Image
from UnityPy.streams import EndianBinaryReader

from tools.bkloc.paths import bundle_path

WEIGHTS = np.array([0, 4, 9, 13, 17, 21, 26, 30, 34, 38, 43, 47, 51, 55, 60, 64], dtype=np.int32)


def decode_bc7(data, w, h):
    """BC7 → RGBA uint8 [h][w][4], строки в порядке памяти (первая = нижняя)."""
    bgra = np.frombuffer(texture2ddecoder.decode_bc7(bytes(data), w, h), dtype=np.uint8).reshape(h, w, 4)
    return bgra[:, :, [2, 1, 0, 3]].copy()


def _pack_mode6(e0, e1, p0, p1, idx):
    """e0,e1: 4 значения 0..127; p0,p1: p-биты; idx: 16 индексов 0..15 (индекс 0 — якорь, старший бит 0)."""
    bits = 0
    pos = 0

    def put(value, n):
        nonlocal bits, pos
        bits |= (int(value) & ((1 << n) - 1)) << pos
        pos += n

    put(0x40, 7)
    for c in range(4):
        put(e0[c], 7)
        put(e1[c], 7)
    put(p0, 1)
    put(p1, 1)
    put(idx[0], 3)
    for i in range(1, 16):
        put(idx[i], 4)
    return bits.to_bytes(16, "little")


def encode_block_mode6(block):
    """block: [16][4] uint8 → 16 байт BC7 (режим 6)."""
    px = block.astype(np.float64)
    if (px == px[0]).all():
        # однородный блок: точные эндпоинты
        v = px[0]
        e = np.round(v).astype(int)
        q = e >> 1
        p = int(e.sum() * 0 + (e & 1).sum() >= 2)
        return _pack_mode6(q, q, p, p, [0] * 16)
    mean = px.mean(axis=0)
    centered = px - mean
    cov = centered.T @ centered
    w, v = np.linalg.eigh(cov)
    axis = v[:, -1]
    proj = centered @ axis
    lo = mean + axis * proj.min()
    hi = mean + axis * proj.max()
    best = None
    for p0 in (0, 1):
        for p1 in (0, 1):
            cur_lo, cur_hi = lo, hi
            for _round in range(4):
                q0 = np.clip(np.round((cur_lo - p0) / 2), 0, 127).astype(int)
                q1 = np.clip(np.round((cur_hi - p1) / 2), 0, 127).astype(int)
                a = q0 * 2 + p0
                b = q1 * 2 + p1
                pal = ((64 - WEIGHTS)[:, None] * a[None, :] + WEIGHTS[:, None] * b[None, :] + 32) >> 6
                d = ((px[:, None, :] - pal[None, :, :]) ** 2).sum(axis=2)
                idx = d.argmin(axis=1)
                err = d[np.arange(16), idx].sum()
                if best is None or err < best[0]:
                    best = (err, q0, q1, p0, p1, idx)
                t = WEIGHTS[idx] / 64.0
                m = np.array([[((1 - t) ** 2).sum(), ((1 - t) * t).sum()], [((1 - t) * t).sum(), (t ** 2).sum()]])
                if abs(np.linalg.det(m)) < 1e-6:
                    break
                rhs = np.stack([((1 - t)[:, None] * px).sum(axis=0), (t[:, None] * px).sum(axis=0)])
                sol = np.linalg.solve(m, rhs)
                cur_lo, cur_hi = sol[0], sol[1]
    _, q0, q1, p0, p1, idx = best
    if idx[0] >= 8:
        q0, q1 = q1, q0
        p0, p1 = p1, p0
        idx = 15 - idx
    return _pack_mode6(q0, q1, p0, p1, idx)


class Atlas:
    def __init__(self, game, bundle_prefix, source=None):
        self.game = game
        self.tol = 0.1
        self.source = source or next(bundle_path(game, "x").parent.glob(bundle_prefix + "*.bundle"))
        self.env = UnityPy.load(str(self.source))
        self.sprites = {}
        atlases = [o.read() for o in self.env.objects if o.type.name == "SpriteAtlas"]
        rdm = {}
        for a in atlases:
            for key, value in a.m_RenderDataMap:
                rdm[(repr(key[0]), key[1])] = value
        self.textures = {o.path_id: o for o in self.env.objects if o.type.name == "Texture2D"}
        self.tex_cache = {}
        self.dirty = {}
        for o in self.env.objects:
            if o.type.name != "Sprite":
                continue
            d = o.read()
            key = (repr(d.m_RenderDataKey[0]), d.m_RenderDataKey[1])
            self.sprites[d.m_Name] = {"obj": o, "data": rdm.get(key) or d.m_RD, "sprite": d}

    # ---- текстуры
    def texture(self, path_id):
        if path_id not in self.tex_cache:
            t = self.textures[path_id].read()
            res = self.env.file.files[t.m_StreamData.path.split("/")[-1]]
            raw = bytes(res.bytes)[t.m_StreamData.offset: t.m_StreamData.offset + t.m_StreamData.size]
            self.tex_cache[path_id] = {"w": t.m_Width, "h": t.m_Height, "raw": bytearray(raw), "tex": t,
                                       "res": t.m_StreamData.path.split("/")[-1], "off": t.m_StreamData.offset,
                                       "pix": decode_bc7(raw, t.m_Width, t.m_Height)}
            if t.m_TextureFormat != 25 or t.m_MipCount != 1:
                raise SystemExit("ожидался BC7 без мип-уровней")
        return self.tex_cache[path_id]

    def region(self, name, tol=None):
        tol = self.tol if tol is None else tol
        """(path_id текстуры, x, y_память, w, h); только целочисленные неповёрнутые прямоугольники."""
        s = self.sprites[name]
        d = s["data"]
        r = d.textureRect
        if (d.settingsRaw >> 2) & 15:
            raise ValueError(f"{name}: спрайт повёрнут в атласе")
        # охватывающий целочисленный прямоугольник (у плотных спрайтов границы дробные); пересечений между спрайтами нет — проверено отдельно
        x0 = int(math.floor(r.x + tol))
        y0 = int(math.floor(r.y + tol))
        x1 = int(math.ceil(r.x + r.width - tol))
        y1 = int(math.ceil(r.y + r.height - tol))
        return d.texture.path_id, x0, y0, x1 - x0, y1 - y0

    def read(self, name):
        """Содержимое спрайта как RGBA-изображение (сверху вниз)."""
        pid, x, y, w, h = self.region(name)
        pix = self.texture(pid)["pix"]
        return Image.fromarray(pix[y:y + h, x:x + w][::-1].copy(), "RGBA")

    def write(self, name, img, mask=None):
        """Записывает RGBA-изображение того же размера; mask (L/bool, сверху вниз) — где писать (по умолчанию везде)."""
        pid, x, y, w, h = self.region(name)
        if img.size != (w, h):
            raise ValueError(f"{name}: размер {img.size} ≠ {(w, h)}")
        tex = self.texture(pid)
        new = np.asarray(img.convert("RGBA"))[::-1]
        region = tex["pix"][y:y + h, x:x + w]
        if mask is None:
            region[:] = new
            self.dirty.setdefault(pid, []).append((x, y, w, h))
        else:
            m = np.asarray(mask)[::-1].astype(bool)
            region[m] = new[m]
            rows = np.nonzero(m.any(axis=1))[0]
            cols = np.nonzero(m.any(axis=0))[0]
            if len(rows):
                self.dirty.setdefault(pid, []).append((x + int(cols.min()), y + int(rows.min()),
                                                       int(cols.max() - cols.min() + 1), int(rows.max() - rows.min() + 1)))

    # ---- сохранение
    def save(self, out):
        stats = {}
        for pid, rects in self.dirty.items():
            tex = self.texture(pid)
            bw = tex["w"] // 4
            blocks = set()
            for x, y, w, h in rects:
                for by in range(y // 4, (y + h - 1) // 4 + 1):
                    for bx in range(x // 4, (x + w - 1) // 4 + 1):
                        blocks.add((bx, by))
            for bx, by in blocks:
                blk = tex["pix"][by * 4:by * 4 + 4, bx * 4:bx * 4 + 4].reshape(16, 4)
                off = (by * bw + bx) * 16
                tex["raw"][off:off + 16] = encode_block_mode6(blk)
            stats[pid] = len(blocks)
            res = self.env.file.files[tex["res"]]
            data = bytearray(res.bytes)
            data[tex["off"]: tex["off"] + len(tex["raw"])] = tex["raw"]
            reader = EndianBinaryReader(bytes(data))
            reader.flags = res.flags
            self.env.file.files[tex["res"]] = reader
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(self.env.file.save(packer="original"))
        return stats
