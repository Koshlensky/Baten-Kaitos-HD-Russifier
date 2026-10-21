"""Дизассемблирование функций GameAssembly.dll (копия) по смещению в файле."""
import sys

from capstone import CS_ARCH_X86, CS_MODE_64, Cs

sys.stdout.reconfigure(encoding="utf-8")
data = open("work/re/bk1/GameAssembly.dll", "rb").read()
md = Cs(CS_ARCH_X86, CS_MODE_64)
targets = [(name, int(off, 16), int(va, 16)) for name, off, va in (a.split(":") for a in sys.argv[1:])]
for name, off, va in targets:
    print(f"== {name} file_off={off:#x} va={va:#x}")
    for n, ins in enumerate(md.disasm(data[off:off + 400], va)):
        print(f"  {ins.address:#x}: {ins.mnemonic} {ins.op_str}")
        if ins.mnemonic in ("ret", "int3") or n > 60:
            break
