"""Закрепление метки времени `head.modified` в шрифте (бинарная правка sfnt) для побайтно воспроизводимой сборки.

fontTools при сохранении ставит в `head.modified` текущее время; патч шрифтов теперь сохраняет исходную метку
(`recalcTimestamp=False`), а выпуск v2.0 был собран со временем сборки. Чтобы воспроизвести выпуск точно, метка
записывается в `compat/buildid_*.json` (`head_modified`) и накладывается на результат: правятся 8 байт поля,
пересчитываются контрольные суммы таблицы `head` и всего файла (так же, как делает fontTools).
"""
import struct

HEAD_MODIFIED_OFFSET = 28          # в таблице head: created (20..27), modified (28..35)
HEAD_ADJUST_OFFSET = 8
MAGIC = 0xB1B0AFBA


def _checksum(data):
    data = data + b"\0" * (-len(data) % 4)
    return sum(struct.unpack(">%dL" % (len(data) // 4), data)) & 0xFFFFFFFF


def _tables(raw):
    num = struct.unpack_from(">H", raw, 4)[0]
    out = []
    for i in range(num):
        tag, chk, off, length = struct.unpack_from(">4sLLL", raw, 12 + 16 * i)
        out.append((i, tag, chk, off, length))
    return out


def read_head_modified(raw):
    for _i, tag, _chk, off, _len in _tables(raw):
        if tag == b"head":
            return struct.unpack_from(">q", raw, off + HEAD_MODIFIED_OFFSET)[0]
    raise ValueError("в шрифте нет таблицы head")


def pin_head_modified(raw, modified):
    buf = bytearray(raw)
    entry = None
    for i, tag, _chk, off, length in _tables(raw):
        if tag == b"head":
            entry = (i, off, length)
    if entry is None:
        raise ValueError("в шрифте нет таблицы head")
    i, off, length = entry
    struct.pack_into(">q", buf, off + HEAD_MODIFIED_OFFSET, int(modified))
    struct.pack_into(">L", buf, off + HEAD_ADJUST_OFFSET, 0)
    head_sum = _checksum(bytes(buf[off:off + length]))
    struct.pack_into(">L", buf, 12 + 16 * i + 4, head_sum)
    total = _checksum(bytes(buf))
    struct.pack_into(">L", buf, off + HEAD_ADJUST_OFFSET, (MAGIC - total) & 0xFFFFFFFF)
    return bytes(buf)
