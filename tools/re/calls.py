"""Этап A: разбор вызовов в байткоде скриптов и привязка текстов к аргументам.

Байткод — стековая машина: аргументы вызова кладутся парами «загрузка значения → OP_PUSH»,
затем OP_CALL (функция скрипта по адресу кода), OP_CALS/OP_CALSS (системная функция по
номеру), после чего OP_POPS*/OP_POPSB снимают аргументы. Для каждого вызова сохраняется
список аргументов: адрес ресурса (OP_ADR, с маркером текста), целое (OP_IM*), прочее.

Проверка корректности линейного разбора: позиции из обеих таблиц перемещений должны
приходиться на операнды 4-байтовых инструкций.
"""
import json
import sys
from collections import Counter

from tools.bkloc.paths import WORK
from tools.re.vm_disasm import disassemble, load_script, opcode_names, relocation_positions, resources

IMMEDIATE = {1: "IMB", 2: "IMW", 3: "IMD", 5: "IMSB", 6: "IMSW", 8: "IMDA"}
CALLS = {"OP_CALL", "OP_CALS", "OP_CALSS"}


def signed(value, size):
    bits = size * 8
    return value - (1 << bits) if value >= 1 << (bits - 1) else value


def parse_file(game, file_id, files, meta, names):
    f, code, _size, tables = load_script(game, file_id, files, meta)
    res = resources(f, meta)
    reloc = [set(relocation_positions(t)[1]) if t else set() for t in tables]
    linear = disassemble(code, game=game)
    linear_starts = {pc + 1 for pc, op, operand, size in linear if size == 4}
    ins = disassemble(code, anchors={p - 1 for r in reloc for p in r}, game=game)
    operand_starts = {pc + 1 for pc, op, operand, size in ins if size == 4}
    check = {
        "code_relocs": len(reloc[0]), "code_relocs_linear_ok": len(reloc[0] & linear_starts),
        "code_relocs_ok": len(reloc[0] & operand_starts),
        "res_relocs": len(reloc[1]), "res_relocs_linear_ok": len(reloc[1] & linear_starts),
        "res_relocs_ok": len(reloc[1] & operand_starts),
        "invalid_linear": sum(1 for x in linear if x[3] == "INVALID"),
        "invalid": sum(1 for x in ins if x[3] == "INVALID"),
    }
    calls = []
    args = []
    pending = None
    for pc, op, operand, size in ins:
        name = names.get(op, str(op))
        if name == "OP_PUSH":
            if pending is not None:
                args.append(pending)
            pending = None
            continue
        if name in CALLS:
            calls.append({"pc": pc, "op": name, "target": operand, "args": args})
            args, pending = [], None
            continue
        if name == "OP_ADR":
            kind, val = res.get(operand, ("data", b""))
            pending = {"t": "id", "v": val, "pc": pc} if kind == "id" else {"t": "adr", "v": operand, "pc": pc}
        elif op in IMMEDIATE:
            v = signed(operand, size) if IMMEDIATE[op] in ("IMSB", "IMSW") else operand
            pending = {"t": "imm", "v": v, "pc": pc}
        else:
            pending = {"t": "expr", "v": name, "pc": pc}
            if operand is not None:
                pending["x"] = operand
            if name.startswith("OP_POP") or name.startswith("OP_J") or name == "OP_RET":
                args, pending = [], None
    return calls, check


def parse_all(game):
    files = json.loads((WORK / "re" / game / "scripts.json").read_text(encoding="utf-8"))
    meta = open(WORK / "re" / game / "global-metadata.dat", "rb").read()
    names = opcode_names(game)
    result, totals = {}, Counter()
    for file_id, f in files.items():
        if not f.get("ScriptCode"):
            continue
        calls, check = parse_file(game, file_id, files, meta, names)
        result[file_id] = calls
        totals.update(check)
    return result, totals


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    game = sys.argv[1] if len(sys.argv) > 1 else "bk1"
    result, totals = parse_all(game)
    print(game, dict(totals))
    out = WORK / "re" / game / "calls.json"
    out.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    print("calls:", sum(len(c) for c in result.values()), "->", out)


if __name__ == "__main__":
    main()
