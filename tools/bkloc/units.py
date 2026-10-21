"""Рабочие единицы: несколько подряд идущих пакетов в одном сжатом входе (D-036).

Экономия токенов относительно `pipeline.build_packet`:
  * справочники (правила, карточки, глоссарий, память) передаются один раз на единицу, а не на каждый пакет;
  * карточки — только нужные поля и только связи между присутствующими персонажами;
  * глоссарий — в, ru, статус, рекомендация; без форм, источников и обоснований;
  * у строки опускаются неизвестные поля (отсутствие поля = «неизвестно»), JSON без отступов;
  * сюжетная память ограничена и зависит от места пакета в очереди (без поздних событий и без памяти для
    служебных наборов и мест без сюжетной привязки — D-030).
Независимость редактора сохраняется: заметки переводчика ему не передаются, вывод идёт в отдельную сессию.
"""
import json
import re

from tools.bkloc import pipeline, tags
from tools.bkloc.paths import BASE, GAMES, ROOT, RUNS, SCENES, read_jsonl, write_text

MEMORY_CHARS = 3000
PREV_MAX = 8
READY_MAX = 20
GENERIC_CTX = "Окно без таблички имени (описание, мысль или безымянный говорящий)."
LEGEND = (
    "Поля строки: `key` — ключ; `en` — оригинал (⟦…⟧ — теги и подстановки игры); `sp` — говорящий (id карточки или табличка "
    "имени; нет поля — безымянный или неизвестный); `sg`/`ag` — род говорящего/адресата (m, f, n, mixed); `ad` — адресат; "
    "`rg` — обращение (ty — «ты», vy — «вы», mixed); `form` — вид строки, если это не обычная реплика (choice — пункт меню, "
    "label, system); `ctx` — контекст; `tag` — табличка имени из оригинала. Отсутствующее поле означает «неизвестно»: род в "
    "таком случае не выражай, обращение бери из контекста и соседних строк.")
MEMORY_REQUEST = (
    "\n## Память для следующих пакетов\n\n"
    "В КОНЕЦ массива добавь ещё один объект `{\"key\":\"__memory__\",\"ru\":\"…\",\"status\":\"edited\",\"note\":\"\"}`: в `ru` — до 600 символов "
    "о том, что произошло в этих сценах (события, отношения героев, закреплённые обращения «ты/вы», новые термины). "
    "Без пересказа мелочей; без форм рода, если пол не подтверждён. Этот объект не переводится и в игру не попадает.\n")


def dumps(x):
    return json.dumps(x, ensure_ascii=False, separators=(",", ":"))


def queue_info(game):
    path = SCENES / f"{game}_queue.json"
    if not path.exists():
        return {}
    return {e["batch_id"]: e for e in json.loads(path.read_text(encoding="utf-8"))["entries"]}


def slim_line(role, r, refs, game):
    d = {"key": r["key"], "en": tags.mask(r["en"])}
    if r["form"] != "line":
        d["form"] = r["form"]
    if r.get("variant"):
        d["variant"] = r["variant"]
    if r["speaker"]["id"] != "unknown":
        d["sp"] = r["speaker"]["id"]
    if r["speaker_gender"] != "unknown":
        d["sg"] = r["speaker_gender"]
    if r["addressee"]["id"] != "unknown":
        d["ad"] = r["addressee"]["id"]
    if r["addressee_gender"] != "unknown":
        d["ag"] = r["addressee_gender"]
    if r["register"] != "unknown":
        d["rg"] = r["register"]
    if r["context"] and r["context"] != GENERIC_CTX:
        d["ctx"] = r["context"]
    if r["speaker"].get("name_tag"):
        d["tag"] = r["speaker"]["name_tag"]["en"]
    if "needs_reference" in r.get("qa_flags", []):
        row = refs[r["text_id"]]
        d["jp"] = tags.mask(row.get("jp", ""))
        if game == "bk1":
            d["fr"] = tags.mask(row.get("fr", ""))
    if role == "editor":
        d["ru"] = tags.mask(r["ru"])
        from tools.qa import checks
        hints = [m for _s, code, m in checks.run_line_checks(r) if code in checks.EDITOR_HINTS]
        # D-036: род без опоры — обязательная подсказка (проверка Opus нашла такие ошибки в 4 из 50 строк)
        from tools.qa import gender_lint
        hints += [f"РОД ({code}): {m} — перепиши без форм рода или оставь, только если английский подтверждает пол"
                  for _s, code, m in gender_lint.lint(r) if code in ("gender_unverified", "gender_neutral_expected")]
        if hints:
            d["hints"] = hints
    return d


def slim_cards(records):
    cards = pipeline.characters_subset(records)
    present = {c["id"] for c in cards}
    out = []
    for c in cards:
        d = {"id": c["id"], "ru": (c.get("ru_name") or {}).get("value"), "g": c.get("grammatical_gender"),
             "form": c.get("formality")}
        style = c.get("speech_style") or {}
        s = " ".join((style.get("en") or []))[:220]
        if s:
            d["style"] = s
        rel = []
        for x in c.get("relationships") or []:
            if x.get("with") in present and x.get("register_to") not in (None, "unknown"):
                item = f"{x['with']}:{x['register_to']}"
                if x.get("register_status") == "approved":
                    item += "!"
                rel.append(item)
        if rel:
            d["to"] = rel
        notes = c.get("translation_notes") or []
        if notes:
            d["notes"] = [str(n)[:200] for n in notes][:3]
        out.append({k: v for k, v in d.items() if v not in (None, "", [])})
    return out


def slim_glossary(records):
    out = []
    for e in pipeline.glossary_subset(records):
        d = {"en": e["en"], "ru": e.get("ru") or e.get("ru_proposed"), "st": e.get("status")}
        if e.get("dispute_recommendation"):
            d["rec"] = e["dispute_recommendation"][:160]
        if e.get("notes"):
            d["note"] = str(e["notes"])[:140]
        out.append({k: v for k, v in d.items() if v not in (None, "")})
    return out


def story_memory(game, first_batch):
    """Сюжетная память для единицы: BK1 — хвост общей памяти; BK2 — разделы событий до места пакета в очереди."""
    info = queue_info(game).get(first_batch, {})
    if info and not info.get("story_memory", True):
        return ""
    path = BASE / "memory" / f"story_so_far_{game}.md"
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8")
    if game != "bk2":
        return text[-MEMORY_CHARS:]
    limit = info.get("chapter")
    if limit is None:
        return ""
    own = info.get("block") == "event"
    parts = re.split(r"(?m)^## (?=EV )", text)
    kept = []
    for part in parts[1:]:
        m = re.match(r"EV (\d+)(?:–(\d+))?", part)
        if not m:
            continue
        end = int(m.group(2) or m.group(1))
        if end < limit if own else end <= limit:
            kept.append("## " + part.strip())
    return "\n\n".join(kept)[-MEMORY_CHARS:]


def append_memory(game, batch_ids, text):
    """Добавить блок памяти единицы (только события BK2 и любые единицы BK1)."""
    info = queue_info(game)
    path = BASE / "memory" / f"story_so_far_{game}.md"
    old = path.read_text(encoding="utf-8") if path.exists() else f"# Память сюжета {game}\n"
    if game == "bk2":
        evs = [info[b]["chapter"] for b in batch_ids if info.get(b, {}).get("block") == "event"]
        if not evs:
            return False
        head = f"## EV {min(evs)}–{max(evs)} ({', '.join(batch_ids)})" if min(evs) != max(evs) else f"## EV {evs[0]} ({batch_ids[0]})"
    else:
        head = f"## {', '.join(batch_ids)}"
    if f"\n{head}\n" in old + "\n":  # повторный приём той же единицы не дублирует блок
        return True
    write_text(path, old.rstrip() + f"\n\n{head}\n{text.strip()}\n")
    return True


def build_unit(unit_id, batch_ids, role, run_dir, only_keys=None):
    """only_keys — точечная правка уже отредактированных строк (D-036): строки берутся по ключам независимо от статуса."""
    game = batch_ids[0].split(":")[0]
    assert all(b.split(":")[0] == game for b in batch_ids), "единица не смешивает игры"
    refs = pipeline.references(game)
    all_todo, ready, blocks, prev, scenes = [], [], [], [], []
    for bid in batch_ids:
        _g, records, meta, pv = pipeline.work_records(bid, role)
        if only_keys is not None:
            keys = set(only_keys)
            todo = [r for r in records if r["key"] in keys]
            done = [r for r in records if r["key"] not in keys and r.get("ru")]
        else:
            todo, done = pipeline.role_records(records, role)
        if not todo:
            continue
        if not prev:
            prev = pv
        all_todo += todo
        ready += done
        scenes += [s for s in meta["scene_ids"] if s not in scenes]
        blocks.append((bid, len(todo), meta["scene_ids"]))
    ready = sorted({r["key"]: r for r in ready}.values(), key=lambda r: r["global_order"])
    first_order = min(r["global_order"] for r in all_todo)
    ready = [r for r in ready if r["global_order"] < first_order][-READY_MAX:] + \
            [r for r in ready if r["global_order"] >= first_order][:READY_MAX]
    template = (ROOT / "prompts" / ("translator_sonnet.md" if role == "translator" else "editor.md")).read_text(encoding="utf-8")
    lines = [slim_line(role, r, refs, game) for r in all_todo]
    mem = story_memory(game, batch_ids[0])
    parts = [template.strip(), "",
             f"## Единица {unit_id}", "",
             f"Игра: {GAMES[game]['title']}. Пакеты: " + "; ".join(f"{b} ({n} строк; сцены {', '.join(s[:4])}{' …' if len(s) > 4 else ''})"
                                                                for b, n, s in blocks) + f". Строк всего: {len(lines)}.", "",
             "## Обозначения", "", LEGEND, "",
             "## Персонажи", "", "```json", dumps(slim_cards(all_todo)), "```", "",
             "## Глоссарий (термины, найденные в строках)", "", "```json", dumps(slim_glossary(all_todo)), "```", "",
             "## Что было раньше", "", mem or "(сюжетной памяти для этого места нет)", "",
             "## Память сцен", "", pipeline.scene_memories(scenes) or "(нет)", ""]
    if role != "editor":
        outline = []
        for bid, _n, sc in blocks:
            outline += pipeline.outline_for(game, {"scene_ids": sc})
        parts += ["## Официальная сводка (ориентир)", "", "\n".join(f"- {o}" for o in dict.fromkeys(outline)) or "(нет)", ""]
    tail = [{"sp": r["speaker"]["id"], "en": tags.mask(r["en"]), "ru": tags.mask(r["ru"])} for r in prev[-PREV_MAX:] if r.get("ru")]
    rdy = [{"sp": r["speaker"]["id"], "en": tags.mask(r["en"]), "ru": tags.mask(r["ru"])} for r in ready]
    parts += ["## Предыдущие и уже готовые строки (контекст; не переводить и не править)", "",
              "```json", dumps({"prev": tail, "ready": rdy}), "```", ""]
    if role == "editor" and only_keys is None:
        parts += [MEMORY_REQUEST]
    parts += ["## Строки", "", "```json", dumps(lines), "```", ""]
    path = run_dir / f"unit_{unit_id}_{role}_input.md"
    write_text(path, "\n".join(parts))
    keys = {r["key"]: {"batch": r["batch_id"], "en": r["en"]} for r in all_todo}
    (run_dir / f"unit_{unit_id}_{role}_map.json").write_text(json.dumps({"unit": unit_id, "batches": [b[0] for b in blocks],
                                                                         "keys": keys}, ensure_ascii=False), encoding="utf-8")
    return path, len(lines)
