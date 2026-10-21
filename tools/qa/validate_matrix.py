"""Проверка структуры матрицы переводов (без сторонних библиотек).

Сверяет записи с base/matrix/schema.json, с исходным текстом из source/ и
между собой: уникальность ключей, полное покрытие TextIDList, совпадение
английского текста и хеша, теги, ссылки prev/next, согласованность статусов.
"""
import collections
import json
import sys

from tools.bkloc import tags
from tools.bkloc.matrix import load
from tools.bkloc.paths import BASE, GAMES, REPORTS, SOURCE, read_jsonl, sha1_text, write_json
from tools.qa import checks

SCHEMA = json.loads((BASE / "matrix" / "schema.json").read_text(encoding="utf-8"))
PROPS = SCHEMA["properties"]
REQUIRED = SCHEMA["required"]
TYPE_MAP = {"string": str, "integer": int, "array": list, "object": dict, "null": type(None)}


def check_type(value, spec):
    if "enum" in spec:
        return value in spec["enum"]
    types = spec.get("type")
    if types is None:
        return True
    types = types if isinstance(types, list) else [types]
    return any(isinstance(value, TYPE_MAP[t]) and not (t == "integer" and isinstance(value, bool)) for t in types)


def validate(game):
    errors = collections.defaultdict(list)
    records = load(game)
    source = {r["id"]: r for r in read_jsonl(SOURCE / game / "lines.jsonl")}
    keys = collections.Counter(r.get("key") for r in records)
    by_key = {r["key"]: r for r in records}
    for dup, n in keys.items():
        if n > 1:
            errors["duplicate_key"].append(dup)
    missing = set(source) - {r["text_id"] for r in records}
    extra = {r["text_id"] for r in records} - set(source)
    errors["missing_ids"].extend(sorted(missing)[:50])
    errors["unknown_ids"].extend(sorted(extra)[:50])
    for r in records:
        key = r.get("key", "?")
        for field in REQUIRED:
            if field not in r:
                errors["missing_field"].append(f"{key}:{field}")
        for field in r:
            if field not in PROPS:
                errors["unexpected_field"].append(f"{key}:{field}")
            elif not check_type(r[field], PROPS[field]):
                errors["bad_value"].append(f"{key}:{field}={str(r[field])[:40]}")
        for sub in ("speaker", "addressee"):
            status = r.get(sub, {}).get("status")
            if status not in PROPS[sub]["properties"]["status"]["enum"]:
                errors["bad_value"].append(f"{key}:{sub}.status={status}")
        if r.get("key") != f"{game}:{r.get('text_id')}":
            errors["bad_key"].append(key)
        src = source.get(r.get("text_id"))
        if src is not None:
            if r["en"] != src["en"]:
                errors["en_differs_from_source"].append(key)
            if r["en_sha1"] != sha1_text(r["en"]):
                errors["en_sha1_mismatch"].append(key)
            if r["tags"] != tags.tokens(r["en"]):
                errors["tags_mismatch"].append(key)
        for link, back in (("prev_key", "next_key"), ("next_key", "prev_key")):
            other = r.get(link)
            if other is not None and (other not in by_key or by_key[other].get(back) != key):
                errors["broken_link"].append(f"{key}:{link}")
        ru, status = r.get("ru", ""), r.get("status")
        if ru and status == "untranslated":
            errors["status_untranslated_with_ru"].append(key)
        if not ru and status in checks.TRANSLATED:
            errors["status_without_ru"].append(key)
    summary = {k: len(v) for k, v in errors.items() if v}
    return len(records), summary, {k: v[:20] for k, v in errors.items() if v}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    report = {}
    failed = False
    for game in GAMES:
        count, summary, samples = validate(game)
        report[game] = {"records": count, "errors": summary, "samples": samples}
        print(f"{game}: записей {count}; ошибок: {summary or 'нет'}")
        failed = failed or bool(summary)
    write_json(REPORTS / "qa" / "matrix_validation.json", report)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
