import collections
import json
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")
rows = {json.loads(l)["id"]: json.loads(l) for l in open("source/bk1/lines.jsonl", encoding="utf-8")}
pairs = collections.defaultdict(dict)
for i, r in rows.items():
    m = re.match(r"^(SCR_.+)_(\d)$", i)
    if m and len(i.split("_")) > 4:
        pairs[m.group(1)][m.group(2)] = r
n = 0
for k, v in pairs.items():
    if "0" in v and "1" in v:
        print(k, "| _0:", v["0"]["jp"][:70].replace("\n", " "), "| _1:", v["1"]["jp"][:70].replace("\n", " "))
        n += 1
        if n >= 12:
            break
choice_share = sum(1 for v in pairs.values() for s in v.values() if "<a1>" in s["en"]) / sum(len(v) for v in pairs.values())
print("share of variant lines that are choice menus:", round(choice_share, 2))
for s in ("0", "1", "2"):
    print("suffix", s, sum(1 for v in pairs.values() if s in v))
print(rows["SYS_0227"])
for game in ("bk2",):
    r2 = [json.loads(l) for l in open(f"source/{game}/lines.jsonl", encoding="utf-8")]
    print(game, [(r["id"], r["en"][:60]) for r in r2 if re.search(r"(?i)gender|\bmale\b|female", r["en"])][:10])
