"""Этап A: распаковка байткода скриптов и разбор ссылок на ресурсы.

Формат (установлен по ScriptProcess.runOpCodes и ScriptCode.decompressCode):
- opCodeDatas: 16 байт заголовка (байты 4–7 — размер распакованного кода, LE), затем zlib.
- Байткод big-endian; опкод — 1 байт (< 0x8B), операнды фиксированной длины (OPERAND_SIZE).
- codeAddrTable / resourceAddrTable — таблицы перемещений: дельты ushort (LE) к позициям
  4-байтовых адресов в коде (адреса кода и адреса буферов ScriptResource соответственно).
"""
import json
import re
import struct
import sys
import zlib

from tools.bkloc.paths import WORK

OPERAND_SIZE = {
    0: 0, 1: 1, 2: 2, 3: 4, 4: 8, 5: 1, 6: 2, 7: 4, 8: 4, 9: 1,
    10: 4, 11: 4, 12: 4, 13: 4, 14: 4, 15: 4, 16: 4, 17: 4, 18: 4, 19: 4, 20: 4, 21: 4,
    22: 1, 23: 1, 24: 1, 25: 1, 26: 1, 27: 1,
    28: 0, 29: 0, 30: 0, 31: 0, 32: 0, 33: 0, 34: 0, 35: 0, 36: 0, 37: 0, 38: 4, 39: 1,
    **{op: 0 for op in range(40, 76)},
    76: 1, 77: 1, 78: 1, 79: 1, 80: 1, 81: 1, 82: 1, 83: 1, 84: 4,
    85: 1, 86: 1, 87: 1, 88: 1, 89: 1, 90: 1, 91: 1, 92: 4,
    **{op: 0 for op in range(93, 116)},
    116: 4, 117: 4, 118: 4, 119: 1, 120: 1, 121: 1, 122: 4, 123: 4, 124: 0, 125: 4, 126: 1,
    127: 0, 128: 4, 129: 1, 130: 0, 131: 0, 132: 4, 133: 1, 134: 0, 135: 0, 136: 4, 137: 1, 138: 4,
}


def opcode_names(game):
    text = (WORK / "re" / game / "dump" / "dump.cs").read_text(encoding="utf-8")
    block = text.split("enum ScriptProcess.OPCODE", 1)[1].split("}", 1)[0]
    return {int(v): n for n, v in re.findall(r"OPCODE (OP_\w+) = (\d+);", block)}


def blob(meta, array):
    return meta[array["meta_offset"]:array["meta_offset"] + array["size"]]


def resources(f, meta):
    """Адрес буфера GameCube -> ("id", маркер) для текстовых буферов или ("data", байты)."""
    result = {}
    for a in f.get("ScriptResource", []):
        b = blob(meta, a)
        m = re.fullmatch(rb"@@([A-Za-z0-9_\-]+)@@\x00*", b)
        result[a["address"]] = ("id", m.group(1).decode()) if m else ("data", b)
    return result


def decompress(raw):
    size = struct.unpack_from("<I", raw, 4)[0]
    code = zlib.decompress(raw[18:], -15)
    return code, size


def relocation_positions(table_raw):
    """Таблица перемещений: дельты ushort (LE) → абсолютные позиции в коде."""
    values = struct.unpack_from("<%dH" % (len(table_raw) // 2), table_raw)
    positions, pos = [], 0
    for v in values:
        pos += v
        positions.append(pos)
    return values, positions


def canonical_opcode(raw, game):
    """BK2 нумерует опкоды от 0x7E на единицу меньше; ScriptProcess.getOpCodes в BK2
    прибавляет 1 к байту >= 0x7E, приводя его к общей нумерации ScriptProcess.OPCODE."""
    if game == "bk2" and raw >= 0x7E:
        return raw + 1
    return raw


def disassemble(code, start=0, end=None, anchors=None, game="bk1"):
    """Линейный разбор. anchors — заведомые начала команд (позиция операнда из таблиц
    перемещений минус 1): если разбор перескочил якорь, он возвращается к якорю, а
    разобранное после якоря отбрасывается (это были данные или рассинхронизация)."""
    end = len(code) if end is None else end
    owner = [-1] * end
    starts = {}

    def place(pc):
        op = canonical_opcode(code[pc], game)
        if op >= 0x8B:
            item, length = (op, None, "INVALID"), 1
        else:
            size = OPERAND_SIZE[op]
            item = (op, int.from_bytes(code[pc + 1:pc + 1 + size], "big") if size else None, size)
            length = 1 + size
        stop = min(pc + length, end)
        for b in range(pc, stop):
            s = owner[b]
            if s != -1 and s != pc and s in starts:
                old = starts.pop(s)
                old_len = 1 + (old[2] if isinstance(old[2], int) else 0)
                for c in range(s, min(s + old_len, end)):
                    if owner[c] == s:
                        owner[c] = -1
        starts[pc] = item
        for b in range(pc, stop):
            owner[b] = pc
        return pc + length

    pc = start
    while pc < end:
        pc = place(pc)
    for a in sorted(a for a in (anchors or ()) if start <= a < end):
        pc = a
        while pc < end and pc not in starts:
            pc = place(pc)
    return [(pc, *starts[pc]) for pc in sorted(starts)]


def load_script(game, file_id, files=None, meta=None):
    files = files or json.loads((WORK / "re" / game / "scripts.json").read_text(encoding="utf-8"))
    meta = meta or open(WORK / "re" / game / "global-metadata.dat", "rb").read()
    f = files[file_id]
    arrays = {a["field"]: a for a in f["ScriptCode"]}
    code, size = decompress(blob(meta, arrays["opCodeDatas"]))
    tables = [blob(meta, arrays[name]) if name in arrays else b"" for name in ("codeAddrTable", "resourceAddrTable")]
    return f, code, size, tables


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    game = sys.argv[1] if len(sys.argv) > 1 else "bk1"
    file_id = sys.argv[2]
    f, code, size, tables = load_script(game, file_id)
    print(f"{file_id}: код {len(code)} байт (заголовок говорит {size}), таблиц {len(tables)} ({[len(t) for t in tables]})")


if __name__ == "__main__":
    main()
