"""Матрица переводов: одна запись на ID, английский оригинал и контекст, пустой перевод.

Файлы: base/matrix/<game>/<шард>.jsonl, индекс base/matrix/<game>/_index.json.
Повторный запуск сохраняет уже заполненные поля перевода и разметки (ru, status,
speaker, addressee, register, context, заметки, history) — пересобирается только
служебная часть из source/ и base/scenes/.
"""
import collections
import datetime
import json
import re
import sys

from tools.bkloc import categories, tags
from tools.bkloc.paths import BASE, GAMES, MATRIX, SCENES, SOURCE, read_jsonl, write_json, write_jsonl

SCHEMA_VERSION = 1
PRESERVE = ("ru", "status", "speaker", "speaker_gender", "addressee", "addressee_gender", "register",
            "context", "translator_note", "editor_note", "qa_flags", "history")
SPIRIT = "GUARDIAN_SPIRIT"


def shard_of(game, text_id, scene_id):
    if text_id.startswith("SCR_"):
        return categories.family_of(text_id)
    if game == "bk2" and re.fullmatch(r"EV_\d+", text_id):
        block = int(scene_id.split("#")[1])
        return f"EV_block{block // 10 * 10:03d}-{block // 10 * 10 + 9:03d}"
    return categories.group_of(text_id)


def spirit_variants(rows):
    """BK1: пары _0/_1 у меню выбора — реплики духа-хранителя (женский/мужской вариант)."""
    by_base = collections.defaultdict(dict)
    for row in rows:
        m = re.match(r"^(SCR_.+)_(\d)$", row["id"])
        if m and len(row["id"].split("_")) > 4:
            by_base[m.group(1)][m.group(2)] = row["id"]
    result = {}
    for base, variants in by_base.items():
        if "0" in variants and "1" in variants:
            result[variants["0"]] = {"kind": "spirit_gender", "gender": "f", "pair": variants["1"]}
            result[variants["1"]] = {"kind": "spirit_gender", "gender": "m", "pair": variants["0"]}
    return result


def card_name_tags(game):
    """ID таблички имени (PCN_1, KOMOMAI_060…) -> id карточки персонажа.

    В карточке поле name_tags хранит ID с префиксом игры: "bk1:PCN_1"."""
    import yaml
    result = {}
    for path in (BASE / "characters").glob("*/*.yaml"):
        card = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for tag in card.get("name_tags") or []:
            tag_game, _, tag_id = tag.partition(":")
            if tag_game == game:
                result[tag_id] = card["id"]
    return result


def script_speaker(sc, cards):
    """Сведения о говорящем из байткода (tools/re/speakers.py) для поля speaker."""
    info = {"status": sc["status"], "names": sc["names"],
            "no_tag_calls": sc.get("no_tag_calls", 0), "dynamic_calls": sc.get("dynamic_calls", 0)}
    update = {"script": info}
    if sc["status"] == "name_tag":
        tag = sc["names"][0]
        update.update({"id": cards.get(tag["id"], tag["id"]), "status": "confirmed_data",
                       "name_tag": {"id": tag["id"], "en": tag["en"]},
                       "basis": "табличка имени в скрипте игры (_SetNameTag)"})
    elif sc["status"] == "continued":
        tag = sc["names"][0]
        update.update({"id": cards.get(tag["id"], tag["id"]), "status": "inferred",
                       "name_tag": {"id": tag["id"], "en": tag["en"]},
                       "basis": "продолжение окна без новой таблички: тот же актёр, что у предыдущей реплики"})
    elif sc["status"] == "name_tag_conflict":
        update.update({"status": "needs_context",
                       "candidates": [cards.get(n["id"], n["id"]) for n in sc["names"]],
                       "basis": "одна строка вызывается с разными табличками имени"})
    return update


def auto_context(cat, form, en, variant, sc=None):
    notes = []
    if sc and sc["status"] == "name_tag_conflict":
        names = ", ".join(n["en"] for n in sc["names"][:6]) + (" …" if len(sc["names"]) > 6 else "")
        notes.append(f"Строку произносят разные персонажи ({names}): перевод должен подходить всем, без рода.")
    elif sc and sc["status"] == "no_tag":
        notes.append("Окно без таблички имени (описание, мысль или безымянный говорящий).")
    if variant:
        notes.append("Вариант реплики духа-хранителя для " + ("женского" if variant["gender"] == "f" else "мужского")
                     + " пола (пара " + variant["pair"] + "); в японском различается речь.")
    if form == "choice":
        notes.append("Меню выбора: пункты <aN> и переводы строк сохраняются.")
    if form == "internal" or cat == "internal":
        notes.append("Служебная строка разработчиков; в обычной игре, вероятно, не видна.")
    if form == "same_as_jp":
        notes.append("Совпадает с японским (коды/латиница).")
    if "<n0>" in en:
        notes.append("<n0> — имя духа-хранителя, введённое игроком.")
    if "$$NAME_" in en:
        notes.append("$$NAME_…$$ — имя подставляет игра.")
    return " ".join(notes)


def build(game):
    rows = read_jsonl(SOURCE / game / "lines.jsonl")
    by_id = {r["id"]: r for r in rows}
    order = json.loads((SCENES / f"{game}_order.json").read_text(encoding="utf-8"))
    batches = json.loads((SCENES / f"{game}_batches.json").read_text(encoding="utf-8"))["batches"]
    batch_of_scene = {sid: b["batch_id"] for b in batches for sid in b["scene_ids"]}
    evidence = {e["id"]: e for e in read_jsonl(BASE / "evidence" / f"{game}.jsonl")}
    script_path = BASE / "evidence" / f"{game}_script_speakers.jsonl"
    script = {e["id"]: e for e in read_jsonl(script_path)} if script_path.exists() else {}
    cards = card_name_tags(game)
    variants = spirit_variants(rows) if game == "bk1" else {}
    entry_sizes = collections.Counter(r["en_entry"] for r in rows)

    old = {}
    for path in (MATRIX / game).glob("*.jsonl"):
        for rec in read_jsonl(path):
            old[rec["key"]] = rec

    source_file = f"{GAMES[game]['text_bundle']}#TextData_en"
    shards = collections.defaultdict(list)
    for text_id, pos in sorted(order.items(), key=lambda kv: kv[1]["global_order"]):
        row = by_id[text_id]
        cat, form = categories.classify(text_id, row["en"], row.get("jp"))
        ev = evidence.get(text_id)
        variant = variants.get(text_id)
        speaker = {"id": "unknown", "status": "unknown", "source": "auto",
                   "candidates": ev["speaker_candidates"] if ev else [],
                   "gender_evidence": ev["speaker_gender"] if ev else {"verdict": "none", "m": 0.0, "f": 0.0},
                   "style": ev["speech_style"] if ev else []}
        sc = script.get(text_id)
        if sc:
            speaker.update(script_speaker(sc, cards))
        if variant:
            speaker.update({"id": SPIRIT, "status": "inferred",
                            "basis": "меню выбора с вариантами _0/_1; в японском женская/мужская речь"})
        addressee = {"id": "unknown", "status": "unknown", "candidates": ev["addressee_candidates"] if ev else []}
        rec = {
            "key": f"{game}:{text_id}",
            "game": game,
            "source_file": source_file,
            "text_id": text_id,
            "id_index": row["idx"],
            "category": cat,
            "form": form,
            "flags": categories.flags(row["en"]),
            "scene_id": pos["scene_id"],
            "batch_id": batch_of_scene.get(pos["scene_id"]),
            "order": pos["order"],
            "global_order": pos["global_order"],
            "prev_key": f"{game}:{pos['prev']}" if pos["prev"] else None,
            "next_key": f"{game}:{pos['next']}" if pos["next"] else None,
            "en": row["en"],
            "en_sha1": row["en_sha1"],
            "dedup_group": f"{game}:en#{row['en_entry']}",
            "dedup_size": entry_sizes[row["en_entry"]],
            "tags": tags.tokens(row["en"]),
            "variant": variant,
            "speaker": speaker,
            "speaker_gender": variant["gender"] if variant else "unknown",
            "addressee": addressee,
            "addressee_gender": "unknown",
            "register": "unknown",
            "context": auto_context(cat, form, row["en"], variant, sc),
            "ru": "",
            "status": "untranslated",
            "qa_flags": [],
            "translator_note": "",
            "editor_note": "",
            "history": [],
        }
        prev = old.get(rec["key"])
        if prev:
            for field in PRESERVE:
                if field in ("speaker", "addressee", "context"):
                    continue
                if field in prev and prev[field] not in ("", [], None, "unknown", "untranslated"):
                    rec[field] = prev[field]
            # Разметка разметчика сохраняется; автоматические улики (таблички, кандидаты) обновляются.
            for side in ("speaker", "addressee"):
                old_side = prev.get(side) or {}
                if old_side.get("source") == "annotator":
                    rec[side].update({k: old_side[k] for k in ("id", "status", "basis", "source") if k in old_side})
            if prev.get("context_annotator"):
                rec["context_annotator"] = prev["context_annotator"]
                rec["context"] = (rec["context"] + " " + prev["context_annotator"]).strip()
            if prev.get("en_sha1") != rec["en_sha1"] and prev.get("ru"):
                rec["status"] = "needs_context"
                rec["qa_flags"] = sorted(set(rec["qa_flags"]) | {"source_changed"})
        shards[shard_of(game, text_id, pos["scene_id"])].append(rec)

    for path in (MATRIX / game).glob("*.jsonl"):
        if path.stem not in shards:
            path.unlink()
    index = {}
    for name, records in shards.items():
        write_jsonl(MATRIX / game / f"{name}.jsonl", records)
        index[name] = {"records": len(records), "translated": sum(1 for r in records if r["ru"])}
    write_json(MATRIX / game / "_index.json", {
        "game": game, "schema_version": SCHEMA_VERSION,
        "built_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "records": sum(v["records"] for v in index.values()), "shards": index,
    })
    return sum(v["records"] for v in index.values()), len(shards), len(variants)


def hydrate(game, records):
    """Публичная матрица хранит только ID, русский текст и метаданные. Английский оригинал берётся из локального
    извлечения игры (source/<game>/lines.jsonl) и сверяется по en_sha1."""
    missing = [r for r in records if "en" not in r]
    if not missing:
        return records
    path = SOURCE / game / "lines.jsonl"
    if not path.exists():
        raise SystemExit(f"Нет английского оригинала для {game}: сначала выполните извлечение из вашей копии игры "
                         "(python -m tools.bkloc.extract), см. BUILDING.md.")
    src = {r["id"]: r for r in read_jsonl(path)}
    for r in missing:
        s = src.get(r["text_id"])
        if s is None:
            raise SystemExit(f"ID {r['key']} отсутствует в извлечённых текстах: версия игры не совпадает с матрицей.")
        if r.get("en_sha1") and s["en_sha1"] != r["en_sha1"]:
            raise SystemExit(f"Английский текст {r['key']} не совпадает с версией игры, для которой сделана матрица "
                             "(нужен Steam buildid 20890367).")
        r["en"] = s["en"]
    return records


def load(game):
    """Все записи матрицы игры в глобальном порядке (английский оригинал подставляется из локального извлечения)."""
    records = []
    for path in sorted((MATRIX / game).glob("*.jsonl")):
        records.extend(read_jsonl(path))
    records.sort(key=lambda r: r["global_order"])
    return hydrate(game, records)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    total = 0
    for game in GAMES:
        n, shards, variants = build(game)
        total += n
        print(f"{game}: записей {n}, шардов {shards}, вариантов реплик духа {variants}")
    print("всего записей", total)


if __name__ == "__main__":
    main()
