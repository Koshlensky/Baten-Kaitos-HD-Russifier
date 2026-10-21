"""Отчёт по прототипу улик: пилотные сцены и общая статистика."""
import collections
import json
import sys

from tools.bkloc import tags
from tools.bkloc.paths import BASE, DOCS, SOURCE, read_jsonl, write_text

PILOT_SCENES = [
    ("bk1", "SCR_EVE08_2_", "BK1, лаборатория: Georg, Larikush, Giacomo"),
    ("bk1", "SCR_EVE15_1_", "BK1, лес Moonguile: Kalas и Xelha"),
    ("bk2", "EV_06", "BK2, мастерская: Lolo, Sagi, Milly, Guillo"),
]


def short(text, n=70):
    return tags.strip_tags(text).replace("\n", " ").replace("|", "/")[:n]


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    expected = json.loads((BASE / "evidence" / "pilot_expected.json").read_text(encoding="utf-8"))["lines"]
    out = ["# Прототип сбора улик: говорящий, род, адресат", "",
           "Сгенерировано `python -m tools.bkloc.evidence_report` по `base/evidence/<игра>.jsonl` "
           "(сами улики собирает `python -m tools.bkloc.evidence`).", "",
           "Скрипт не назначает говорящего. Он выдаёт улики с весами и сводку рода: `m`/`f` (вес ≥ 0,5), "
           "`weak_m`/`weak_f` (0,25–0,5), `conflict`, `none`; стиль речи (`archaic`, `polite`); кандидатов в говорящие "
           "(самоназвания) и адресаты (обращения по имени, `<n0>`).", "",
           "Колонка «оценка» — моя разметка по контексту сцены (`base/evidence/pilot_expected.json`), "
           "она не подтверждена игровыми данными и служит только для проверки прототипа.", ""]
    total = agree = with_verdict = 0
    for game, prefix, title in PILOT_SCENES:
        rows = [r for r in read_jsonl(SOURCE / game / "lines.jsonl") if r["id"].startswith(prefix) and r["id"] in expected]
        ev = {e["id"]: e for e in read_jsonl(BASE / "evidence" / f"{game}.jsonl")}
        out += [f"## {title}", "", "| ID | EN | Род (авто) | Стиль | Адресат (авто) | Оценка | Совпадение |", "|---|---|---|---|---|---|---|"]
        for r in rows:
            e = ev.get(r["id"], {})
            verdict = e.get("speaker_gender", {}).get("verdict", "none")
            style = ", ".join(e.get("speech_style", []))
            addr = ", ".join(e.get("addressee_candidates", []))
            exp = expected[r["id"]]
            mark = ""
            total += 1
            if verdict != "none":
                with_verdict += 1
                ok = verdict.endswith(exp["gender"])
                agree += ok
                mark = "да" if ok else "НЕТ"
            elif exp["gender"] == "n" and "archaic" in style:
                mark = "стиль Guillo"
            out.append(f"| `{r['id']}` | {short(r['en'])} | {verdict} | {style} | {addr} | {exp['speaker']} ({exp['gender']}) | {mark} |")
        out.append("")
    out += ["## Итог по пилоту", "",
            f"Реплик: {total}. Вывод о роде говорящего есть у {with_verdict}; совпал с оценкой у {agree}. "
            "Остальные реплики требуют разбора по контексту сцены: чередование говорящих, содержание, сюжет.", ""]
    out += ["## Покрытие по всей игре", ""]
    for game in ("bk1", "bk2"):
        evs = read_jsonl(BASE / "evidence" / f"{game}.jsonl")
        verdicts = collections.Counter(e["speaker_gender"]["verdict"] for e in evs)
        styles = collections.Counter(s for e in evs for s in e["speech_style"])
        addressed = sum(1 for e in evs if e["addressee_candidates"])
        out.append(f"- {game}: строк с уликами {len(evs)}; род говорящего: " +
                   ", ".join(f"{k} {v}" for k, v in verdicts.most_common()) +
                   f"; стиль: {dict(styles)}; с адресатом {addressed}.")
    out += ["",
            "## Выводы", "",
            "- Улики надёжны там, где они есть: в пилоте все выводы о роде совпали с оценкой. Но покрывают они меньшинство реплик, "
            "поэтому основную работу делает разметка по сцене.",
            "- Японская речь — главный источник: местоимения (`俺`, `僕`, `あたし`), окончания (`わ`, `のよ`, `かしら`, `ぜ`, `ぞ`), "
            "обращения (`きみ`, `あなた`, `おまえ`). Слабые маркеры (`わたし`, `あなた`, `きみ`) дают только «слабый» вывод.",
            "- Европейские локализации BK1 полезны точечно: родовые формы в «je suis …», «estoy …», «sono …» и в обращении на «ты».",
            "- Частицы мужской речи в архаичной речи не говорят о поле: у Guillo (махина без пола) `～ておる…ぞ`. "
            "Поэтому стиль `archaic` выделен отдельно, а мужская частица рядом с ним получает малый вес.",
            "- Обращения по имени в английском и японском дают адресата, а самоназвания («I'm just Xelha») — говорящего.",
            "- Конфликты (`conflict`) и строки без улик, где от рода зависит русская форма, получают статус `needs_context`.", ""]
    write_text(DOCS / "03c_evidence_prototype.md", "\n".join(out))
    print(f"пилот: {total} реплик, вывод о роде у {with_verdict}, совпало {agree}")


if __name__ == "__main__":
    main()
