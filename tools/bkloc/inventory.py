"""Инвентарь текстов: категории, объёмы, теги, переносы, схлопнутые строки.

Пишет source/<game>/inventory.json и docs/02_text_inventory.md.
"""
import collections
import json
import re
import sys

from tools.bkloc import categories, tags
from tools.bkloc.paths import DOCS, GAMES, SOURCE, read_jsonl, write_json, write_text

WORD = re.compile(r"[A-Za-z0-9']+")


def words(text):
    return len(WORD.findall(tags.strip_tags(text)))


def analyze(game):
    rows = read_jsonl(SOURCE / game / "lines.jsonl")
    by_cat = collections.Counter()
    by_form = collections.Counter()
    words_ids = collections.Counter()
    unique_seen = collections.defaultdict(set)
    words_unique = collections.Counter()
    groups = collections.defaultdict(lambda: collections.Counter())
    tag_counts = collections.Counter()
    tag_examples = {}
    flags = collections.Counter()
    for row in rows:
        en, jp = row["en"], row.get("jp")
        cat, form = categories.classify(row["id"], en, jp)
        row["_cat"], row["_form"] = cat, form
        by_cat[cat] += 1
        by_form[(cat, form)] += 1
        w = words(en)
        words_ids[cat] += w
        if en not in unique_seen[cat]:
            unique_seen[cat].add(en)
            words_unique[cat] += w
        groups[categories.group_of(row["id"])][cat] += 1
        for flag in categories.flags(en):
            flags[flag] += 1
        for token in tags.tokens(en):
            s = tags.shape(token)
            tag_counts[s] += 1
            tag_examples.setdefault(s, (row["id"], en[:120]))

    # Переносы строк в репликах
    dialogue = [r for r in rows if r["_cat"] == "dialogue" and r["_form"] == "line"]
    bubbles_en = [b for r in dialogue for b in tags.bubbles(r["en"])]
    with_nl = sum(1 for b in bubbles_en if "\n" in b)
    long_lines = [len(tags.strip_tags(seg)) for b in bubbles_en for seg in b.split("\n")]
    long_lines.sort()
    bubbles_jp = [b for r in dialogue for b in tags.bubbles(r.get("jp", ""))]
    jp_nl = sum(1 for b in bubbles_jp if "\n" in b)

    # Маскировка тегов обратима; теги озвучки EN и JP сравниваются по каждому ID
    mask_failures = [r["id"] for r in rows if tags.unmask(tags.mask(r["en"])) != r["en"]]
    v_re = re.compile(r"<v-?\d*>")
    voiced = [r for r in rows if v_re.search(r["en"]) or v_re.search(r.get("jp", ""))]
    v_differs = sum(1 for r in voiced if v_re.findall(r["en"]) != v_re.findall(r.get("jp", "")))

    # Схлопнутые строки: одна EN-запись на несколько ID с разным JP
    by_entry = collections.defaultdict(list)
    for r in rows:
        by_entry[r["en_entry"]].append(r)
    shared = [v for v in by_entry.values() if len(v) > 1]
    shared_dialogue = [v for v in shared if v[0]["_cat"] == "dialogue"]
    divergent = [v for v in shared_dialogue if len({x.get("jp", "") for x in v}) > 1]
    divergent_examples = [
        {"en": v[0]["en"][:80], "ids": [x["id"] for x in v][:4], "jp": sorted({x.get("jp", "")[:40] for x in v})[:4]}
        for v in sorted(divergent, key=lambda v: -len(v))[:12]
    ]

    # Схема ID
    id_scheme = {}
    if game == "bk1":
        scr = [r["id"] for r in rows if r["id"].startswith("SCR_")]
        scripts = collections.Counter(i.split("_")[1] for i in scr)
        events = collections.Counter("_".join(i.split("_")[1:3]) for i in scr)
        variants = sum(1 for i in scr if len(i.split("_")) > 4)
        families = collections.Counter(re.sub(r"\d.*$", "", s) for s in scripts)
        id_scheme = {"scr_ids": len(scr), "scripts": len(scripts), "events": len(events),
                     "ids_with_variant_suffix": variants, "script_families": dict(families.most_common())}
    else:
        ev = sorted(int(r["id"][3:]) for r in rows if re.fullmatch(r"EV_\d+", r["id"]))
        gaps = [(a, b) for a, b in zip(ev, ev[1:]) if b - a > 1]
        id_scheme = {"ev_ids": len(ev), "ev_first": ev[0], "ev_last": ev[-1], "ev_gaps": gaps[:20], "ev_gap_count": len(gaps)}

    return {
        "game": game,
        "ids": len(rows),
        "categories": {c: {"ids": by_cat[c], "unique_en": len(unique_seen[c]), "words_unique": words_unique[c],
                           "words_ids": words_ids[c]} for c in sorted(by_cat, key=lambda c: -by_cat[c])},
        "forms": {f"{c}/{f}": n for (c, f), n in sorted(by_form.items(), key=lambda x: -x[1])},
        "groups": {g: dict(v) for g, v in groups.items()},
        "flags": dict(flags),
        "tags": {s: {"count": n, "example_id": tag_examples[s][0], "example": tag_examples[s][1]}
                 for s, n in tag_counts.most_common()},
        "line_breaks": {
            "dialogue_bubbles_en": len(bubbles_en),
            "en_bubbles_with_newline": with_nl,
            "jp_bubbles_with_newline_share": round(jp_nl / max(1, len(bubbles_jp)), 3),
            "en_line_len_p50": long_lines[len(long_lines) // 2] if long_lines else 0,
            "en_line_len_p95": long_lines[int(len(long_lines) * 0.95)] if long_lines else 0,
            "en_line_len_max": long_lines[-1] if long_lines else 0,
            "en_lines_over_60_chars": sum(1 for n in long_lines if n > 60),
        },
        "shared_entries": {"en_entries_with_several_ids": len(shared), "dialogue": len(shared_dialogue),
                           "dialogue_with_different_jp": len(divergent), "examples": divergent_examples},
        "id_scheme": id_scheme,
        "mask_roundtrip_failures": mask_failures,
        "voice_tags": {"ids_with_voice_tags": len(voiced), "en_jp_voice_tags_differ": v_differs},
    }


CATEGORY_RU = {
    "dialogue": "Реплики, осмотр, меню выбора в сценах и на локациях",
    "magnus_name": "Названия магнусов, приёмов, предметов-карт",
    "magnus_desc": "Описания магнусов и комбо",
    "item_name": "Названия ключевых предметов",
    "item_desc": "Описания ключевых предметов",
    "npc_name": "Имена NPC",
    "party_name": "Имена членов отряда",
    "enemy_name": "Имена врагов",
    "location_name": "Названия локаций и точек сохранения",
    "story_summary": "Официальные сюжетные сводки и дневник",
    "movie_subtitle": "Субтитры заставок",
    "battle_voice": "Боевые реплики персонажей",
    "ui": "Интерфейс и системные сообщения",
    "ui_battle": "Боевой интерфейс",
    "help": "Справка",
    "trophy": "Достижения",
    "music_title": "Названия музыкальных композиций",
    "encyclopedia": "Энциклопедия (BK2)",
    "letter": "Письма (BK2)",
    "quest_log": "Журнал поручений",
    "credits": "Титры",
    "minigame": "Мини-игра (комбинации карт)",
    "internal": "Служебные строки разработчиков",
}

FORM_RU = {
    "line": "реплика или описание",
    "choice": "меню выбора",
    "system": "системное сообщение с подстановкой",
    "label": "короткая подпись или название",
    "internal": "служебная пометка (FLAG_, debug, «разговор с продавцом»)",
    "same_as_jp": "совпадает с японским (коды, латиница)",
    "empty": "пустая строка",
    "fixed": "по группе",
}


def render(results):
    out = ["# Инвентарь текстов Baten Kaitos I & II HD", "",
           "Документ сгенерирован `python -m tools.bkloc.inventory` по `source/<игра>/lines.jsonl`. "
           "Цифры — по английскому тексту; слова считаются без тегов.", ""]
    out += ["## Где лежит текст", "",
            "- BK1: `Batenkaitos1\\Batenkaitos_Data\\StreamingAssets\\aa\\StandaloneWindows64\\6a872618f189b35a4039d59de4a885c3.bundle`.",
            "- BK2: `Batenkaitos2\\Batenkaitos_Data\\StreamingAssets\\aa\\StandaloneWindows64\\a8cc32bba98258300a54327273719c75.bundle`.",
            "- Внутри 16 ассетов `TextAsset`: `TextIDList` (`{\"textId\": [...]}`) и `TextData_<язык>` "
            "(`{\"array\": [{\"i\": [индексы ID], \"t\": \"текст\"}]}`, UTF-8 с BOM).",
            "- Полные языки: EN, JP, KR, SC, TC в обеих играх; FR, DE, ES, IT — только в BK1. "
            "Слоты RU, AR, BR, NS, PL, TH (и FR/DE/ES/IT в BK2) — заглушки «(empty)».",
            "- Повторная сериализация `json.dumps(obj, ensure_ascii=False, separators=(',', ':'))` даёт "
            "байт-в-байт исходный текст для всех 32 ассетов — извлечение без потерь.", ""]
    for res in results:
        game = res["game"]
        out += [f"## {GAMES[game]['title']} ({game})", "", f"Всего ID: **{res['ids']}**.", "",
                "| Категория | ID | Уникальных EN | Слов (уникальные) |", "|---|---:|---:|---:|"]
        for cat, info in res["categories"].items():
            out.append(f"| {CATEGORY_RU.get(cat, cat)} (`{cat}`) | {info['ids']} | {info['unique_en']} | {info['words_unique']} |")
        total_words = sum(i["words_unique"] for i in res["categories"].values())
        out += ["", f"Итого слов по уникальным строкам: **{total_words}**.", "",
                "Подтипы диалоговых строк:", ""]
        for key, n in res["forms"].items():
            cat, form = key.split("/")
            if cat == "dialogue":
                out.append(f"- {FORM_RU.get(form, form)} (`{form}`): {n}")
        fl = res["flags"]
        out += ["", f"Озвученные строки (тег `<v...>`): {fl.get('voiced', 0)}; "
                f"упоминают духа-хранителя (`<n0>`): {fl.get('mentions_spirit', 0)}; "
                f"подозрение на отладочные пункты (`Lv. N` без `<w>`): {fl.get('internal_suspect', 0)}.", ""]
        sch = res["id_scheme"]
        if game == "bk1":
            fam = ", ".join(f"{k} {v}" for k, v in list(sch["script_families"].items())[:30])
            out += ["Схема ID сцен: `SCR_{скрипт}_{событие}_{смещение}[_{вариант}]`. "
                    f"Строк SCR: {sch['scr_ids']}, скриптов: {sch['scripts']}, событий: {sch['events']}, "
                    f"с суффиксом варианта: {sch['ids_with_variant_suffix']}.", "",
                    f"Семейства скриптов (код локации → число скриптов): {fam}.", ""]
        else:
            out += [f"Схема ID сюжета: `EV_0000…EV_{sch['ev_last']:04d}`, строк {sch['ev_ids']}, "
                    f"пропусков в нумерации: {sch['ev_gap_count']} (первые: {sch['ev_gaps'][:8]}). "
                    "Полевые реплики сгруппированы по локациям (`MINTAKA_###`, `SHERATAN_###` и т.д.).", ""]
        lb = res["line_breaks"]
        out += [f"Переносы строк в репликах: облачков {lb['dialogue_bubbles_en']}, с явным `\\n` в EN — "
                f"{lb['en_bubbles_with_newline']}; в JP доля облачков с `\\n` — {lb['jp_bubbles_with_newline_share']}. "
                f"Длина строки EN между переносами: медиана {lb['en_line_len_p50']}, 95-й перцентиль {lb['en_line_len_p95']}, "
                f"максимум {lb['en_line_len_max']}; строк длиннее 60 символов — {lb['en_lines_over_60_chars']}. "
                "Длинные строки без переносов означают, что английская версия полагается на автоперенос окна; "
                "проверяется в игре на пилоте.", ""]
        sh = res["shared_entries"]
        out += [f"Схлопнутые строки: EN-записей с несколькими ID — {sh['en_entries_with_several_ids']}, из них в диалогах — "
                f"{sh['dialogue']}; у {sh['dialogue_with_different_jp']} из них японские реплики разные. "
                "Это кандидаты на разный перевод (род, обращение, интонация) в зависимости от говорящего.", ""]
        for ex in sh["examples"][:6]:
            en_ex = ex["en"].replace("\n", "\\n")
            jp_ex = " / ".join(j.replace("\n", "\\n") for j in ex["jp"])
            out.append(f"- «{en_ex}» — {', '.join(ex['ids'])}; JP: {jp_ex}")
        vt = res["voice_tags"]
        conclusion = ("Поэтому в перевод теги копируются только из английской строки."
                      if vt["en_jp_voice_tags_differ"] else "Здесь они совпадают, но правило то же: теги берутся из английской строки.")
        out += ["", f"Теги озвучки: у {vt['ids_with_voice_tags']} ID есть `<v…>` в EN или JP; у {vt['en_jp_voice_tags_differ']} "
                f"из них набор тегов `<v…>` в EN и JP различается. {conclusion}",
                f"Маскировка тегов для модели обратима для всех строк: ошибок {len(res['mask_roundtrip_failures'])}.", ""]
    out += ["## Управляющие теги", "",
            "Статусы: «подтверждено» — следует из структуры однозначно; «вероятно» — согласуется со всеми примерами; "
            "«гипотеза» — назначение не установлено. Все теги переносятся в перевод без изменений.", "",
            "| Тег | BK1 | BK2 | Назначение | Статус | Пример |", "|---|---:|---:|---|---|---|"]
    shapes = []
    for res in results:
        for s in res["tags"]:
            if s not in shapes:
                shapes.append(s)
    status_ru = {"confirmed": "подтверждено", "likely": "вероятно", "hypothesis": "гипотеза"}
    for s in shapes:
        info, status = tags.TAG_INFO.get(s, ("не описан", "hypothesis"))
        counts = [str(res["tags"].get(s, {}).get("count", 0)) for res in results]
        example = next((res["tags"][s] for res in results if s in res["tags"]), None)
        ex = f"`{example['example_id']}`: {example['example'][:60]}" if example else ""
        ex = ex.replace("|", "\\|").replace("\n", "\\n")
        out.append(f"| `{s}` | {counts[0]} | {counts[1]} | {info} | {status_ru[status]} | {ex} |")
    out += ["", "Правила для перевода и проверок — в [08_qa_system.md](08_qa_system.md): структурные теги "
            "(`<w…>`, `<p>`, `<v…>`, `<aN>`, `<q>`, `<Q>`, `<sN>`, `<nN>`, `%s`, `%d`) сохраняются в том же количестве и порядке; "
            "парные `<cN>…<c>` и `<BN>…</B>` должны оставаться парными; текст внутри `<cN>…<c>` переводится.", ""]
    return "\n".join(out)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    results = []
    for game in GAMES:
        res = analyze(game)
        write_json(SOURCE / game / "inventory.json", res)
        results.append(res)
        print(game, {c: i["ids"] for c, i in res["categories"].items()})
    write_text(DOCS / "02_text_inventory.md", render(results))


if __name__ == "__main__":
    main()
