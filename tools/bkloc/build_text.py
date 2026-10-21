"""Сборка русского документа TextData по ID (для проверки и будущего установщика).

Для каждого ID из TextIDList берётся перевод из матрицы, если он есть, его статус не ниже
заданного и хеш английского текста совпадает с текущим оригиналом. Иначе — английский
текст (запасной вариант). Строки с одинаковым итоговым текстом группируются, как в оригинале.
"""
import json

from tools.bkloc.matrix import load as load_matrix
from tools.bkloc.paths import SOURCE, sha1_text

STATUS_ORDER = ["untranslated", "needs_context", "blocked_decision", "needs_fit", "draft", "translated", "auto_checked",
                "qa_passed", "self_reviewed", "sampled_review", "edited", "approved"]


def read_raw(game, name):
    raw = (SOURCE / game / "raw" / f"{name}.json").read_bytes()
    text = raw[3:].decode("utf-8") if raw.startswith(b"\xef\xbb\xbf") else raw.decode("utf-8")
    return json.loads(text)


def build_document(game, min_status="edited"):
    ids = read_raw(game, "TextIDList")["textId"]
    en_doc = read_raw(game, "TextData_en")
    en_by_index = {}
    for entry in en_doc["array"]:
        for i in entry["i"]:
            en_by_index[i] = entry["t"]
    records = {r["text_id"]: r for r in load_matrix(game)}
    threshold = STATUS_ORDER.index(min_status)
    groups, order = {}, []
    stats = {"ids": len(ids), "translated": 0, "fallback_en": 0, "stale": 0, "missing_in_matrix": 0}
    for index, text_id in enumerate(ids):
        en = en_by_index.get(index, "")
        rec = records.get(text_id)
        text = en
        if rec is None:
            stats["missing_in_matrix"] += 1
        elif rec["ru"] and rec["status"] in STATUS_ORDER and STATUS_ORDER.index(rec["status"]) >= threshold:
            if rec["en_sha1"] == sha1_text(en):
                text = rec["ru"]
                stats["translated"] += 1
            else:
                stats["stale"] += 1
        if text == en:
            stats["fallback_en"] += 1
        if text not in groups:
            groups[text] = []
            order.append(text)
        groups[text].append(index)
    doc = {"array": [{"i": groups[t], "t": t} for t in order]}
    payload = "\ufeff" + json.dumps(doc, ensure_ascii=False, separators=(",", ":"))
    return doc, payload, stats
