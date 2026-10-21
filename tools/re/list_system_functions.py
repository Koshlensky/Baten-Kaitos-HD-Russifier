import re
import sys

sys.stdout.reconfigure(encoding="utf-8")
lines = open("work/re/bk1/dump/dump.cs", encoding="utf-8").read().split("\n")
start = next(i for i, l in enumerate(lines) if l.startswith("public class SystemFunctionsBase //"))
end = next(i for i in range(start + 1, len(lines)) if lines[i].startswith("// Namespace:"))
names = []
for i in range(start, end):
    m = re.match(r"\s+(?:protected|public|private) static void (\w+)\(SystemFunctionOption option\)", lines[i])
    if m:
        rva = re.search(r"RVA: (0x[0-9A-F]+) Offset: (0x[0-9A-F]+)", lines[i - 1])
        names.append((m.group(1), rva.group(1) if rva else "", rva.group(2) if rva else ""))
print("system functions in base:", len(names))
interesting = [n for n in names if re.search(r"(?i)mes|msg|talk|win|balloon|face|name|voice|text|print|question|select|chara|char|actor|speak|pos|screen", n[0])]
for n, rva, off in interesting:
    print(f"  {n:40s} {rva} {off}")
print()
print(", ".join(n for n, _, _ in names))
