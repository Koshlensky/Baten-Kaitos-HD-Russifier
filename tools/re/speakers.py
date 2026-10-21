"""Этап A: говорящие по табличкам имени из байткода скриптов.

Установлено на BK1 (EVE08, файл E00050FB): реплика выводится вызовом локальной функции,
которая получает (окно, ..., ID озвучки, РЕПЛИКА, ИМЯ, ..., актёр) и в итоге вызывает
системные _SetNameTag(окно, ИМЯ) и _Print(окно, РЕПЛИКА). Имя — текстовый ресурс
(PCN_*, NPC_*, MGN_*, иногда SCR_*), который игра показывает над окном; 0 на месте
имени — окно без таблички.

Каждый файл скрипта содержит свои копии библиотечных функций, поэтому функция
опознаётся по отпечатку тела (байткод до первого OP_RET с замаскированными адресами).
Сигнатура реплики (отпечаток, число аргументов, позиция реплики) подтверждается, если
хотя бы в одном вызове за сюжетным текстом сразу идёт ресурс-имя. Для всех вызовов
подтверждённых сигнатур: ресурс-имя → name_tag, 0 → no_tag (табличка не показывается),
вычисляемое значение → dynamic. Прямые _SetNameTag + _Print в одном блоке тоже учитываются.

Табличка имени — то, что игра показывает игроку как говорящего; это факт уровня
script_name_tag. Он не определяет пол и не заменяет проверку контекста: обобщённые
имена (Male, Woman, Soldier) и групповые реплики требуют отдельного решения.
"""
import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict

from tools.bkloc.categories import classify
from tools.bkloc.paths import ROOT, WORK, read_jsonl, write_jsonl
from tools.re.vm_disasm import OPERAND_SIZE, canonical_opcode, load_script, relocation_positions

STORY = {"dialogue", "battle_voice", "movie_subtitle", "letter", "story_summary"}
NAME_PREFIX = re.compile(r"^(PCN|NPC|NPCName|MGN)_")
OP_RET = 0x23


def load_lines(game):
    en, cat = {}, {}
    for r in read_jsonl(ROOT / "source" / game / "lines.jsonl"):
        en[r["id"]] = r.get("en") or ""
        cat[r["id"]] = classify(r["id"], en[r["id"]], r.get("jp"))[0]
    return en, cat


def name_like(text_id, en):
    text = en.get(text_id)
    if text is None:
        return False
    t = text.strip()
    if not t or "<" in t or "\n" in t or len(t) > 32 or t[-1] in ".!?…,":
        return False
    return bool(NAME_PREFIX.match(text_id)) or len(t.split()) <= 4


def sysfunc_index(game, name):
    tables = json.loads((WORK / "re" / game / "sysfuncs.json").read_text(encoding="utf-8"))
    table = tables[f"LB.PRJ0505.Batenkaitos{1 if game == 'bk1' else 2}.SystemFunctions"]
    return next(e["index"] for e in table if e["name"] == name)


class Fingerprints:
    """Отпечаток функции скрипта: байткод от входа до первого OP_RET, адреса замаскированы."""

    def __init__(self, game):
        self.game = game
        self.files = json.loads((WORK / "re" / game / "scripts.json").read_text(encoding="utf-8"))
        self.meta = open(WORK / "re" / game / "global-metadata.dat", "rb").read()
        self.cache = {}

    def _load(self, file_id):
        if file_id not in self.cache:
            _f, code, _size, tables = load_script(self.game, file_id, self.files, self.meta)
            masked = set()
            for t in tables:
                if t:
                    masked.update(relocation_positions(t)[1])
            self.cache[file_id] = (code, masked, {})
        return self.cache[file_id]

    def get(self, file_id, address):
        code, masked, memo = self._load(file_id)
        if address in memo:
            return memo[address]
        if address >= len(code):
            # внешняя функция (в BK2 библиотека вызывается по общему адресу)
            memo[address] = f"ext:{address:x}"
            return memo[address]
        out = bytearray()
        pc = address
        while pc < len(code) and len(out) < 4096:
            op = canonical_opcode(code[pc], self.game)
            if op >= 0x8B:
                break
            size = OPERAND_SIZE[op]
            out.append(op)
            operand = code[pc + 1:pc + 1 + size]
            out += b"\0" * size if (pc + 1) in masked else operand
            pc += 1 + size
            if op == OP_RET:
                break
        memo[address] = hashlib.sha1(bytes(out)).hexdigest()[:12]
        return memo[address]


def frame_arg_index(nargs, operand):
    """Аргумент по смещению в кадре: последний аргумент лежит по 0x8, каждый следующий +4."""
    j = ((operand & ~3) - 8) // 4
    index = nargs - 1 - j
    return index if 0 <= index < nargs else None


def frame_ref(arg):
    return arg["t"] == "expr" and arg["v"] in ("OP_LSD", "OP_LSB") and "x" in arg


def derive_specs(calls, fp, story, is_name):
    """Спецификации функций реплики: (файл, вход) -> (число аргументов, позиция реплики, источник имени).

    Источник имени: ("arg", j) — аргумент функции, ("zero",) — табличка не показывается,
    ("id", ID) — функция всегда ставит одно и то же имя."""
    base = Counter()
    for fid, cl in calls.items():
        for c in cl:
            if c["op"] != "OP_CALL":
                continue
            args = c["args"]
            for i in range(len(args) - 1):
                if story(args[i]) and args[i + 1]["t"] == "id" and is_name(args[i + 1]["v"]):
                    base[(fp.get(fid, c["target"]), len(args), i)] += 1
    base_fp = {}
    for (f, n, i), k in base.most_common():
        base_fp.setdefault((f, n), (n, i, ("arg", i + 1)))

    entries, nargs, owner = {}, {}, {}
    for fid, cl in calls.items():
        targets = sorted({c["target"] for c in cl if c["op"] == "OP_CALL"} | {0})
        entries[fid] = targets
        counts = defaultdict(Counter)
        for c in cl:
            if c["op"] == "OP_CALL":
                counts[c["target"]][len(c["args"])] += 1
        nargs[fid] = {t: cnt.most_common(1)[0][0] for t, cnt in counts.items()}
        from bisect import bisect_right
        owner[fid] = [targets[bisect_right(targets, c["pc"]) - 1] for c in cl]

    specs = {}
    for fid, targets in entries.items():
        for t in targets:
            n = nargs[fid].get(t)
            if n is not None and (fp.get(fid, t), n) in base_fp:
                specs[(fid, t)] = base_fp[(fp.get(fid, t), n)]

    changed = True
    while changed:
        changed = False
        for fid, cl in calls.items():
            for c, w in zip(cl, owner[fid]):
                if c["op"] != "OP_CALL" or (fid, w) in specs or (fid, c["target"]) not in specs:
                    continue
                n, mi, src = specs[(fid, c["target"])]
                wn = nargs[fid].get(w)
                if wn is None or len(c["args"]) != n or not frame_ref(c["args"][mi]):
                    continue
                w_msg = frame_arg_index(wn, c["args"][mi]["x"])
                if w_msg is None:
                    continue
                if src[0] == "arg":
                    a = c["args"][src[1]]
                    if a["t"] == "imm" and a["v"] == 0:
                        w_src = ("zero",)
                    elif a["t"] == "id":
                        w_src = ("id", a["v"])
                    elif frame_ref(a) and frame_arg_index(wn, a["x"]) is not None:
                        w_src = ("arg", frame_arg_index(wn, a["x"]))
                    else:
                        w_src = ("unknown",)
                else:
                    w_src = src
                specs[(fid, w)] = (wn, w_msg, w_src)
                changed = True
    return specs, base_fp


def collect(game):
    calls = json.loads((WORK / "re" / game / "calls.json").read_text(encoding="utf-8"))
    en, cat = load_lines(game)
    fp = Fingerprints(game)
    story = lambda a: a["t"] == "id" and cat.get(a["v"]) in STORY  # noqa: E731
    is_name = lambda v: name_like(v, en)  # noqa: E731
    name_tag, print_fn = sysfunc_index(game, "_SetNameTag"), sysfunc_index(game, "_Print")
    specs, base_fp = derive_specs(calls, fp, story, is_name)

    def actor_of(args):
        a = args[-1] if args else None
        return a["v"] if a and a["t"] == "imm" and 0 <= a["v"] < 256 else None

    obs = defaultdict(list)
    for fid, cl in calls.items():
        last_tag = None
        last_talk = None
        for c in cl:
            args = c["args"]
            if c["op"] == "OP_CALSS" and c["target"] == name_tag and len(args) == 2:
                last_tag = args[0]
                continue
            if c["op"] == "OP_CALSS" and c["target"] == print_fn and len(args) == 2 and story(args[0]):
                kind = "inline_tag" if last_tag and last_tag["t"] == "id" else "print_no_tag"
                obs[args[0]["v"]].append({"file": fid, "pc": c["pc"], "via": kind,
                                          "name_id": last_tag["v"] if kind == "inline_tag" else None,
                                          "no_tag": kind == "print_no_tag"})
                last_tag = None
                continue
            spec = specs.get((fid, c["target"])) if c["op"] == "OP_CALL" else None
            if not spec or len(args) != spec[0] or not story(args[spec[1]]):
                paired = False
                for i in range(len(args) - 1):
                    if story(args[i]) and args[i + 1]["t"] == "id" and is_name(args[i + 1]["v"]):
                        obs[args[i]["v"]].append({"file": fid, "pc": c["pc"], "via": "direct_pair",
                                                  "name_id": args[i + 1]["v"]})
                        paired = True
                stories = [a for a in args if story(a)]
                actor = actor_of(args)
                # Продолжение реплики в том же окне: функция без имени, тот же актёр, что у
                # предыдущей реплики с табличкой в этом файле.
                if (not paired and c["op"] == "OP_CALL" and len(stories) == 1 and actor is not None
                        and last_talk and last_talk["actor"] == actor and last_talk.get("name_id")):
                    obs[stories[0]["v"]].append({"file": fid, "pc": c["pc"], "via": "continued",
                                                 "continued_name_id": last_talk["name_id"],
                                                 "after": last_talk["text_id"]})
                continue
            n, mi, src = spec
            item = {"file": fid, "pc": c["pc"], "via": "talk_call", "func": f"{c['target']:x}"}
            if src[0] == "arg":
                a = args[src[1]]
                if a["t"] == "id" and is_name(a["v"]):
                    item["name_id"] = a["v"]
                elif a["t"] == "imm" and a["v"] == 0:
                    item["no_tag"] = True
                else:
                    item["dynamic"] = a.get("v") if a["t"] in ("expr", "id") else a["t"]
            elif src[0] == "id":
                item["name_id"] = src[1]
            elif src[0] == "zero":
                item["no_tag"] = True
            else:
                item["dynamic"] = "unknown"
            voice = args[mi - 1] if mi > 0 else None
            if voice and voice["t"] == "imm" and (voice["v"] >> 24) == 0x60:
                item["voice"] = f"{voice['v']:08X}"
            item["actor"] = actor_of(args)
            obs[args[mi]["v"]].append(item)
            last_talk = {"actor": item["actor"], "name_id": item.get("name_id"), "text_id": args[mi]["v"]}
    return obs, (specs, base_fp), en, cat


def summarize(obs, en, cat):
    rows = []
    for text_id in sorted(t for t, c in cat.items() if c in STORY):
        items = obs.get(text_id, [])
        names = Counter(i["name_id"] for i in items if i.get("name_id"))
        continued = Counter(i["continued_name_id"] for i in items if i.get("continued_name_id"))
        no_tag = sum(1 for i in items if i.get("no_tag"))
        dynamic = sum(1 for i in items if i.get("dynamic") is not None)
        text = en.get(text_id, "")
        if names:
            status = "name_tag" if len(names) == 1 else "name_tag_conflict"
        elif continued:
            status = "continued" if len(continued) == 1 else "name_tag_conflict"
            names = continued
        elif dynamic:
            status = "dynamic"
        elif no_tag:
            status = "no_tag"
        elif "<a1>" in text:
            status = "choice"
        elif "<" not in text and "\n" not in text.strip() and len(text.strip()) <= 40 and text.strip()[-1:] not in ".!?…":
            status = "label"
        else:
            status = "not_found"
        rows.append({
            "id": text_id, "status": status,
            "names": [{"id": n, "en": en.get(n, "").strip(), "calls": k} for n, k in names.most_common()],
            "no_tag_calls": no_tag, "dynamic_calls": dynamic,
            "voices": sorted({i["voice"] for i in items if i.get("voice")}),
            "files": sorted({i["file"] for i in items}),
            "calls": len(items),
        })
    return rows


def sequences(obs):
    """Файл скрипта -> реплики в порядке кода: [позиция, ID, имя | None, признак]."""
    per_file = defaultdict(list)
    for text_id, items in obs.items():
        for i in items:
            if i.get("name_id"):
                mark, name = "tag", i["name_id"]
            elif i.get("continued_name_id"):
                mark, name = "continued", i["continued_name_id"]
            else:
                mark, name = ("no_tag" if i.get("no_tag") else "dynamic"), None
            per_file[i["file"]].append([i["pc"], text_id, name, mark])
    return {f: sorted(v) for f, v in sorted(per_file.items())}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--game", default="bk1")
    args = parser.parse_args()
    obs, (specs, base_fp), en, cat = collect(args.game)
    rows = summarize(obs, en, cat)
    out = ROOT / "base" / "evidence" / f"{args.game}_script_speakers.jsonl"
    write_jsonl(out, rows)
    seq_path = ROOT / "base" / "scenes" / f"{args.game}_script_sequences.json"
    seq_path.write_text(json.dumps(sequences(obs), ensure_ascii=False, separators=(",", ":")) + "\n",
                        encoding="utf-8", newline="\n")
    by_cat = defaultdict(Counter)
    for r in rows:
        by_cat[cat[r["id"]]][r["status"]] += 1
    print(f"{args.game}: базовых функций реплики {len(base_fp)}, функций с выведенной спецификацией "
          f"{len(specs)}, сюжетных ID {len(rows)}")
    for c, cnt in by_cat.items():
        print(f"  {c}: {dict(cnt)}")
    print("->", out.relative_to(ROOT))


if __name__ == "__main__":
    main()
