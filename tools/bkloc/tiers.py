"""Уровни сложности пакетов для распределения работы между моделями (D-032).

    python -m tools.bkloc.tiers [--game bk1|bk2] [--write]

Уровень назначается пакету целиком, чтобы сцена не делилась между моделями:
  story  — сюжетная сцена: перевод Opus, полная редактура Opus;
  mixed  — несложная сцена с героями: черновик Grok, полная редактура Opus;
  npc    — разговоры NPC: черновик Grok, автоматический QA, выборочная редактура Opus;
  names  — имена и названия: черновик Grok, полная редактура Opus (терминология);
  text   — описания, интерфейс, справка: черновик Grok, QA, выборочная редактура Opus.
Учитываются только непереведённые строки.
"""
import argparse
import collections
import json

from tools.bkloc.matrix import load as load_matrix
from tools.bkloc.paths import BASE, SCENES, write_json

TIERS_FILE = BASE / "scenes" / "tiers.json"
NAME_CATEGORIES = {"magnus_name", "npc_name", "location_name", "enemy_name", "party_name", "item_name"}
STORY_CATEGORIES = {"movie_subtitle", "story_summary", "letter"}
STORY_CAST_SHARE = 0.6
STORY_VOICED_SHARE = 0.3
MIXED_CAST_SHARE = 0.25


def main_cast():
    return {p.stem for d in ("bk1", "bk2", "shared") for p in (BASE / "characters" / d).glob("*.yaml")}


def classify(batch, records, cast):
    todo = [r for r in records if r["status"] == "untranslated"]
    if not todo:
        return None
    category = batch.get("category", "dialogue")
    stats = {"lines": len(todo), "words": sum(len(r["en"].split()) for r in todo), "category": category}
    if category in NAME_CATEGORIES:
        return {"tier": "names", **stats}
    if category in STORY_CATEGORIES:
        return {"tier": "story", **stats}
    if category != "dialogue":
        return {"tier": "text", **stats}
    cast_share = sum(r["speaker"]["id"] in cast for r in todo) / len(todo)
    voiced_share = sum("voiced" in r["flags"] for r in todo) / len(todo)
    stats.update(cast_share=round(cast_share, 2), voiced_share=round(voiced_share, 2), kind=batch.get("kind"))
    if voiced_share >= STORY_VOICED_SHARE or cast_share >= STORY_CAST_SHARE or batch.get("kind") == "event_block":
        tier = "story"
    elif cast_share >= MIXED_CAST_SHARE:
        tier = "mixed"
    else:
        tier = "npc"
    return {"tier": tier, **stats}


def build(games):
    cast = main_cast()
    result = {}
    for game in games:
        batches = json.loads((SCENES / f"{game}_batches.json").read_text(encoding="utf-8"))["batches"]
        by_batch = collections.defaultdict(list)
        for r in load_matrix(game):
            by_batch[r["batch_id"]].append(r)
        for b in batches:
            info = classify(b, by_batch[b["batch_id"]], cast)
            if info:
                result[b["batch_id"]] = info
    return result


def summary(tiers):
    table = collections.defaultdict(lambda: [0, 0, 0])
    for batch_id, info in tiers.items():
        row = table[(batch_id.split(":")[0], info["tier"])]
        row[0] += 1
        row[1] += info["lines"]
        row[2] += info["words"]
    for (game, tier), (n, lines, words) in sorted(table.items()):
        print(f"{game} {tier:6} пакетов {n:4} строк {lines:6} слов {words:7}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--game", choices=("bk1", "bk2"))
    p.add_argument("--write", action="store_true")
    args = p.parse_args()
    tiers = build([args.game] if args.game else ["bk1", "bk2"])
    summary(tiers)
    if args.write:
        write_json(TIERS_FILE, {"note": "python -m tools.bkloc.tiers --write; уровни по непереведённым строкам",
                                "tiers": tiers})


if __name__ == "__main__":
    main()
