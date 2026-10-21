"""Этап A: карта «ID текста → файл скрипта» по маркерам @@ID@@ в ресурсах скриптов.

Пишет base/scenes/<game>_script_files.json:
  files      — для каждого файла скрипта: сюжетные ID в порядке буферов, коды SCR (BK1),
               имена NPC, на которые ссылается скрипт, число общих строк;
  by_text    — для сюжетных ID: файлы, в которых они встречаются;
  scr_codes  — BK1: код скрипта из ID (EVE08) → файлы.
"""
import argparse
import collections
import json
import re
import sys

from tools.bkloc.categories import classify
from tools.bkloc.paths import SCENES, SOURCE, WORK, read_jsonl, write_json

NAME_PREFIX = re.compile(r"^(NPC|NPCName|PCN)_")


def build(game):
    files = json.loads((WORK / "re" / game / "scripts.json").read_text(encoding="utf-8"))
    lines = {r["id"]: r for r in read_jsonl(SOURCE / game / "lines.jsonl")}
    story_cats = {"dialogue", "battle_voice", "movie_subtitle", "letter", "story_summary"}
    by_text = collections.defaultdict(list)
    out_files = {}
    for file_id, f in sorted(files.items()):
        story, names, common = [], [], 0
        seen = set()
        for m in f.get("markers", []):
            tid = m["id"]
            if tid in seen:
                continue
            seen.add(tid)
            if tid not in lines:
                continue
            if NAME_PREFIX.match(tid):
                names.append(tid)
            elif classify(tid, lines[tid]["en"], lines[tid].get("jp"))[0] in story_cats:
                story.append(tid)
                by_text[tid].append(file_id)
            else:
                common += 1
        codes = sorted({t.split("_")[1] for t in story if t.startswith("SCR_")})
        out_files[file_id] = {"story_ids": story, "scr_codes": codes, "npc_names": names, "common_ids": common,
                              "code_arrays": [a["size"] for a in f.get("ScriptCode", [])]}
    scr_codes = collections.defaultdict(set)
    for file_id, f in out_files.items():
        for code in f["scr_codes"]:
            scr_codes[code].add(file_id)
    doc = {"game": game,
           "files": out_files,
           "by_text": {k: v for k, v in sorted(by_text.items())},
           "scr_codes": {k: sorted(v) for k, v in sorted(scr_codes.items())}}
    write_json(SCENES / f"{game}_script_files.json", doc, indent=None)
    return doc, lines


def stats(game, doc, lines):
    story_ids = [i for i, r in lines.items()
                 if classify(i, r["en"], r.get("jp"))[0] in {"dialogue", "battle_voice", "movie_subtitle", "letter", "story_summary"}]
    mapped = [i for i in story_ids if i in doc["by_text"]]
    multi = [i for i in mapped if len(doc["by_text"][i]) > 1]
    print(f"{game}: файлов {len(doc['files'])}; сюжетных ID {len(story_ids)}, найдено в скриптах {len(mapped)}, "
          f"в нескольких файлах {len(multi)}")
    if game == "bk1":
        one = sum(1 for v in doc["scr_codes"].values() if len(v) == 1)
        print(f"  кодов SCR: {len(doc['scr_codes'])}, однозначно на один файл: {one}; примеры: "
              + ", ".join(f"{k}→{','.join(v)}" for k, v in list(doc["scr_codes"].items())[:6]))
    else:
        ev_files = {fid: [t for t in f["story_ids"] if re.fullmatch(r"EV_\d+", t)] for fid, f in doc["files"].items()}
        ev_files = {k: v for k, v in ev_files.items() if v}
        print(f"  файлов с EV-репликами: {len(ev_files)}; размеры: "
              + str(sorted(len(v) for v in ev_files.values())[-8:]))
    with_names = sum(1 for f in doc["files"].values() if f["npc_names"])
    print(f"  файлов со ссылками на имена NPC: {with_names}")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--game", default="bk1")
    args = parser.parse_args()
    doc, lines = build(args.game)
    stats(args.game, doc, lines)


if __name__ == "__main__":
    main()
