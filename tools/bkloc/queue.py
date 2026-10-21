"""Очередь обработки пакетов по приблизительному сюжетному порядку (не меняет пакеты, ID и переводы).

    python -m tools.bkloc.queue --game bk1

Пакет получает главу первого посещения места своего семейства скриптов
(base/scenes/<игра>_family_chapters.json). Блоки очереди:
  1. location — семейства с известным местом, по главе (порядок приблизительный);
  2. unknown  — сюжетные и прочие семейства без надёжного места: в конце, в исходной группировке;
     их события пишутся только в память сцен, не в story_so_far;
  3. set      — справочные наборы (магнусы, интерфейс, справка), в исходном порядке.
Внутри блока и главы сохраняется исходный порядок пакетов. Результат — base/scenes/<игра>_queue.json.
"""
import argparse
import collections
import json
import sys

from tools.bkloc.paths import SCENES, write_json

BLOCKS = {"location": 0, "unknown": 1, "set": 2}


def family_of(batch, scenes):
    counts = collections.Counter()
    for scene_id in batch["scene_ids"]:
        scene = scenes.get(scene_id)
        if scene:
            counts[scene.get("family")] += scene.get("count", 1)
    return counts.most_common(1)[0][0] if counts else None


def build(game):
    batches = json.loads((SCENES / f"{game}_batches.json").read_text(encoding="utf-8"))["batches"]
    scenes = {s["scene_id"]: s for s in json.loads((SCENES / f"{game}_scenes.json").read_text(encoding="utf-8"))["scenes"]}
    config = json.loads((SCENES / f"{game}_family_chapters.json").read_text(encoding="utf-8"))
    entries = []
    for index, batch in enumerate(batches):
        family = family_of(batch, scenes)
        if batch["kind"] != "event":
            block, chapter, basis = "set", None, "справочный набор"
        elif family in config["families"]:
            chapter, basis = config["families"][family]
            block = "location"
        else:
            block, chapter, basis = "unknown", None, "место и глава не установлены"
        entries.append({"batch_id": batch["batch_id"], "original_index": index, "family": family, "block": block,
                        "chapter": chapter, "order_confidence": "approximate" if block == "location" else "unknown",
                        "basis": basis, "story_memory": block == "location", "count": batch["count"]})
    entries.sort(key=lambda e: (BLOCKS[e["block"]], e["chapter"] or 0, e["original_index"]))
    queue = {"game": game, "note": __doc__.split("\n\n")[1].strip(), "order": [e["batch_id"] for e in entries],
             "entries": entries}
    write_json(SCENES / f"{game}_queue.json", queue)
    return queue


def load(game):
    path = SCENES / f"{game}_queue.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--game", required=True)
    args = parser.parse_args()
    queue = build(args.game)
    blocks = collections.Counter(e["block"] for e in queue["entries"])
    lines = collections.Counter()
    for e in queue["entries"]:
        lines[e["block"]] += e["count"]
    print(f"{args.game}: пакетов {len(queue['order'])}; " + ", ".join(f"{b} {blocks[b]} ({lines[b]} строк)" for b in BLOCKS))
    chapters = collections.Counter((e["chapter"], e["family"]) for e in queue["entries"] if e["block"] == "location")
    for (chapter, family), n in sorted(chapters.items()):
        print(f"  глава {chapter:>2}: {family} — пакетов {n}")


if __name__ == "__main__":
    main()
