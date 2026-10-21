"""Этап A: спецификация скриптовой машины из обработчиков ScriptProcess.runOpCodes.

Для каждого опкода дизассемблируется обработчик из таблицы переходов и считается,
на сколько байт он сдвигает счётчик команд ([rsi+0x38]) и задаёт ли его явно (переход).
Имена опкодов — из перечисления ScriptProcess.OPCODE в dump.cs.
"""
import json
import re
import struct
import sys

from capstone import CS_ARCH_X86, CS_MODE_64, Cs

from tools.bkloc.paths import WORK, write_json
from tools.re.script_data import PE

# Адреса для BK1 (из дизассемблирования runOpCodes); для BK2 ищутся по сигнатуре.
LOOP_CONTINUE_HINT = "jmp"


def opcode_names(game):
    text = (WORK / "re" / game / "dump" / "dump.cs").read_text(encoding="utf-8")
    block = text.split("enum ScriptProcess.OPCODE", 1)[1].split("}", 1)[0]
    return {int(v): n for n, v in re.findall(r"OPCODE (OP_\w+) = (\d+);", block)}


def find_dispatch(pe, run_rva, size=8000):
    """Таблица переходов и адрес продолжения цикла в runOpCodes."""
    md = Cs(CS_ARCH_X86, CS_MODE_64)
    code = pe.data[pe.offset(run_rva):pe.offset(run_rva) + size]
    insns = list(md.disasm(code, run_rva))
    table = loop = limit = None
    for i, ins in enumerate(insns):
        if ins.mnemonic == "cmp" and re.match(r"(edx|r8d), 0x", ins.op_str) and i + 3 < len(insns):
            for nxt in insns[i + 1:i + 4]:
                m = re.search(r"\[r12 \+ (rdx|r8)\*4 \+ (0x[0-9a-f]+)\]", nxt.op_str)
                if m:
                    table = int(m.group(2), 16)
                    limit = int(ins.op_str.split(",")[1], 16) + 1
                    break
        if table and ins.mnemonic == "jmp" and loop is None and i > 0:
            pass
    # продолжение цикла — самая частая цель безусловного jmp внутри функции
    targets = {}
    for ins in insns:
        if ins.mnemonic == "jmp" and ins.op_str.startswith("0x"):
            t = int(ins.op_str, 16)
            targets[t] = targets.get(t, 0) + 1
    loop = max(targets, key=targets.get)
    return table, limit, loop, insns


def handler_spec(pe, start, loop, end_limit=600):
    md = Cs(CS_ARCH_X86, CS_MODE_64)
    code = pe.data[pe.offset(start):pe.offset(start) + end_limit]
    advance, sets_pc, loads, calls = 0, False, [], []
    for ins in md.disasm(code, start):
        s = ins.op_str
        if ins.mnemonic == "inc" and s == "dword ptr [rsi + 0x38]":
            advance += 1
        elif ins.mnemonic == "add" and s.startswith("dword ptr [rsi + 0x38], "):
            value = s.split(", ")[1]
            if value.startswith("0x") or value.isdigit():
                advance += int(value, 16)
            else:
                sets_pc = True
        elif ins.mnemonic == "mov" and s.startswith("dword ptr [rsi + 0x38], "):
            sets_pc = True
        elif ins.mnemonic == "lea" and re.match(r"r8d, \[r9 \+ (\d)\]", s):
            loads.append(int(re.match(r"r8d, \[r9 \+ (\d)\]", s).group(1)))
        elif ins.mnemonic == "call":
            calls.append(s)
        if ins.mnemonic == "jmp" and s.startswith("0x") and int(s, 16) == loop:
            break
        if ins.mnemonic in ("ret",):
            break
    return {"advance": advance, "sets_pc": sets_pc, "loads": loads, "calls": calls[:6]}


def build(game):
    base = WORK / "re" / game
    pe = PE(base / "GameAssembly.dll")
    script = json.loads((base / "dump" / "script.json").read_text(encoding="utf-8"))
    run = next(m["Address"] for m in script["ScriptMethod"] if m["Name"] == "LB.PRJ0505.Common.ScriptProcess$$runOpCodes")
    table, limit, loop, _ = find_dispatch(pe, run)
    names = opcode_names(game)
    spec = {}
    for op in range(limit):
        entry = struct.unpack_from("<i", pe.data, pe.offset(table) + op * 4)[0]
        h = handler_spec(pe, entry, loop)
        h["name"] = names.get(op, f"OP_{op}")
        h["handler"] = hex(entry)
        spec[op] = h
    doc = {"game": game, "run_rva": hex(run), "table_rva": hex(table), "opcodes": limit, "loop_rva": hex(loop), "spec": spec}
    write_json(base / "vm_spec.json", doc)
    return doc


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    game = sys.argv[1] if len(sys.argv) > 1 else "bk1"
    doc = build(game)
    print(f"{game}: опкодов {doc['opcodes']}, таблица {doc['table_rva']}, цикл {doc['loop_rva']}")
    for op, h in doc["spec"].items():
        print(f"  {op:3d} {h['name']:10s} +{h['advance']} {'PC' if h['sets_pc'] else '  '} loads={h['loads']}")


if __name__ == "__main__":
    main()
