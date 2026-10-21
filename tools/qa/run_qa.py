"""Полный запуск проверок качества с отчётом в reports/qa/.

  python -m tools.qa.run_qa [--game bk1] [--batch bk1:B0209] [--build] [--selftest]

Шаги: структура матрицы, карантин v1.0, построчные проверки (теги, пропуски,
непереведённое, числа, типографика, обращения, глоссарий), род (pymorphy3),
ширина строк интерфейса, при --build — сборка документа в копию бандла.
"""
import argparse
import collections
import datetime
import json
import sys

from tools.bkloc import categories
from tools.bkloc.matrix import load as load_matrix
from tools.bkloc.paths import GAMES, REPORTS, SCENES, write_json, write_text
from tools.qa import build_check, checks, fit, gender_lint, validate_matrix
try:
    from tools.qa import legacy_guard
except ImportError:      # в публичном дереве нет карантина v1.0
    legacy_guard = None


def selftest():
    """Проверки на синтетических записях (без матрицы)."""
    base = {"form": "line", "category": "dialogue", "register": "unknown", "status": "draft",
            "speaker_gender": "unknown", "addressee_gender": "unknown"}
    cases = [
        ({"en": "Hello!<w>", "ru": "Привет!"}, {"tags_structure"}),
        ({"en": "Got %s!<w>", "ru": "Получено!<w>"}, {"placeholders", "tags_structure"}),
        ({"en": "It costs 300 Gold.<w>", "ru": "Это стоит дорого.<w>"}, {"numbers"}),
        ({"en": "Hello there, friend.<w>", "ru": "Hello there, friend.<w>"}, {"untranslated"}),
        ({"en": "The Magnus is here.<w>", "ru": "Здесь лежит Mагнус.<w>"}, {"mixed_script"}),
        ({"en": "Are you ready?<w>", "ru": "Ты готов(а).<w>"}, {"slash_gender", "question_lost"}),
        ({"en": "<s0><a1>Yes\n<a2>No<q><s>", "ru": "<s0><a1>Да <a2>Нет<q><s>", "form": "choice"}, {"newlines"}),
        ({"en": "Use the <c1>gust boulder<c>.<w>", "ru": "Используй <c1>камень ветра.<w>"}, {"tags_unpaired"}),
        ({"en": "Come with me.<w>", "ru": "Пойдёмте со мной, ваше величество.<w>", "register": "ty"}, {"register"}),
        ({"en": "I'm fine, really.<w>", "ru": "Со мной всё хорошо, правда.<w>"}, set()),
        ({"en": "What's up, <n0>?<w>", "ru": "Что такое, <n0>?<w>"}, set()),
        ({"en": "<s0>    <a1>Yes\n    <a2>No<q><s>", "ru": "<s0>    <a1>Да\n    <a2>Нет<q><s>", "form": "choice"}, set()),
        ({"en": "Hey there, you two!<w>", "ru": "Привет вам обоим!<w>", "register": "ty", "addressee_gender": "group"}, set()),
        ({"en": "You're right. Let's go.<w>", "ru": "Верно. Идём.<w>"}, set()),
        ({"en": "He should be back by now.<w>", "ru": "Давно вернулся.<w>"}, {"intensifier", "modality"}),
        ({"en": "We might be able to fix it.<w>", "ru": "Может, получится починить.<w>"}, set()),
    ]
    failed = 0
    for fields, expected in cases:
        record = dict(base, **fields)
        got = {code for _s, code, _m in checks.run_line_checks(record)}
        ok = expected <= got and not ({"tags_structure", "placeholders", "untranslated"} & (got - expected))
        failed += not ok
        print(("ok  " if ok else "FAIL"), repr(record["ru"])[:60], "→", sorted(got), "" if ok else f"(ожидалось {sorted(expected)})")
    print(f"самотест построчных проверок: {len(cases) - failed}/{len(cases)}")
    from tools.bkloc import review_flow
    return failed + gender_lint.self_test() + scope_selftest() + review_flow.self_test()


def scope_selftest():
    """Допуск pipeline: отложенные категории (D-011, D-013) не попадают в границы, служебные роли без границ отклоняются."""
    from tools.bkloc import pipeline

    failed = 0

    def check(name, ok):
        nonlocal failed
        failed += not ok
        print(("ok  " if ok else "FAIL"), name)

    for game in ("bk1", "bk2"):
        rows = pipeline.load_matrix(game)
        music = {r["key"] for r in rows if r["category"] == "music_title"}
        for role in ("translator", "editor"):
            allowed = pipeline.approved_keys(game, role)
            check(f"допуск {role} {game}: названия музыки ({len(music)}) исключены", music and not (music & allowed))
            check(f"допуск {role} {game}: основные строки разрешены", len(allowed) > 1000)
        check(f"{game}: названия музыки не попадают в очередь", music <= pipeline.excluded_keys(game))
    check("BK2: служебные коды (internal) исключены по D-011",
          {r["key"] for r in pipeline.load_matrix("bk2") if r["category"] == "internal"} <= pipeline.excluded_keys("bk2"))
    try:
        pipeline.require_scope("bk2", "reviewer", [], "selftest")
        check("роль вне approvals.json не проверяется", True)
    except SystemExit:
        check("роль вне approvals.json не проверяется", False)
    return failed

def group_widths(records):
    widths = collections.defaultdict(float)
    for r in records:
        if r["category"] in fit.FIT_CATEGORIES:
            w = fit.width(r["en"])
            if w is not None:
                widths[r["_group"]] = max(widths[r["_group"]], w)
    return widths


def run(games, batch, build):
    report = {"started": datetime.datetime.now().isoformat(timespec="seconds"), "games": {}, "global": {}}
    errors = 0
    count_m, summary_m, samples_m = 0, {}, {}
    for game in games:
        count_m, summary_m, samples_m = validate_matrix.validate(game)
        report["games"].setdefault(game, {})["matrix"] = {"records": count_m, "errors": summary_m}
        errors += sum(summary_m.values())
    guard = legacy_guard.check() if legacy_guard else []
    report["global"]["legacy_guard"] = guard
    errors += len(guard)
    for game in games:
        records = load_matrix(game)
        if batch:
            scene_ids = set(next(b for b in json.loads((SCENES / f"{game}_batches.json").read_text(encoding="utf-8"))["batches"]
                                 if b["batch_id"] == batch)["scene_ids"])
            records = [r for r in records if r["scene_id"] in scene_ids]
        for r in records:
            r["_group"] = categories.group_of(r["text_id"])
        widths = group_widths(records)
        codes = collections.Counter()
        severities = collections.Counter()
        examples = collections.defaultdict(list)
        translated = 0
        for r in records:
            issues = checks.run_line_checks(r)
            if r.get("ru"):
                translated += 1
                issues += checks.waive(r, gender_lint.lint(r) + fit.check_fit(r, widths) + fit.check_glyphs(r))
            for severity, code, message in issues:
                codes[code] += 1
                severities[severity] += 1
                if len(examples[code]) < 5:
                    examples[code].append({"key": r["key"], "message": message, "ru": r["ru"][:100]})
        errors += severities["error"]
        statuses = collections.Counter(r["status"] for r in records)
        report["games"][game].update({"records_checked": len(records), "translated": translated,
                                      "statuses": dict(statuses), "severities": dict(severities),
                                      "codes": dict(codes), "examples": examples})
        if build:
            res = build_check.check_game(game)
            report["games"][game]["build"] = res
            errors += len(res["errors"])
    report["errors_total"] = errors
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    write_json(REPORTS / "qa" / f"qa_{stamp}.json", report)
    lines = [f"# Отчёт QA {stamp}", "", f"Ошибок (блокирующих): **{errors}**.", "",
             f"Карантин v1.0: нарушений {len(guard)}.", ""]
    for game, g in report["games"].items():
        lines += [f"## {game}", "", f"- Матрица: записей {g['matrix']['records']}, структурных ошибок: {g['matrix']['errors'] or 'нет'}.",
                  f"- Проверено строк: {g.get('records_checked', 0)}, с переводом: {g.get('translated', 0)}.",
                  f"- Статусы: {g.get('statuses', {})}.",
                  f"- Найдено: {g.get('severities', {}) or 'ничего'}."]
        for code, n in sorted(g.get("codes", {}).items(), key=lambda x: -x[1]):
            ex = g["examples"][code][0]
            lines.append(f"  - `{code}`: {n} (например, `{ex['key']}`: {ex['message']})")
        if "build" in g:
            b = g["build"]
            lines.append(f"- Сборка в копию бандла: ID {b['stats']['ids']}, переведено {b['stats']['translated']}, "
                         f"английский запасной {b['stats']['fallback_en']}, устаревших {b['stats']['stale']}, "
                         f"слотов {b['slots_written']}, ошибок {len(b['errors'])}.")
        lines.append("")
    write_text(REPORTS / "qa" / f"qa_{stamp}.md", "\n".join(lines))
    return errors, REPORTS / "qa" / f"qa_{stamp}.md"


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--game", choices=sorted(GAMES))
    parser.add_argument("--batch")
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return 1 if selftest() else 0
    games = [args.game] if args.game else list(GAMES)
    if args.batch:
        games = [args.batch.split(":")[0]]
    errors, path = run(games, args.batch, args.build)
    print(f"ошибок: {errors}; отчёт: {path}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

