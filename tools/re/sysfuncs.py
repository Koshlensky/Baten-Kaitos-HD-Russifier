"""Этап A: таблица системных функций скриптовой машины (номер -> имя).

Статические конструкторы SystemFunctionsBase и SystemFunctions (Batenkaitos1/2) создают
записи FunctionData(index, name): перед вызовом конструктора в edx кладётся номер, в r8 —
строковый литерал с именем. Порядок вызовов сохраняется (таблицы разных режимов идут
подряд; граница режима — сброс номеров).
"""
import json
import sys
from bisect import bisect_right

from capstone import CS_ARCH_X86, CS_MODE_64, Cs
from capstone.x86 import X86_OP_IMM, X86_OP_MEM, X86_OP_REG, X86_REG_EDX, X86_REG_R8, X86_REG_R9, X86_REG_RIP

from tools.bkloc.paths import WORK, write_json
from tools.re.script_data import PE


def extract(game):
    base = WORK / "re" / game
    script = json.loads((base / "dump" / "script.json").read_text(encoding="utf-8"))
    pe = PE(base / "GameAssembly.dll")
    strings = {s["Address"]: s["Value"] for s in script["ScriptString"]}
    methods = sorted((m["Address"], m["Name"]) for m in script["ScriptMethod"])
    starts = [a for a, _ in methods]
    by_name = {n: a for a, n in methods}
    ctor = by_name["LB.PRJ0505.Common.SystemFunctionsBase.FunctionData$$.ctor"]
    namespace = "Batenkaitos1" if game == "bk1" else "Batenkaitos2"
    md = Cs(CS_ARCH_X86, CS_MODE_64)
    md.detail = True
    tables = {}
    for owner in ("LB.PRJ0505.Common.SystemFunctionsBase$$.cctor", f"LB.PRJ0505.{namespace}.SystemFunctions$$.cctor"):
        address = by_name.get(owner)
        if address is None:
            continue
        end = starts[bisect_right(starts, address)]
        code = pe.data[pe.offset(address):pe.offset(address) + (end - address)]
        entries, index, name = [], None, None
        # Номер записи — последнее присваивание edx перед вызовом конструктора
        # ("mov edx, N", "lea edx, [r9 + N]" при обнулённом r9, "xor edx, edx"). Любой другой
        # вызов (в том числе запись предыдущей записи в массив) сбрасывает номер.
        for ins in md.disasm(code, address):
            ops = ins.operands
            if ins.mnemonic == "mov" and len(ops) == 2 and ops[0].type == X86_OP_REG:
                if ops[0].reg == X86_REG_EDX:
                    index = ops[1].imm if ops[1].type == X86_OP_IMM else None
                elif ops[0].reg == X86_REG_R8 and ops[1].type == X86_OP_MEM and ops[1].mem.base == X86_REG_RIP:
                    name = strings.get(ins.address + ins.size + ops[1].mem.disp)
            elif ins.mnemonic == "lea" and len(ops) == 2 and ops[0].type == X86_OP_REG and ops[0].reg == X86_REG_EDX:
                index = ops[1].mem.disp if ops[1].mem.base == X86_REG_R9 and ops[1].mem.index == 0 else None
            elif ins.mnemonic == "xor" and len(ops) == 2 and ops[0].type == X86_OP_REG and ops[0].reg == X86_REG_EDX:
                index = 0
            elif ins.mnemonic == "call" and ops and ops[0].type == X86_OP_IMM and ops[0].imm == ctor:
                entries.append({"index": index, "name": name})
                index, name = None, None
            elif ins.mnemonic == "call":
                index = None
        tables[owner.split("$$")[0]] = entries
    write_json(base / "sysfuncs.json", tables)
    return tables


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    game = sys.argv[1] if len(sys.argv) > 1 else "bk1"
    tables = extract(game)
    for owner, entries in tables.items():
        print(owner, len(entries))
        print("  ", ", ".join(f"{e['index']}:{e['name']}" for e in entries[:400]))


if __name__ == "__main__":
    main()
