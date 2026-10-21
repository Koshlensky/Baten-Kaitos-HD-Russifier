"""Статусы проверки по D-032: translated → qa_passed → sampled_review | edited.

    python -m tools.bkloc.review_flow qa-pass <пакет>          автоматический QA: translated → qa_passed
    python -m tools.bkloc.review_flow sample <пакет> [--share 0.25] [--out F]
                                                               обязательные проблемные строки + случайная выборка
    python -m tools.bkloc.review_flow selftest                 самотест отбора, без матрицы
    python -m tools.bkloc.review_flow mark-sampled <пакет>     остальные qa_passed → sampled_review

sampled_review ставится только строкам, которые редактор не видел построчно, и только если
выборка пакета (не меньше MIN_SHARE строк) действительно отредактирована. Полной литературной
редактурой (edited) такой пакет не считается.
"""
import argparse
import collections
import datetime
import json
import math
import random
import sys

from tools.bkloc import categories, pipeline
from tools.bkloc.paths import MATRIX, read_jsonl, write_jsonl
from tools.qa import checks, fit, gender_lint

MIN_SHARE = 0.2
# error блокирует QA, warning требует редактора. info (модальность, усилители) в обязательный
# список не входит: эти подсказки и так попадают в пакет редактора как qa_hints.
MANDATORY_SEVERITIES = {"error", "warning"}


def batch_lines(batch_id):
    if pipeline.is_unit(batch_id):
        game, records, _meta, _prev = pipeline.work_records(batch_id, "editor")
        return game, records
    game, batch, _prev = pipeline.batch_info(batch_id)
    excluded = pipeline.excluded_keys(game)
    return game, [r for r in pipeline.batch_records(game, batch) if r["key"] not in excluded]


def line_issues(game, records):
    widths = collections.defaultdict(float)
    for r in pipeline.load_matrix(game):
        if r["category"] in fit.FIT_CATEGORIES:
            g = categories.group_of(r["text_id"])
            widths[g] = max(widths[g], fit.width(r["en"]) or 0)
    out = {}
    for r in records:
        if not r.get("ru"):
            continue
        r["_group"] = categories.group_of(r["text_id"])
        issues = checks.run_line_checks(r)
        issues += checks.waive(r, gender_lint.lint(r) + fit.check_fit(r, widths) + fit.check_glyphs(r))
        out[r["key"]] = issues
    return out


def save(game, changed, batch_id, action):
    now = datetime.datetime.now().isoformat(timespec="seconds")
    for path in (MATRIX / game).glob("*.jsonl"):
        rows = read_jsonl(path)
        touched = False
        for row in rows:
            if row["key"] in changed:
                row["status"] = changed[row["key"]]
                row["history"].append({"ts": now, "actor": "qa/local", "action": action})
                touched = True
        if touched:
            write_jsonl(path, rows)
    state = pipeline.load_state()
    entry = (state.setdefault("units", {}).setdefault(batch_id, {"status": "pending", "updated": None, "runs": []})
             if pipeline.is_unit(batch_id) else state["games"][game][batch_id])
    return state, entry, now


def set_batch_status(state, entry, now, game, batch_id):
    statuses = {r["status"] for r in batch_lines(batch_id)[1] if r.get("ru")}
    for status in ("translated", "qa_passed", "sampled_review", "edited"):
        if status in statuses or status == "edited":
            entry["status"] = status
            break
    entry["updated"] = now
    pipeline.save_state(state)


def cmd_qa_pass(batch_id):
    game, records = batch_lines(batch_id)
    issues = line_issues(game, [r for r in records if r["status"] == "translated"])
    changed, failed = {}, {}
    for key, found in issues.items():
        errors = [f"{code}: {msg}" for sev, code, msg in found if sev == "error"]
        if errors:
            failed[key] = errors
        else:
            changed[key] = "qa_passed"
    state, entry, now = save(game, changed, batch_id, "qa_pass")
    set_batch_status(state, entry, now, game, batch_id)
    print(f"{batch_id}: qa_passed {len(changed)}, с ошибками {len(failed)}")
    for key, errors in failed.items():
        print(f"  {key}: {'; '.join(errors)}")
    return 1 if failed else 0


def speaker_id(record):
    speaker = record.get("speaker")
    return speaker["id"] if isinstance(speaker, dict) else speaker


def choose_sample(pool, issues, share, seed, cast_ids):
    """Обязательные строки и случайная выборка считаются раздельно.

    mandatory — каждая строка с error или warning. Список не обрезается долей share:
    раньше обрезка [:max(need, min(len(priority), need * 2))] теряла предупреждения,
    когда их было больше удвоенной доли.
    sample — оценка качества: ceil(share * размер пакета) строк без проблем.
    Внутри выборки сначала идут реплики главных героев, затем остальные; ни одна
    обязательная строка выборкой не вытесняется.
    """
    held = [r for r in pool if r.get("status") in ("needs_context", "blocked_decision")]
    mandatory = held + [r for r in pool if r not in held and any(
        sev in MANDATORY_SEVERITIES for sev, _c, _m in issues.get(r["key"], []))]
    mandatory_keys = {r["key"] for r in mandatory}
    need = math.ceil(len(pool) * share) if pool and share > 0 else 0
    rest = [r for r in pool if r["key"] not in mandatory_keys]
    cast_rest = [r for r in rest if speaker_id(r) in cast_ids]
    other = [r for r in rest if r not in cast_rest]
    rng = random.Random(seed)
    rng.shuffle(cast_rest)
    rng.shuffle(other)
    sample = (cast_rest + other)[:min(need, len(rest))]

    def ordered(rows):
        return [r["key"] for r in sorted(rows, key=lambda r: r["global_order"])]

    return ordered(mandatory), ordered(sample)


def cmd_sample(batch_id, share, out):
    """Обязательные проблемные строки плюс случайная доля чистых строк."""
    game, records = batch_lines(batch_id)
    pool = [r for r in records if r["status"] in ("qa_passed", "translated", "needs_context", "blocked_decision")]
    if not pool:
        raise SystemExit(f"{batch_id}: нет строк для выборки")
    issues = line_issues(game, pool)
    from tools.bkloc.tiers import main_cast
    mandatory, sample = choose_sample(pool, issues, share, batch_id, main_cast())
    by_order = {r["key"]: r["global_order"] for r in pool}
    keys = sorted(set(mandatory) | set(sample), key=lambda k: by_order[k])
    report = {"batch_id": batch_id, "pool": len(pool), "share": share,
              "mandatory": mandatory, "sample": sample}
    if out:
        from pathlib import Path
        path = Path(out)
        path.write_text("\n".join(keys) + "\n", encoding="utf-8")
        path.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{batch_id}: обязательных {len(mandatory)}, случайная выборка {len(sample)}, "
          f"в редактуру {len(keys)} из {len(pool)}")
    return 0


def self_test():
    """Отбор без матрицы. Падает, если проблемная строка вытеснена долей выборки."""
    failed = 0

    def line(i, speaker="npc"):
        return {"key": f"k{i}", "speaker": speaker, "global_order": i}

    def check(name, ok):
        nonlocal failed
        failed += not ok
        print(("ok  " if ok else "FAIL"), name)

    pool = [line(i) for i in range(40)]
    issues = {f"k{i}": [("warning", "latin", "")] for i in range(30)}
    mandatory, sample = choose_sample(pool, issues, 0.25, "seed", {"hero"})
    check("все 30 предупреждений обязательны, доля 25% их не режет", len(mandatory) == 30 and set(mandatory) == {f"k{i}" for i in range(30)})
    check("случайная выборка не забирает обязательные строки", not (set(sample) & set(mandatory)))
    check("случайная выборка — 25% пакета из чистых строк", len(sample) == 10)

    issues_err = {f"k{i}": [("error", "tags_structure", "")] for i in range(15)}
    mandatory, sample = choose_sample(pool, issues_err, 0.25, "seed", set())
    check("ошибки QA тоже обязательны и не режутся", len(mandatory) == 15)

    issues_info = {f"k{i}": [("info", "modality", "")] for i in range(40)}
    mandatory, sample = choose_sample(pool, issues_info, 0.25, "seed", set())
    check("info не делает строку обязательной", mandatory == [] and len(sample) == 10)

    cast_pool = [line(i, "hero" if i < 8 else "npc") for i in range(20)]
    mandatory, sample = choose_sample(cast_pool, {}, 0.25, "seed", {"hero"})
    check("внутри выборки сначала герои, но не сверх доли", len(sample) == 5 and set(sample) <= {f"k{i}" for i in range(8)})

    full = {f"k{i}": [("warning", "numbers", "")] for i in range(40)}
    mandatory, sample = choose_sample(pool, full, 0.25, "seed", set())
    check("если проблемны все строки, выборка пуста и ни одна не потеряна", len(mandatory) == 40 and sample == [])

    again = choose_sample(pool, issues, 0.25, "seed", {"hero"})
    check("один и тот же seed даёт ту же выборку", again == choose_sample(pool, issues, 0.25, "seed", {"hero"}))

    held = [dict(line(0), status="needs_context"), dict(line(1), status="blocked_decision")] + [line(i) for i in range(2, 8)]
    mandatory, sample = choose_sample(held, {}, 0.25, "seed", set())
    check("needs_context и blocked_decision обязательны и не режутся долей", set(mandatory) == {"k0", "k1"})
    print(f"самотест выборки редактуры: {9 - failed}/9")
    return failed


def cmd_mark_sampled(batch_id):
    game, records = batch_lines(batch_id)
    translated = [r for r in records if r.get("ru")]
    reviewed = [r for r in translated if r["status"] in ("edited", "approved")
                and any(h["action"] == "editor" for h in r["history"])]
    if len(reviewed) < MIN_SHARE * len(translated):
        raise SystemExit(f"{batch_id}: отредактировано {len(reviewed)} из {len(translated)} — меньше {MIN_SHARE:.0%}, "
                         "sampled_review не ставится")
    pending = [r for r in translated if r["status"] == "translated"]
    if pending:
        raise SystemExit(f"{batch_id}: {len(pending)} строк не прошли QA (translated) — сначала qa-pass")
    changed = {r["key"]: "sampled_review" for r in translated if r["status"] == "qa_passed"}
    state, entry, now = save(game, changed, batch_id, "sampled_review")
    set_batch_status(state, entry, now, game, batch_id)
    print(f"{batch_id}: sampled_review {len(changed)}, отредактировано {len(reviewed)} из {len(translated)}")
    return 0


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("qa-pass")
    s.add_argument("batch_id")
    s = sub.add_parser("sample")
    s.add_argument("batch_id")
    s.add_argument("--share", type=float, default=0.25)
    s.add_argument("--out")
    s = sub.add_parser("mark-sampled")
    s.add_argument("batch_id")
    sub.add_parser("selftest")
    args = p.parse_args()
    if args.cmd == "qa-pass":
        return cmd_qa_pass(args.batch_id)
    if args.cmd == "sample":
        return cmd_sample(args.batch_id, args.share, args.out)
    if args.cmd == "selftest":
        return 1 if self_test() else 0
    return cmd_mark_sampled(args.batch_id)


if __name__ == "__main__":
    raise SystemExit(main())
