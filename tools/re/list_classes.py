import re
import sys

sys.stdout.reconfigure(encoding="utf-8")
path = "work/re/bk1/dump/dump.cs"
text = open(path, encoding="utf-8").read()
blocks = re.split(r"\n(?=// Namespace: )", text)
want = re.compile(r"(Mes|Msg|Message|Talk|Balloon|Serif|Speak|Window|Text|ScriptManager|ScriptExec|ScriptVM|Interpreter|OpCode|ScriptResourcePack|ScriptFunc|Fukidashi|Face)", re.I)
skip = re.compile(r"ScriptCode_[0-9A-F]{8}|ScriptResource_[0-9A-F]{8}|Mesh|TextMesh|TMP_|Texture|Context")
for block in blocks:
    m = re.match(r"// Namespace: (\S*)\n(?:\[.*\]\n)*(.*?(?:class|struct|enum|interface) ([\w.<>`]+))", block)
    if not m:
        continue
    ns, decl, name = m.group(1), m.group(2), m.group(3)
    if not ns.startswith("LB.PRJ0505") and not (ns == "" and re.match(r"(Script|Mes|Talk|Text)", name)):
        continue
    if not want.search(name) or skip.search(name):
        continue
    methods = re.findall(r"\n\t(?:public|private|protected|internal)[^\n]*?\b(\w+)\(([^)]*)\) \{ \}", block)
    fields = re.findall(r"\n\t(?:public|private|protected|internal)[^\n(]*? (\w+); // 0x", block)
    print(f"== {ns}.{name}  [{decl.strip()[:80]}]")
    print("   fields:", ", ".join(fields[:25]))
    print("   methods:", ", ".join(f"{n}({a[:40]})" for n, a in methods[:40]))
