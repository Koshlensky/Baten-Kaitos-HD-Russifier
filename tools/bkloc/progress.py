"""Сводка прогресса перевода: reports/progress.md (для продолжения работы в новой сессии).

    python -m tools.bkloc.progress
"""
import collections
import datetime
import json
import sys

from tools.bkloc import categories, pipeline
from tools.bkloc.paths import BASE, GAMES, REPORTS, SCENES, write_text
from tools.qa import checks, fit, gender_lint

OPEN_QUESTIONS = BASE / "progress" / "open_questions.md"


def qa_counts(records):
    widths = collections.defaultdict(float)
    for r in records:
        r["_group"] = categories.group_of(r["text_id"])
        if r["category"] in fit.FIT_CATEGORIES:
            widths[r["_group"]] = max(widths[r["_group"]], fit.width(r["en"]) or 0)
    counts = collections.Counter()
    for r in records:
        if r.get("ru"):
            issues = checks.run_line_checks(r)
            issues += checks.waive(r, gender_lint.lint(r) + fit.check_fit(r, widths) + fit.check_glyphs(r))
            counts.update(severity for severity, _code, _msg in issues)
    return counts


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    state = pipeline.load_state()
    lines = [f"# Прогресс перевода — {datetime.datetime.now():%Y-%m-%d %H:%M}", "",
             "Собирается командой `python -m tools.bkloc.progress`. Продолжение работы: `pipeline next --game <игра>`.", ""]
    for game in GAMES:
        queued = pipeline.queue_order(game)
        order = [b for b, _info in queued]
        info = {b["batch_id"]: i for b, i in queued}
        entries = state["games"][game]
        done = [b["batch_id"] for b in order if entries[b["batch_id"]]["status"] in ("edited", "approved")]
        nxt = next((b for b in order if entries[b["batch_id"]]["status"] not in ("edited", "approved")), None)
        current = info.get(nxt["batch_id"], {}) if nxt else {}
        records = pipeline.load_matrix(game)
        statuses = collections.Counter(r["status"] for r in records)
        translated = sum(1 for r in records if r.get("ru"))
        unknown = sum(1 for r in records if r["category"] == "dialogue" and r["speaker"]["status"] in ("unknown", "needs_context")
                      and r.get("ru"))
        qa = qa_counts([r for r in records if r.get("ru")])
        finished = [b for b in order if entries[b["batch_id"]]["status"] in ("edited", "approved")]
        last = max(finished, key=lambda b: entries[b["batch_id"]].get("updated") or "", default=None)
        lines += [f"## {GAMES[game]['title']} ({game})", "",
                  f"- Пакеты: завершено {len(done)} из {len(order)}"
                  + (f" (последний — {last['batch_id']}: {', '.join(last['scene_ids'][:3])})" if last else ""),
                  (f"- Следующий пакет: {nxt['batch_id']} — {', '.join(nxt['scene_ids'][:3])}, строк {nxt['count']}; "
                   f"блок очереди {current.get('block', '—')}, глава {current.get('chapter') or '—'} "
                   f"({current.get('order_confidence', 'порядок файла')})") if nxt else "- Все пакеты завершены",
                  f"- Строк: всего {len(records)}, с переводом {translated}, отредактировано {statuses.get('edited', 0) + statuses.get('approved', 0)}, "
                  f"выборочно проверено {statuses.get('sampled_review', 0)}, прошло QA {statuses.get('qa_passed', 0)}, "
                  f"черновиков {statuses.get('draft', 0) + statuses.get('translated', 0)}, "
                  f"needs_context {statuses.get('needs_context', 0)}, "
                  f"blocked_decision {statuses.get('blocked_decision', 0)}",
                  f"- Переведённые реплики без установленного говорящего: {unknown}",
                  f"- QA по переведённым строкам: ошибок {qa.get('error', 0)}, предупреждений {qa.get('warning', 0)}", ""]
    lines += ["## Нерешённые вопросы", ""]
    lines += [OPEN_QUESTIONS.read_text(encoding="utf-8").strip() if OPEN_QUESTIONS.exists() else "(нет)", ""]
    write_text(REPORTS / "progress.md", "\n".join(lines))
    print("\n".join(lines[:20]))


if __name__ == "__main__":
    main()
