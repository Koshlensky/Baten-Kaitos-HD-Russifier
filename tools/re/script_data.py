"""Этап A: извлечение данных скриптов GameCube из копий GameAssembly.dll и global-metadata.dat.

Для каждого класса ScriptCode_<fileId> и ScriptResource_<fileId> дизассемблируется
статический конструктор. Шаблон на каждое поле:
    newarr(размер) → [InitializeArray(слот Field$<PrivateImplementationDetails>.<хеш>)] →
    mov qword ptr [статические_поля + смещение], массив
Слот привязывается к смещению поля, смещение — к имени поля из dump.cs. Поле без
InitializeArray (пустой массив) получает пустые данные. Данные массивов читаются из
метаданных по смещениям, которые выдал Il2CppDumper.

Результат: work/re/<game>/scripts.json — для каждого файла скрипта массивы ScriptCode
(opCodeDatas, codeAddrTable, resourceAddrTable) и буферы ScriptResource (адрес GameCube
из имени поля, размер, смещение данных), а также маркеры @@ID@@ с адресами буферов.
"""
import argparse
import json
import re
import struct
import sys
from bisect import bisect_right

from capstone import CS_ARCH_X86, CS_MODE_64, Cs
from capstone.x86 import X86_OP_MEM, X86_OP_REG, X86_REG_RBP, X86_REG_RIP, X86_REG_RSP

from tools.bkloc.paths import WORK, write_json


class PE:
    def __init__(self, path):
        self.data = open(path, "rb").read()
        lfanew = struct.unpack_from("<I", self.data, 0x3C)[0]
        n_sections = struct.unpack_from("<H", self.data, lfanew + 6)[0]
        opt_size = struct.unpack_from("<H", self.data, lfanew + 20)[0]
        opt = lfanew + 24
        self.image_base = struct.unpack_from("<Q", self.data, opt + 24)[0]
        self.sections = []
        table = opt + opt_size
        for i in range(n_sections):
            name, vsize, va, raw_size, raw_ptr = struct.unpack_from("<8sIIII", self.data, table + i * 40)
            self.sections.append((name.rstrip(b"\0").decode(), va, vsize, raw_ptr, raw_size))

    def offset(self, rva):
        for _name, va, vsize, raw_ptr, raw_size in self.sections:
            if va <= rva < va + max(vsize, raw_size):
                return rva - va + raw_ptr
        raise ValueError(hex(rva))


def private_fields(dump_text):
    """Хеш поля <PrivateImplementationDetails> -> (размер, смещение данных в метаданных)."""
    pattern = re.compile(r"__StaticArrayInitTypeSize=(\d+) ([0-9A-F]{64}) /\*Metadata offset 0x([0-9A-F]+)\*/")
    return {m.group(2): (int(m.group(1)), int(m.group(3), 16)) for m in pattern.finditer(dump_text)}


def class_fields(dump_text, namespace):
    """Класс -> {смещение статического поля: имя поля} для ScriptCode_* и ScriptResource_*."""
    result = {}
    pattern = re.compile(r"// Namespace: " + re.escape(namespace) +
                         r"\npublic class (Script(?:Code|Resource)_[0-9A-F]{8}) : [^\n]*\n\{(.*?)\n\}", re.S)
    for m in pattern.finditer(dump_text):
        fields = {}
        for fm in re.finditer(r"static readonly [\w\[\]]+ (\w+); // 0x([0-9A-F]+)", m.group(2)):
            fields[int(fm.group(2), 16)] = fm.group(1)
        result[m.group(1)] = fields
    return result


def cctor_field_slots(md, code, address, slots, field_offsets):
    """Порядок (смещение поля, слот или None) по коду статического конструктора.

    Указатель на статические поля компилятор получает по-разному (загрузка [reg+0xB8]
    или вызов вспомогательной функции), поэтому записью в поле считается любая 64-битная
    запись регистра в память по базовому регистру (не стек и не RIP) со смещением,
    объявленным у статических полей класса."""
    pairs = []
    pending = None
    for ins in md.disasm(code, address):
        ops = ins.operands
        if ins.mnemonic == "mov" and len(ops) == 2:
            dst, src = ops
            if dst.type == X86_OP_REG and src.type == X86_OP_MEM and src.mem.base == X86_REG_RIP:
                target = ins.address + ins.size + src.mem.disp
                if target in slots:
                    pending = slots[target]
            elif (dst.type == X86_OP_MEM and src.type == X86_OP_REG and dst.size == 8
                  and dst.mem.base not in (X86_REG_RIP, X86_REG_RSP, X86_REG_RBP, 0)
                  and dst.mem.index == 0 and dst.mem.disp in field_offsets):
                pairs.append((dst.mem.disp, pending))
                pending = None
        if ins.mnemonic in ("ret", "int3"):
            break
    return pairs


def extract(game):
    base = WORK / "re" / game
    script = json.loads((base / "dump" / "script.json").read_text(encoding="utf-8"))
    dump_text = (base / "dump" / "dump.cs").read_text(encoding="utf-8")
    pe = PE(base / "GameAssembly.dll")
    meta = open(base / "global-metadata.dat", "rb").read()
    fields = private_fields(dump_text)
    namespace = "LB.PRJ0505.Batenkaitos1" if game == "bk1" else "LB.PRJ0505.Batenkaitos2"
    layout = class_fields(dump_text, namespace)
    slots = {}
    for m in script["ScriptMetadata"]:
        name = m.get("Name") or ""
        if name.startswith("Field$<PrivateImplementationDetails>."):
            slots[m["Address"]] = name.split(".", 1)[1]
    methods = sorted((m["Address"], m["Name"]) for m in script["ScriptMethod"])
    starts = [a for a, _ in methods]
    md = Cs(CS_ARCH_X86, CS_MODE_64)
    md.detail = True
    pattern = re.compile(r"^" + re.escape(namespace) + r"\.(ScriptCode|ScriptResource)_([0-9A-F]{8})\$\$\.cctor$")
    files = {}
    unmatched = 0
    for address, name in methods:
        m = pattern.match(name)
        if not m:
            continue
        kind, file_id = m.group(1), m.group(2)
        i = bisect_right(starts, address)
        end = starts[i] if i < len(starts) else address + 0x10000
        code = pe.data[pe.offset(address):pe.offset(address) + (end - address)]
        offsets = layout.get(f"{kind}_{file_id}", {})
        arrays = []
        for field_offset, slot in cctor_field_slots(md, code, address, slots, set(offsets)):
            field = offsets.get(field_offset)
            if field is None:
                unmatched += 1
                continue
            size, off = fields.get(slot, (0, 0)) if slot else (0, 0)
            item = {"field": field, "size": size, "meta_offset": off, "hash": slot}
            if field.startswith("buffer_0x"):
                item["address"] = int(field[len("buffer_0x"):], 16)
            arrays.append(item)
        entry = files.setdefault(file_id, {"file_id": file_id})
        entry[kind] = arrays
        if kind == "ScriptResource":
            markers = []
            for a in arrays:
                blob = meta[a["meta_offset"]:a["meta_offset"] + a["size"]]
                found = re.findall(rb"@@([A-Za-z0-9_\-]+)@@", blob)
                for mk in found:
                    markers.append({"address": a.get("address"), "id": mk.decode("ascii", "replace")})
            entry["markers"] = markers
            entry["fields_expected"] = sum(1 for f in offsets.values() if f.startswith("buffer_0x"))
    write_json(base / "scripts.json", files, indent=None)
    return files, unmatched


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--game", default="bk1")
    args = parser.parse_args()
    files, unmatched = extract(args.game)
    with_code = sum(1 for f in files.values() if f.get("ScriptCode"))
    with_res = sum(1 for f in files.values() if f.get("ScriptResource"))
    markers = sum(len(f.get("markers", [])) for f in files.values())
    mismatched = sum(1 for f in files.values() if f.get("ScriptResource") is not None
                     and len(f["ScriptResource"]) != f.get("fields_expected"))
    empty = sum(1 for f in files.values() for a in f.get("ScriptResource", []) if not a["hash"])
    print(f"{args.game}: файлов {len(files)}, с кодом {with_code}, с ресурсами {with_res}, маркеров {markers}, "
          f"несовпадений числа полей {mismatched}, полей без данных {empty}, неопознанных записей {unmatched}")


if __name__ == "__main__":
    main()
