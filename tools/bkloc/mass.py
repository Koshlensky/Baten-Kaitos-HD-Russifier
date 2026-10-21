"""Массовый перевод D-033: этапы с контрольными точками, сборка пакетов, приём вывода, сводка.

    python -m tools.bkloc.mass plan                         построить план этапов (base/progress/mass_plan_d033.json)
    python -m tools.bkloc.mass status                       сводка по этапам и готовым строкам
    python -m tools.bkloc.mass packets <этап> --role translator|editor     собрать входные пакеты этапа
    python -m tools.bkloc.mass apply <этап> --role translator|editor       принять выводы этапа (с проверкой тегов) и QA
    python -m tools.bkloc.mass stats <этап>                 доля правок редактора, статусы, needs_context
    python -m tools.bkloc.mass estimate --tokens-per-1000 N [--budget-tokens M]   прогноз по замеру расхода

Правила: переводчик — prompts/translator_sonnet.md, редактор — prompts/editor.md (без заметок переводчика).
Выводы лежат в runs/<дата этапа>/<пакет>_<роль>_output.json. Допуск (approvals.json) проверяется в pipeline:
BK2 вне пилота/пробы требует отдельного решения владельца (область main_bk2).
"""
import argparse
import collections
import datetime
import json
import re
import subprocess
import sys
from pathlib import Path

from tools.bkloc import pipeline, tags
from tools.bkloc.paths import BASE, ROOT, RUNS, SCENES, write_json

PLAN = BASE / "progress" / "mass_plan_d033.json"
STAGE_MIN, STAGE_MAX = 3500, 5000
MODEL = "claude-sonnet-5.5"


def pending_batches(game):
    """Пакеты с непереведёнными строками в порядке обработки (очередь BK1 по сюжету, BK2 — по порядку пакетов)."""
    excluded = pipeline.excluded_keys(game)
    by_batch = collections.defaultdict(list)
    for r in pipeline.load_matrix(game):
        if r["status"] == "untranslated" and r["key"] not in excluded:
            by_batch[r["batch_id"]].append(r)
    out = []
    for b, info in pipeline.queue_order(game):
        rows = by_batch.get(b["batch_id"])
        if rows:
            out.append({"batch_id": b["batch_id"], "lines": len(rows), "chars": sum(len(r["en"]) for r in rows),
                        "category": b.get("category", "dialogue"), "chapter": info.get("chapter")})
    return out


def build_plan():
    stages, current, n = [], [], 0
    for game in ("bk1", "bk2"):
        current, n = [], 0
        for b in pending_batches(game):
            if current and n + b["lines"] > STAGE_MAX:
                stages.append(current)
                current, n = [], 0
            current.append(b)
            n += b["lines"]
            if n >= STAGE_MIN:
                stages.append(current)
                current, n = [], 0
        if current:
            stages.append(current)
    plan = {"decision": "D-033", "created": datetime.datetime.now().isoformat(timespec="seconds"),
            "note": "Контрольная точка после каждого этапа: QA, коммит, reports/progress.md, замер Usage. Запуск этапа — только "
                    "после подтверждения владельца. BK2 требует допуска main_bk2 в approvals.json.",
            "stages": []}
    for i, st in enumerate(stages, 1):
        plan["stages"].append({
            "stage": f"S{i:02d}", "game": st[0]["batch_id"].split(":")[0], "lines": sum(b["lines"] for b in st),
            "input_chars_estimate": sum(b["chars"] for b in st), "status": "planned",
            "batches": [b["batch_id"] for b in st]})
    write_json(PLAN, plan)
    return plan


def load_plan():
    return json.loads(PLAN.read_text(encoding="utf-8"))


def stage_batches(stage):
    for s in load_plan()["stages"]:
        if s["stage"] == stage:
            return s["batches"]
    raise SystemExit(f"Нет этапа {stage}")


def run_dir():
    return RUNS / datetime.datetime.now().strftime("%Y%m%d")


def out_path(bid, role):
    for d in sorted(RUNS.glob("*"), reverse=True):
        p = d / f"{bid.replace(':', '_')}_{role}_output.json"
        if p.exists():
            return p
    return run_dir() / f"{bid.replace(':', '_')}_{role}_output.json"


def in_path(bid, role):
    for d in sorted(RUNS.glob("*"), reverse=True):
        p = d / f"{bid.replace(':', '_')}_{role}_input.md"
        if p.exists():
            return p
    raise SystemExit(f"Нет входного пакета {bid} {role}")


def cmd_packets(stage, role):
    prompt = "translator_sonnet" if role == "translator" else None
    total = 0
    for bid in stage_batches(stage):
        path, _sha, n = pipeline.build_packet(bid, role, prompt)
        total += n
        print(f"{bid}: {n} строк, {len(path.read_text(encoding='utf-8'))} символов → {path.name}")
    print("всего строк", total)


def cmd_apply(stage, role):
    ok = 0
    for bid in stage_batches(stage):
        path = out_path(bid, role)
        if not path.exists():
            print(f"{bid}: нет вывода")
            continue
        text = in_path(bid, role).read_text(encoding="utf-8")
        expected = json.loads(re.search(r"## Строки.*?```json\s*(\[.*\])\s*```", text, re.S).group(1))
        en = {x["key"]: x["en"] for x in expected}
        items = pipeline.parse_output(path)
        problems = []
        if [i["key"] for i in items] != list(en):
            problems.append("ключи или порядок не совпадают")
        for i in items:
            if i["key"] in en:
                errs, _w = pipeline.check_ru(tags.unmask(en[i["key"]]), tags.unmask(i.get("ru", "")))
                if errs:
                    problems.append(f"{i['key']}: {errs}")
        if problems:
            print(f"{bid}: ПРОБЛЕМЫ {problems[:4]}")
            continue
        dry = pipeline.apply_output(bid, role, path, MODEL, True)
        if dry["rejected"]:
            print(f"{bid}: ОТКЛОНЕНО {dry['rejected'][:3]}")
            continue
        rep = pipeline.apply_output(bid, role, path, MODEL, False)
        ok += rep["accepted"]
        print(f"{bid}: принято {rep['accepted']}")
        if role == "translator":
            subprocess.run([sys.executable, "-m", "tools.bkloc.review_flow", "qa-pass", bid], cwd=ROOT, check=False)
    print("всего принято", ok)


def editor_changed(r):
    """Строку правил редактор Sonnet: в истории есть запись редактора с ru_before (первая правка текста).
    Повторный приём той же правки (запись без ru_before) счёт не меняет."""
    return any(h["action"] == "editor" and "sonnet" in h["actor"] and "ru_before" in h for h in r["history"])


def cmd_stats(stage):
    tot = collections.Counter()
    for bid in stage_batches(stage):
        game, batch, _ = pipeline.batch_info(bid)
        scenes = set(batch["scene_ids"])
        st = collections.Counter()
        for r in pipeline.load_matrix(game):
            if r["scene_id"] not in scenes:
                continue
            st[r["status"]] += 1
            ed = [h for h in r["history"] if h["action"] == "editor" and "sonnet" in h["actor"]]
            if r["status"] == "edited" and ed:
                tot["edited"] += 1
                tot["changed"] += editor_changed(r)
        print(bid, dict(st))
    if tot["edited"]:
        print(f"правок редактора: {tot['changed']} из {tot['edited']} ({tot['changed'] / tot['edited']:.1%})")


def cmd_status():
    plan = load_plan()
    pending = {}
    for game in ("bk1", "bk2"):
        for r in pipeline.load_matrix(game):
            if r["status"] == "untranslated":
                pending[r["batch_id"]] = pending.get(r["batch_id"], 0) + 1
    for s in plan["stages"]:
        remaining = sum(pending.get(bid, 0) for bid in s["batches"])
        print(f"{s['stage']} {s['game']} строк {s['lines']}, ещё не переведено {remaining}, пакетов {len(s['batches'])}, {s['status']}")


def cmd_estimate(tokens_per_1000, budget_tokens):
    plan = load_plan()
    remaining = sum(s["lines"] for s in plan["stages"])
    need = remaining / 1000 * tokens_per_1000
    print(f"осталось строк {remaining}; по замеру {tokens_per_1000:,.0f} токенов на 1000 строк нужно ≈ {need / 1e6:.1f} млн токенов")
    if budget_tokens:
        print(f"доступно {budget_tokens / 1e6:.1f} млн → {'укладывается' if need <= budget_tokens else 'НЕ укладывается'} "
              f"({need / budget_tokens:.0%} бюджета)")
    for s in plan["stages"]:
        print(f"  {s['stage']}: {s['lines']} строк ≈ {s['lines'] / 1000 * tokens_per_1000 / 1e6:.2f} млн токенов")


# --- рабочие единицы (D-036): несколько пакетов в одном сжатом входе -------------------------------

UNIT_LINES = 150


def units_file(name):
    return BASE / "progress" / f"units_{name}.json"


def pending_counts():
    out = {}
    for game in ("bk1", "bk2"):
        excluded = pipeline.excluded_keys(game)
        for r in pipeline.load_matrix(game):
            if r["status"] == "untranslated" and r["key"] not in excluded:
                out[r["batch_id"]] = out.get(r["batch_id"], 0) + 1
    return out


def cmd_units(name, batches, max_lines):
    """Нарезать пакеты (по порядку) на единицы до UNIT_LINES строк; не смешивать игры и семейства очереди."""
    from tools.bkloc.units import queue_info
    counts = pending_counts()
    infos = {g: queue_info(g) for g in ("bk1", "bk2")}
    units, cur, n, taken = [], [], 0, 0
    for bid in batches:
        c = counts.get(bid, 0)
        if not c:
            continue
        if max_lines and taken + c > max_lines:
            break
        info = infos[bid.split(":")[0]].get(bid, {})
        key = (bid.split(":")[0], info.get("block") if bid.startswith("bk2") else None)
        if cur and (n + c > UNIT_LINES or key != cur_key):
            units.append({"batches": cur, "lines": n})
            cur, n = [], 0
        if not cur:
            cur_key = key
        cur.append(bid)
        n += c
        taken += c
    if cur:
        units.append({"batches": cur, "lines": n})
    for i, u in enumerate(units, 1):
        u["id"] = f"{name}_{i:02d}"
    write_json(units_file(name), {"decision": "D-036", "name": name, "created": datetime.datetime.now().isoformat(timespec="seconds"),
                                  "total_lines": sum(u["lines"] for u in units), "units": units})
    for u in units:
        print(u["id"], u["lines"], "строк:", ", ".join(u["batches"]))
    print("всего", sum(u["lines"] for u in units), "строк в", len(units), "единицах")


def cmd_fixunits(name, keys_file):
    """Единицы точечной правки: ключи из JSON (список объектов с key и batch), по играм и пакетам, до UNIT_LINES строк."""
    items = json.loads(Path(keys_file).read_text(encoding="utf-8"))
    by_batch = collections.OrderedDict()
    for it in items:
        by_batch.setdefault(it["batch"], []).append(it["key"])
    order = {}
    for game in ("bk1", "bk2"):
        for i, r in enumerate(sorted(pipeline.load_matrix(game), key=lambda r: r["global_order"])):
            order[r["batch_id"]] = min(order.get(r["batch_id"], 10 ** 9), i)
    units, cur, n = [], None, 0
    for bid in sorted(by_batch, key=lambda b: (b.split(":")[0], order.get(b, 0))):
        c = len(by_batch[bid])
        if cur is None or cur["game"] != bid.split(":")[0] or n + c > UNIT_LINES:
            cur = {"game": bid.split(":")[0], "batches": [], "keys": [], "lines": 0}
            units.append(cur)
            n = 0
        cur["batches"].append(bid)
        cur["keys"] += by_batch[bid]
        cur["lines"] += c
        n += c
    for i, u in enumerate(units, 1):
        u["id"] = f"{name}_{i:02d}"
        u.pop("game")
    write_json(units_file(name), {"decision": "D-036", "name": name, "kind": "fix", "total_lines": len(items), "units": units})
    for u in units:
        print(u["id"], u["lines"], "строк:", ", ".join(u["batches"][:6]), "…" if len(u["batches"]) > 6 else "")


def load_units(name):
    return json.loads(units_file(name).read_text(encoding="utf-8"))["units"]


def cmd_unit_packets(name, role):
    from tools.bkloc import units as U
    run = run_dir()
    run.mkdir(parents=True, exist_ok=True)
    total = chars = 0
    for u in load_units(name):
        path, n = U.build_unit(u["id"], u["batches"], role, run, only_keys=u.get("keys"))
        size = len(path.read_text(encoding="utf-8"))
        total += n
        chars += size
        print(f"{u['id']}: {n} строк, {size} символов → {path.name}")
    print(f"всего строк {total}, символов входа {chars}")


def find_latest(unit, role, kind):
    for d in sorted(RUNS.glob("*"), reverse=True):
        p = d / f"unit_{unit}_{role}_{kind}"
        if p.exists():
            return p
    return None


def cmd_unit_apply(name, role):
    from tools.bkloc import units as U
    accepted = 0
    for u in load_units(name):
        outp = find_latest(u["id"], role, "output.json")
        mapp = find_latest(u["id"], role, "map.json")
        if not outp or not mapp:
            print(f"{u['id']}: нет вывода или карты")
            continue
        meta = json.loads(mapp.read_text(encoding="utf-8"))
        items = pipeline.parse_output(outp)
        memory = [i for i in items if i.get("key") == "__memory__"]
        items = [i for i in items if i.get("key") != "__memory__"]
        keys = meta["keys"]
        if [i["key"] for i in items] != list(keys):
            missing = set(keys) - {i["key"] for i in items}
            extra = {i["key"] for i in items} - set(keys)
            print(f"{u['id']}: ПРОБЛЕМА ключей: нет {len(missing)}, лишних {len(extra)}, порядок совпадает: "
                  f"{[i['key'] for i in items if i['key'] in keys] == [k for k in keys if k in {i['key'] for i in items}]}")
            continue
        bad = []
        for i in items:  # двойное экранирование: литеральное \n вместо перевода строки
            if "\\n" in i.get("ru", "") and "\\n" not in keys[i["key"]]["en"]:
                i["ru"] = i["ru"].replace("\\n", "\n")
        for i in items:
            errs, _w = pipeline.check_ru(tags.unmask(keys[i["key"]]["en"]), tags.unmask(i.get("ru", "")))
            if errs and i.get("status") not in ("needs_context", "blocked_decision"):
                bad.append((i["key"], errs))
        if bad:
            print(f"{u['id']}: ОШИБКИ ТЕГОВ {bad[:4]}")
            continue
        for bid in meta["batches"]:
            part = [i for i in items if keys[i["key"]]["batch"] == bid]
            path = out_path(bid, role).with_name(f"{bid.replace(':', '_')}_{role}_output.json")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(part, ensure_ascii=False), encoding="utf-8")
            dry = pipeline.apply_output(bid, role, path, MODEL, True)
            if dry["rejected"]:
                print(f"{bid}: ОТКЛОНЕНО {dry['rejected'][:3]}")
                continue
            rep = pipeline.apply_output(bid, role, path, MODEL, False)
            accepted += rep["accepted"]
            if role == "translator":
                subprocess.run([sys.executable, "-m", "tools.bkloc.review_flow", "qa-pass", bid], cwd=ROOT, check=False,
                               stdout=subprocess.DEVNULL)
        if role == "editor" and memory:
            ok = U.append_memory(meta["batches"][0].split(":")[0], meta["batches"], memory[0].get("ru", ""))
            print(f"{u['id']}: память {'записана' if ok else 'не записывается (не событие BK2)'}")
        print(f"{u['id']}: принято {len(items)}")
    print("всего принято", accepted)


def cmd_unit_stats(name):
    batches = [b for u in load_units(name) for b in u["batches"]]
    tot = collections.Counter()
    sts = collections.Counter()
    for game in ("bk1", "bk2"):
        mine = {b for b in batches if b.startswith(game)}
        for r in pipeline.load_matrix(game):
            if r["batch_id"] not in mine:
                continue
            ed = [h for h in r["history"] if h["action"] == "editor" and "sonnet" in h["actor"]]
            tr = [h for h in r["history"] if h["action"] == "translator" and "sonnet" in h["actor"]]
            if not tr:
                continue
            sts[r["status"]] += 1
            if r["status"] == "edited" and ed:
                tot["edited"] += 1
                tot["changed"] += editor_changed(r)
    print("статусы:", dict(sts))
    if tot["edited"]:
        print(f"правок редактора: {tot['changed']} из {tot['edited']} ({tot['changed'] / tot['edited']:.1%})")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("units")
    s.add_argument("--name", required=True)
    s.add_argument("--batches", required=True, help="пакеты через запятую или диапазон этапа S01[:N] — первые N строк этапа")
    s.add_argument("--max-lines", type=int, default=0)
    s = sub.add_parser("fixunits")
    s.add_argument("--name", required=True)
    s.add_argument("--keys-file", required=True)
    for nm in ("upackets", "uapply"):
        s = sub.add_parser(nm)
        s.add_argument("name")
        s.add_argument("--role", required=True, choices=("translator", "editor"))
    s = sub.add_parser("ustats")
    s.add_argument("name")
    sub.add_parser("plan")
    sub.add_parser("status")
    for name in ("packets", "apply"):
        s = sub.add_parser(name)
        s.add_argument("stage")
        s.add_argument("--role", required=True, choices=("translator", "editor"))
    s = sub.add_parser("stats")
    s.add_argument("stage")
    s = sub.add_parser("estimate")
    s.add_argument("--tokens-per-1000", type=float, required=True)
    s.add_argument("--budget-tokens", type=float, default=0)
    a = p.parse_args()
    if a.cmd == "units":
        batches = []
        for part in a.batches.split(","):
            if re.match(r"^S\d+$", part):
                batches += stage_batches(part)
            else:
                batches.append(part)
        cmd_units(a.name, batches, a.max_lines)
        return
    if a.cmd == "fixunits":
        cmd_fixunits(a.name, a.keys_file)
        return
    if a.cmd == "upackets":
        cmd_unit_packets(a.name, a.role)
        return
    if a.cmd == "uapply":
        cmd_unit_apply(a.name, a.role)
        return
    if a.cmd == "ustats":
        cmd_unit_stats(a.name)
        return
    if a.cmd == "plan":
        plan = build_plan()
        for s in plan["stages"]:
            print(s["stage"], s["game"], s["lines"], "строк", len(s["batches"]), "пакетов")
        print("всего", sum(s["lines"] for s in plan["stages"]))
    elif a.cmd == "status":
        cmd_status()
    elif a.cmd == "packets":
        cmd_packets(a.stage, a.role)
    elif a.cmd == "apply":
        cmd_apply(a.stage, a.role)
    elif a.cmd == "stats":
        cmd_stats(a.stage)
    else:
        cmd_estimate(a.tokens_per_1000, a.budget_tokens)


if __name__ == "__main__":
    main()

