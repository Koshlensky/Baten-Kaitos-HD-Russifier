"""Пайплайн перевода по пакетам: статус, следующий пакет, сборка входа, приём ответа.

Команды:
  status [--game bk1]                       прогресс по пакетам
  next --game bk1                           следующий пакет по порядку
  packet <batch|unit> --role ROLE           собрать вход для роли (annotator|translator|editor)
  apply <batch|unit> --role ROLE --file F   принять ответ модели (JSON) в матрицу
        [--model NAME] [--dry-run]
  scope                                     разрешённые границы работ (approvals.json)

Пакет (bk1:B0000) — единица разметки по сценам. Единица (bk1:P1_intro) — часть разрешённых
границ работ из base/progress/approvals.json: набор сцен или ID.

Допуск: роли translator и editor работают только со строками в границах approvals.json
(решения владельца, например D-027 — пилот). Пакет целиком вне границ — отказ; это защита
от начала массового перевода без разрешения. Разметка (annotator) разрешена везде.

Состояние: base/progress/state.json. Журнал: runs/<дата>/. Матрица: base/matrix/.
"""
import argparse
import datetime
import hashlib
import json
import re
import sys

import yaml

from tools.bkloc import tags
from tools.bkloc.matrix import load as load_matrix
from tools.bkloc.paths import BASE, GAMES, MATRIX, ROOT, RUNS, SCENES, SOURCE, read_jsonl, write_json, write_jsonl, write_text

STATE = BASE / "progress" / "state.json"
APPROVALS = BASE / "progress" / "approvals.json"
ROLES = ("annotator", "translator", "editor")
GATED_ROLES = ("translator", "editor")
BATCH_FLOW = ["pending", "annotated", "translated", "qa_passed", "sampled_review", "edited", "approved"]
ROLE_RESULT = {"annotator": "annotated", "translator": "translated", "editor": "edited"}
TAIL_LINES = 12
MEMORY_CHARS = 4000


# --- состояние -------------------------------------------------------------------

def init_state():
    state = {"version": 1, "games": {}}
    for game in GAMES:
        batches = json.loads((SCENES / f"{game}_batches.json").read_text(encoding="utf-8"))["batches"]
        state["games"][game] = {b["batch_id"]: {"status": "pending", "updated": None, "runs": []} for b in batches}
    return state


def load_state():
    if STATE.exists():
        state = json.loads(STATE.read_text(encoding="utf-8"))
        fresh = init_state()
        for game, batches in fresh["games"].items():
            for batch_id, entry in batches.items():
                state["games"].setdefault(game, {}).setdefault(batch_id, entry)
        return state
    return init_state()


def save_state(state):
    write_json(STATE, state)


# --- данные пакета -------------------------------------------------------------------

def batch_info(batch_id):
    game = batch_id.split(":")[0]
    batches = json.loads((SCENES / f"{game}_batches.json").read_text(encoding="utf-8"))["batches"]
    for i, b in enumerate(batches):
        if b["batch_id"] == batch_id:
            return game, b, batches[i - 1] if i else None
    raise SystemExit(f"Пакет не найден: {batch_id}")


def batch_records(game, batch):
    scene_ids = set(batch["scene_ids"])
    return [r for r in load_matrix(game) if r["scene_id"] in scene_ids]


# --- границы работ (approvals.json) ---------------------------------------------------

def load_approvals():
    if not APPROVALS.exists():
        return {"scopes": {}}
    return json.loads(APPROVALS.read_text(encoding="utf-8"))


def is_unit(item_id):
    return re.match(r"^bk[12]:P\d+_", item_id) is not None


def unit_info(unit_id):
    """(игра, имя границ, описание единицы, заблокированные ключи) для единицы из approvals.json."""
    for scope_name, scope in load_approvals().get("scopes", {}).items():
        unit = scope.get("units", {}).get(unit_id)
        if unit is not None:
            return unit_id.split(":")[0], scope_name, unit, scope.get("blocked", {})
    raise SystemExit(f"Единица не найдена в {APPROVALS.name}: {unit_id}")


def unit_records(game, unit, blocked):
    scenes, ids = set(unit.get("scenes", [])), set(unit.get("ids", []))
    records = [r for r in load_matrix(game) if (r["scene_id"] in scenes or r["text_id"] in ids)
               and r["key"] not in blocked]
    return records


def approved_keys(game, role):
    """Ключи строк, с которыми роли разрешено работать (по всем границам approvals.json).
    Граница с полем games разрешает игру целиком, кроме blocked и deferred_categories."""
    keys = set()
    for scope in load_approvals().get("scopes", {}).values():
        if role not in scope.get("roles", []):
            continue
        blocked = scope.get("blocked", {})
        if game in scope.get("games", []):
            deferred = set(scope.get("deferred_categories", {}))
            keys.update(r["key"] for r in load_matrix(game)
                        if r["key"] not in blocked and r["category"] not in deferred)
        for unit_id, unit in scope.get("units", {}).items():
            if unit_id.split(":")[0] == game:
                keys.update(r["key"] for r in unit_records(game, unit, blocked))
    return keys


def excluded_keys(game):
    """Строки, отложенные решениями владельца (blocked, deferred_categories): в пакеты не попадают."""
    keys = set()
    for scope in load_approvals().get("scopes", {}).values():
        keys.update(k for k in scope.get("blocked", {}) if k.startswith(game + ":"))
        if game in scope.get("games", []):
            deferred = set(scope.get("deferred_categories", {}))
            keys.update(r["key"] for r in load_matrix(game) if r["category"] in deferred)
    return keys


DRAFT_STATUSES = {"draft", "translated", "qa_passed", "sampled_review"}
ROLE_STATUSES = {"annotator": {"untranslated", "needs_context"} | DRAFT_STATUSES,
                 "translator": {"untranslated", "draft", "needs_context"},
                 "editor": {"needs_context"} | DRAFT_STATUSES}


APPLY_CORRECTIONS = {"annotator": {"edited"}, "editor": {"edited"}}


def role_records(records, role):
    """Строки, которые роль обрабатывает в пакете; готовые (например, пилот) остаются контекстом."""
    todo = [r for r in records if r["status"] in ROLE_STATUSES[role]]
    done = [r for r in records if r["status"] not in ROLE_STATUSES[role] and r.get("ru")]
    return todo, done


def require_scope(game, role, records, item_id):
    """Защита от перевода вне разрешённых границ: отказ, если строки не входят в approvals.json."""
    if role not in GATED_ROLES:
        return
    allowed = approved_keys(game, role)
    outside = [r["key"] for r in records if r["key"] not in allowed]
    if outside:
        raise SystemExit(
            f"{item_id}: {len(outside)} из {len(records)} строк вне разрешённых границ для роли {role} "
            f"({APPROVALS.relative_to(ROOT)}). Массовый перевод без решения владельца не начинается. "
            f"Примеры: {', '.join(outside[:5])}")


def work_records(item_id, role):
    """Строки для пакета или единицы, с проверкой допуска; (игра, строки, описание, предыдущие строки)."""
    if is_unit(item_id):
        game, scope_name, unit, blocked = unit_info(item_id)
        if role in GATED_ROLES and role not in load_approvals()["scopes"][scope_name].get("roles", []):
            raise SystemExit(f"{item_id}: роль {role} не разрешена в границах «{scope_name}»")
        records = unit_records(game, unit, blocked)
        scene_ids = list(dict.fromkeys(r["scene_id"] for r in records))
        everything = load_matrix(game)
        first = min((r["global_order"] for r in records), default=0)
        prev = [r for r in everything if r["global_order"] < first][-TAIL_LINES:] if unit.get("scenes") else []
        meta = {"id": item_id, "title": unit.get("title", ""), "scene_ids": scene_ids, "scope": scope_name}
        return game, records, meta, prev
    game, batch, prev_batch = batch_info(item_id)
    records = batch_records(game, batch)
    excluded = excluded_keys(game)
    records = [r for r in records if r["key"] not in excluded]
    require_scope(game, role, [r for r in records if r["status"] in {"untranslated", "needs_context"} | DRAFT_STATUSES],
                  item_id)
    meta = {"id": item_id, "title": "", "scene_ids": batch["scene_ids"], "scope": None}
    return game, records, meta, previous_tail(game, prev_batch)


def references(game):
    return {r["id"]: r for r in read_jsonl(SOURCE / game / "lines.jsonl")}


def glossary_subset(records):
    path = BASE / "glossary" / "glossary.yaml"
    entries = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("entries") or []
    text = " ".join(tags.strip_tags(r["en"]) for r in records)
    recommendations = dispute_recommendations()
    found = []
    for e in entries:
        if re.search(r"\b" + re.escape(e["en"]) + r"\b", text, re.I):
            anchor = (e.get("dispute") or "").partition("#")[2]
            if not e.get("ru") and anchor in recommendations:
                e = {**e, "dispute_recommendation": recommendations[anchor]}
            found.append(e)
    return found


def dispute_recommendations():
    """Рекомендации из disputes.md: без них переводчик спорного термина видит только ru: null."""
    path = BASE / "glossary" / "disputes.md"
    if not path.exists():
        return {}
    result, anchor = {}, None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            anchor = line[3:].strip()
        elif anchor and line.startswith("- Рекомендация:"):
            result[anchor] = line.partition(":")[2].strip()
    return result


def character_cards():
    cards = {}
    for path in (BASE / "characters").rglob("*.yaml"):
        if path.name.startswith("_"):
            continue
        card = yaml.safe_load(path.read_text(encoding="utf-8"))
        if card and card.get("id"):
            cards[card["id"]] = card
    return cards


def characters_subset(records):
    cards = character_cards()
    text = " ".join(tags.strip_tags(r["en"]) for r in records)
    wanted = set()
    for r in records:
        for side in ("speaker", "addressee"):
            sid = r[side]["id"]
            if sid not in ("unknown",):
                wanted.add("guardian_spirit" if sid == "GUARDIAN_SPIRIT" else sid)
            wanted.update(c.lower() for c in r[side].get("candidates", []))
        if "<n0>" in r["en"]:
            wanted.add("guardian_spirit")
    for cid, card in cards.items():
        if re.search(r"\b" + re.escape(card.get("en_name", "")) + r"\b", text):
            wanted.add(cid)
    return [cards[c] for c in sorted(wanted) if c in cards]


def init_memory():
    """Пустая память сюжета и официальная канва из текстов игры (английский оригинал)."""
    for game in GAMES:
        story = BASE / "memory" / f"story_so_far_{game}.md"
        if not story.exists():
            write_text(story, f"# Память сюжета {game}\n\nДополняется после каждого пакета по шаблону `prompts/summarizer.md`.\n")
        rows = read_jsonl(SOURCE / game / "lines.jsonl")
        if game == "bk1":
            items = [r for r in rows if r["id"].startswith("SaveStoryLine_")]
            body = [f"- `{r['id']}`: {tags.strip_tags(r['en'])}" for r in items]
            title = "Официальные сводки сохранений (SaveStoryLine), по порядку"
        else:
            names = [r for r in rows if r["id"].startswith("STORYOUTLINENAME_") and "<a" in r["en"]]
            diary = [r for r in rows if r["id"].startswith("STORYOUTLINE_") and not r["en"].startswith("<s0>")][::-1]
            body = ["### Названия глав", ""] + [f"- `{r['id']}`: {tags.strip_tags(r['en']).strip()}" for r in names]
            body += ["", "### Дневник (хронологически; автор записи — по первой персоне в японском)", ""]
            body += [f"- `{r['id']}`: {tags.strip_tags(r['en']).replace(chr(10), ' ')}" for r in diary]
            title = "Официальная канва: главы и дневник"
        write_text(BASE / "memory" / f"official_outline_{game}.md",
                   f"# {title} — {game}\n\nСгенерировано из английского оригинала; используется как ориентир, не для перевода.\n\n"
                   + "\n".join(body) + "\n")


def outline_for(game, batch):
    """Официальные сводки, сопоставленные со сценами пакета (автоматически, ориентир)."""
    scenes = json.loads((SCENES / f"{game}_scenes.json").read_text(encoding="utf-8"))["scenes"]
    wanted = set(batch["scene_ids"])
    ids = []
    for s in scenes:
        if s["scene_id"] in wanted and s.get("chapter_guess"):
            cid = s["chapter_guess"].get("id") or s["chapter_guess"].get("outline_id")
            if cid and cid not in ids:
                ids.append(cid)
    rows = {r["id"]: r for r in read_jsonl(SOURCE / game / "lines.jsonl")}
    return [f"`{i}`: {tags.strip_tags(rows[i]['en']).replace(chr(10), ' ')}" for i in ids[:2] if i in rows]


def scene_memory_path(scene_id):
    """Файл памяти сцены: base/memory/scenes/<scene_id>.md, ':' и '#' заменены на '_'."""
    return BASE / "memory" / "scenes" / (scene_id.replace(":", "_").replace("#", "_") + ".md")


def scene_memories(scene_ids):
    parts = []
    for sid in scene_ids:
        path = scene_memory_path(sid)
        if path.exists():
            parts.append(path.read_text(encoding="utf-8").strip())
    return "\n\n".join(parts)


def memory_tail(game):
    path = BASE / "memory" / f"story_so_far_{game}.md"
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8")
    return text[-MEMORY_CHARS:]


def previous_tail(game, prev_batch):
    if not prev_batch:
        return []
    return batch_records(game, prev_batch)[-TAIL_LINES:]


def card_genders():
    """id персонажа → грамматический род из карточки (m, f, n); дух-хранитель — n (D-002)."""
    genders = {"GUARDIAN_SPIRIT": "n"}
    for card in character_cards().values():
        if card.get("grammatical_gender") in ("m", "f", "n"):
            genders[card["id"]] = card["grammatical_gender"]
    return genders


def card_brief(card):
    keys = ("id", "en_name", "jp_name", "sex", "grammatical_gender", "pronouns_ru", "formality")
    brief = {k: card.get(k) for k in keys}
    brief["ru_name"] = (card.get("ru_name") or {}).get("value")
    brief["speech_style"] = card.get("speech_style")
    brief["relationships"] = card.get("relationships")
    brief["translation_notes"] = card.get("translation_notes")
    return brief


# --- сборка входа -------------------------------------------------------------------

def line_for(role, r, refs, game):
    row = refs[r["text_id"]]
    item = {"key": r["key"], "scene": r["scene_id"], "form": r["form"], "en": tags.mask(r["en"])}
    if r.get("variant"):
        item["variant"] = r["variant"]
    if role == "annotator":
        item["jp"] = tags.mask(row.get("jp", ""))
        if game == "bk1":
            for lang in ("fr", "es", "it", "de"):
                item[lang] = tags.mask(row.get(lang, ""))
        item["evidence"] = {"speaker_gender": r["speaker"].get("gender_evidence"), "style": r["speaker"].get("style"),
                            "speaker_candidates": r["speaker"].get("candidates"),
                            "addressee_candidates": r["addressee"].get("candidates"),
                            "script": r["speaker"].get("script")}
        if r["speaker"].get("name_tag"):
            item["evidence"]["name_tag"] = r["speaker"]["name_tag"]
        item["current"] = {"speaker": r["speaker"]["id"], "addressee": r["addressee"]["id"]}
    else:
        item.update({"speaker": r["speaker"]["id"], "speaker_status": r["speaker"]["status"],
                     "speaker_gender": r["speaker_gender"], "addressee": r["addressee"]["id"],
                     "addressee_gender": r["addressee_gender"], "register": r["register"], "context": r["context"]})
        if r["speaker"].get("name_tag"):
            item["speaker_tag_en"] = r["speaker"]["name_tag"]["en"]
        if "needs_reference" in r.get("qa_flags", []):
            item["jp_reference"] = tags.mask(row.get("jp", ""))
            if game == "bk1":
                item["fr_reference"] = tags.mask(row.get("fr", ""))
        if role == "editor":
            item["ru"] = tags.mask(r["ru"])
            # D-033: редактор независим от переводчика — заметки переводчика ему не передаются.
            from tools.qa import checks
            hints = [m for _s, code, m in checks.run_line_checks(r) if code in checks.EDITOR_HINTS]
            if hints:
                item["qa_hints"] = hints
    return item


def build_packet(item_id, role, prompt=None, only_keys=None):
    """only_keys — выборочная редактура: остальные строки пакета идут в «готовые» как контекст."""
    game, records, meta, prev = work_records(item_id, role)
    done = []
    if not is_unit(item_id):
        records, done = role_records(records, role)
    if only_keys is not None:
        done = sorted(done + [r for r in records if r["key"] not in only_keys and r.get("ru")],
                      key=lambda r: r["global_order"])
        records = [r for r in records if r["key"] in only_keys]
    refs = references(game)
    template = (ROOT / "prompts" / f"{prompt or role}.md").read_text(encoding="utf-8")
    lines = [line_for(role, r, refs, game) for r in records]
    ready = [{"key": r["key"], "en": tags.mask(r["en"]), "ru": tags.mask(r["ru"]), "speaker": r["speaker"]["id"],
              "addressee": r["addressee"]["id"], "register": r["register"]} for r in done]
    tail = [{"key": r["key"], "en": tags.mask(r["en"]), "ru": tags.mask(r["ru"]), "speaker": r["speaker"]["id"]}
            for r in prev]
    scene_ids = meta["scene_ids"]
    parts = [
        template.strip(), "",
        f"## Пакет {item_id}" + (f" — {meta['title']}" if meta["title"] else ""), "",
        f"Игра: {GAMES[game]['title']}. Сцены: {', '.join(scene_ids[:12])}"
        + (" …" if len(scene_ids) > 12 else "") + f". Строк: {len(records)}.", "",
        "## Персонажи", "", "```json", json.dumps([card_brief(c) for c in characters_subset(records)], ensure_ascii=False, indent=1), "```", "",
        "## Глоссарий (термины, найденные в строках)", "", "```json", json.dumps(glossary_subset(records), ensure_ascii=False, indent=1), "```", "",
        "## Что было раньше", "", memory_tail(game) or "(память сюжета пока пуста)", "",
        "## Память сцен пакета", "", scene_memories(scene_ids) or "(нет)", "",
    ]
    if role != "editor":
        parts += ["## Официальная сводка (сопоставлена автоматически, ориентир)", "",
                  "\n".join(f"- {o}" for o in outline_for(game, {"scene_ids": scene_ids})) or "(нет)", ""]
    parts += [
        "## Предыдущие строки (только для контекста, не переводить)", "", "```json", json.dumps(tail, ensure_ascii=False, indent=1), "```", "",
        "## Уже готовые строки этих сцен (только для контекста, не переводить и не править)", "",
        "```json", json.dumps(ready, ensure_ascii=False, indent=1), "```", "",
        "## Строки", "", "```json", json.dumps(lines, ensure_ascii=False, indent=1), "```", "",
    ]
    text = "\n".join(parts)
    stamp = datetime.datetime.now()
    suffix = "_sample" if only_keys is not None else ""
    path = RUNS / stamp.strftime("%Y%m%d") / f"{item_id.replace(':', '_')}_{role}{suffix}_input.md"
    write_text(path, text)
    return path, hashlib.sha1(template.encode("utf-8")).hexdigest(), len(records)


# --- приём ответа -------------------------------------------------------------------

def parse_output(path):
    text = path.read_text(encoding="utf-8-sig")
    match = re.search(r"```(?:json)?\s*(\[.*\])\s*```", text, re.S)
    payload = match.group(1) if match else text
    data = json.loads(payload)
    if not isinstance(data, list):
        raise SystemExit("Ответ должен быть JSON-массивом объектов")
    return data


def check_ru(en, ru):
    """Структурная проверка перевода. Возвращает (ошибки, предупреждения)."""
    errors, warnings = [], []
    if tags.MASK_OPEN in ru or tags.MASK_CLOSE in ru:
        errors.append("маркер тега не восстановлен")
    if tags.structural(en) != tags.structural(ru):
        errors.append("структурные теги не совпадают")
    if sorted(tags.placeholders(en)) != sorted(tags.placeholders(ru)):
        errors.append("подстановки не совпадают")
    if "\\n" in ru and "\\n" not in en:
        errors.append("литеральное \\n вместо перевода строки")
    for close, opener in tags.PAIRED.items():
        bal_ru = len([t for t in tags.tokens(ru) if opener.match(t)]) - tags.tokens(ru).count(close)
        bal_en = len([t for t in tags.tokens(en) if opener.match(t)]) - tags.tokens(en).count(close)
        if bal_ru != bal_en:  # непарность допустима, только если она есть в оригинале
            errors.append(f"непарный тег {close}")
    latin = re.findall(r"[A-Za-z]{3,}", tags.strip_tags(ru))
    if latin:
        warnings.append("латиница в переводе: " + ", ".join(latin[:3]))
    return errors, warnings


def apply_output(batch_id, role, file_path, model, dry_run):
    if is_unit(batch_id):
        game, work, _meta, _prev = work_records(batch_id, role)
    else:
        game, batch, _prev = batch_info(batch_id)
        excluded = excluded_keys(game)
        records_in_batch = [r for r in batch_records(game, batch) if r["key"] not in excluded]
        work = role_records(records_in_batch, role)[0]
        # Исправления уже отредактированных строк — только явным файлом правок, в пакеты они не попадают.
        work += [r for r in records_in_batch if r["status"] in APPLY_CORRECTIONS.get(role, ()) and r not in work]
        if role in GATED_ROLES:
            allowed = approved_keys(game, role)
            work = [r for r in work if r["key"] in allowed]
    work_keys = {r["key"] for r in work}
    items = parse_output(file_path)
    records = {r["key"]: r for r in load_matrix(game)}
    now = datetime.datetime.now().isoformat(timespec="seconds")
    run_ref = f"runs/{datetime.datetime.now():%Y%m%d}/{batch_id.replace(':', '_')}_{role}_{datetime.datetime.now():%H%M%S}.json"
    report = {"accepted": 0, "rejected": [], "warnings": []}
    for item in items:
        key = item.get("key")
        rec = records.get(key)
        if rec is None or key not in work_keys:
            report["rejected"].append({"key": key, "reason": "ключ не из этого пакета или вне разрешённых границ"})
            continue
        if role == "annotator":
            for side in ("speaker", "addressee"):
                if side in item:
                    value = item[side] if isinstance(item[side], dict) else {"id": item[side]}
                    rec[side].update({k: v for k, v in value.items() if k in ("id", "status", "basis")})
                    rec[side]["source"] = "annotator"
            for field in ("speaker_gender", "addressee_gender", "register"):
                if field in item:
                    rec[field] = item[field]
            if item.get("context"):
                rec["context_annotator"] = (rec.get("context_annotator", "") + " " + item["context"]).strip()
                rec["context"] = (rec["context"] + " " + item["context"]).strip()
            if item.get("needs_reference"):
                rec["qa_flags"] = sorted(set(rec["qa_flags"]) | {"needs_reference"})
            rec["history"].append({"ts": now, "actor": f"annotator/{model}", "action": "annotate", "run": run_ref})
            report["accepted"] += 1
            continue
        ru = tags.unmask(item.get("ru", ""))
        problems, notes = check_ru(rec["en"], ru) if ru else (["пустой перевод"], [])
        status = item.get("status") or ("translated" if role == "translator" else "edited")
        if role == "translator" and status == "draft":
            status = "translated"
        if problems and status not in ("needs_context", "blocked_decision"):
            report["rejected"].append({"key": key, "reason": "; ".join(problems)})
            continue
        if problems or notes:
            report["warnings"].append({"key": key, "reason": "; ".join(problems + notes)})
        entry = {"ts": now, "actor": f"{role}/{model}", "action": role, "run": run_ref}
        if rec["ru"] and rec["ru"] != ru:
            entry["ru_before"] = rec["ru"]
        rec["ru"] = ru
        rec["status"] = status
        if role == "translator":
            rec["translator_note"] = item.get("note", rec["translator_note"])
        else:
            rec["editor_note"] = item.get("note", rec["editor_note"])
        rec["history"].append(entry)
        report["accepted"] += 1
    if role == "annotator":
        genders = card_genders()
        for key in work_keys:
            rec = records[key]
            for side, field in (("speaker", "speaker_gender"), ("addressee", "addressee_gender")):
                card = genders.get(rec[side]["id"])
                if card and rec.get(field) in (None, "", "unknown"):
                    rec[field] = card
    missing = [k for k in work_keys if k not in {i.get("key") for i in items}]
    if missing:
        report["warnings"].append({"missing_keys": sorted(missing)[:20], "count": len(missing)})
    if dry_run:
        return report
    by_shard = {}
    for path in (MATRIX / game).glob("*.jsonl"):
        by_shard[path] = [records[r["key"]] for r in read_jsonl(path)]
    for path, rows in by_shard.items():
        write_jsonl(path, rows)
    write_json(ROOT / run_ref, {"batch": batch_id, "role": role, "model": model, "applied_at": now,
                                "input_file": str(file_path), "output": items, "report": report})
    state = load_state()
    if is_unit(batch_id):
        entry = state.setdefault("units", {}).setdefault(batch_id, {"status": "pending", "updated": None, "runs": []})
    else:
        entry = state["games"][game][batch_id]
    if not report["rejected"]:
        current = BATCH_FLOW.index(entry["status"]) if entry["status"] in BATCH_FLOW else 0
        result = ROLE_RESULT[role]
        if role == "editor" and any(records[k]["status"] in DRAFT_STATUSES for k in work_keys):
            result = None
        target = BATCH_FLOW.index(result) if result else current
        entry["status"] = BATCH_FLOW[max(current, target)]
    entry["updated"] = now
    entry["runs"].append(run_ref)
    save_state(state)
    return report


# --- команды -------------------------------------------------------------------

def cmd_status(game_filter):
    state = load_state()
    for game, batches in state["games"].items():
        if game_filter and game != game_filter:
            continue
        counts = {}
        for entry in batches.values():
            counts[entry["status"]] = counts.get(entry["status"], 0) + 1
        records = load_matrix(game)
        rs = {}
        for r in records:
            rs[r["status"]] = rs.get(r["status"], 0) + 1
        print(f"{game}: пакетов {len(batches)} {counts}; строк {len(records)} {rs}")


def queue_order(game):
    """Пакеты в порядке обработки: очередь по сюжету (tools.bkloc.queue), иначе порядок файла пакетов."""
    batches = json.loads((SCENES / f"{game}_batches.json").read_text(encoding="utf-8"))["batches"]
    queue_path = SCENES / f"{game}_queue.json"
    if not queue_path.exists():
        return [(b, {}) for b in batches]
    by_id = {b["batch_id"]: b for b in batches}
    return [(by_id[e["batch_id"]], e) for e in json.loads(queue_path.read_text(encoding="utf-8"))["entries"]]


def cmd_next(game):
    state = load_state()
    for b, info in queue_order(game):
        entry = state["games"][game][b["batch_id"]]
        if entry["status"] not in ("edited", "approved"):
            print(json.dumps({"batch_id": b["batch_id"], "status": entry["status"], "count": b["count"],
                              "words": b["words"], "scenes": b["scene_ids"][:5], "block": info.get("block"),
                              "chapter": info.get("chapter"), "order_confidence": info.get("order_confidence"),
                              "story_memory": info.get("story_memory", True)}, ensure_ascii=False))
            return
    print("Все пакеты утверждены")


def cmd_scope():
    for scope_name, scope in load_approvals().get("scopes", {}).items():
        print(f"{scope_name}: решение {scope.get('decision')}, роли {scope.get('roles')}, разрешено {scope.get('approved')}")
        state = load_state().get("units", {})
        for unit_id, unit in scope.get("units", {}).items():
            game, _, _, blocked = unit_info(unit_id)
            n = len(unit_records(game, unit, blocked))
            status = state.get(unit_id, {}).get("status", "pending")
            print(f"  {unit_id}: {unit.get('title', '')} — строк {n}, статус {status}")
        for key, why in scope.get("blocked", {}).items():
            print(f"  заблокировано {key}: {why}")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("scope")
    p = sub.add_parser("status")
    p.add_argument("--game")
    p = sub.add_parser("next")
    p.add_argument("--game", required=True, choices=sorted(GAMES))
    p = sub.add_parser("packet")
    p.add_argument("batch_id")
    p.add_argument("--role", required=True, choices=ROLES)
    p.add_argument("--prompt", help="шаблон prompts/<имя>.md вместо шаблона роли (например, translator_grok)")
    p.add_argument("--keys", help="файл со списком ключей для выборочной редактуры (по одному в строке)")
    p = sub.add_parser("apply")
    p.add_argument("batch_id")
    p.add_argument("--role", required=True, choices=ROLES)
    p.add_argument("--file", required=True)
    p.add_argument("--model", default="claude-opus")
    p.add_argument("--dry-run", action="store_true")
    p = sub.add_parser("init-state")
    args = parser.parse_args()
    if args.cmd == "scope":
        cmd_scope()
    elif args.cmd == "status":
        cmd_status(args.game)
    elif args.cmd == "next":
        cmd_next(args.game)
    elif args.cmd == "packet":
        only = None
        if args.keys:
            from pathlib import Path
            only = {k.strip() for k in Path(args.keys).read_text(encoding="utf-8-sig").splitlines() if k.strip()}
        path, prompt_sha, n = build_packet(args.batch_id, args.role, args.prompt, only)
        print(f"{path} (строк {n}, шаблон {prompt_sha[:10]})")
    elif args.cmd == "apply":
        from pathlib import Path
        report = apply_output(args.batch_id, args.role, Path(args.file), args.model, args.dry_run)
        print(json.dumps(report, ensure_ascii=False, indent=1)[:4000])
        return 1 if report["rejected"] else 0
    elif args.cmd == "init-state":
        save_state(load_state())
        init_memory()
        print(STATE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
