"""Очередь пакетов BK2 в сюжетном порядке: base/scenes/bk2_queue.json (D-036).

    python -m tools.bkloc.queue_bk2

Порядок:
  1. Сюжетные события (event_block, EV#000…EV#068) строго по номеру — это порядок игры.
  2. Затем полевые пакеты мест (field_block) в порядке привязки к блоку события (`ANCHORS`: индекс блока).
     Пакет места получает сюжетную память только до своего блока события (`chapter`), без поздних событий. Привязка приблизительная: по главам официальной канвы
     (названия глав ≈ блоки событий, коэффициент ≈ 1,28) и по плотности упоминаний в событиях;
     основание записано в `basis`, у записей `order_confidence: approximate`.
  3. Места без надёжного сигнала идут отдельным блоком после событий (`unplaced`), без сюжетной памяти
     (`story_memory: false`): память будущих событий не должна попадать в ранние сцены (D-030).
  4. Служебные семейства и наборы (интерфейс, справка, названия) — в конце, без сюжетной памяти.
Алфавитный порядок не используется для привязанных мест; внутри блока «без сигнала» сохранён порядок файла.
"""
import json

from tools.bkloc.paths import SCENES, write_json

# локация -> (индекс блока события, основание). Индекс = номер блока EV (bk2:B0000 = 0).
ANCHORS = {
    "MINTAKA_N": (1, "пролог: резиденция императора, Минтака (глава 01 «To the emperor's residence»)"),
    "MINTAKA": (1, "пролог: Минтака (упоминания с EV#000)"),
    "SEWER": (2, "канализация в побеге из резиденции (упоминание EV#000)"),
    "IMPERIAL": (3, "резиденция/имперские здания (упоминание EV#002)"),
    "SHERATAN": (10, "деревня Шератан, главы 07–08 «Hassaleh at last!», «The world's finest wings»; события EV#001–003, 012–015"),
    "F_VILLAGE": (10, "деревня рядом с Шератаном (упоминание EV#001)"),
    "R_LAKE": (12, "озеро, глава 09 «Secret of the lake»"),
    "SEDNA": (12, "озеро и чудовище Седна, глава 09"),
    "CUJAM": (14, "упоминания EV#005–007, 021, 026–028"),
    "RASALAS": (14, "упоминания EV#005–006, 021; канва 27"),
    "ZAURAK": (9, "упоминание EV#008"),
    "CLOUD": (9, "облачные дороги, EV#008; канва 21"),
    "MATAR": (16, "упоминания EV#013, 028"),
    "NEKKAR": (16, "упоминание EV#013"),
    "NIHAL": (22, "упоминания EV#018, 042"),
    "AZHA": (20, "глава 16 «The Azha investigation» (≈ EV#020); упоминания EV#004, 018"),
    "LORD": (24, "глава 19 «Lords a-leaping»"),
    "BAELHEIT": (26, "глава 20 «Off to see Baelheit»; упоминания с EV#004"),
    "SENKAN": (27, "глава 21 «A ship! How lovely!»"),
    "NASHIRA": (31, "глава 24 «Escape from Nashira»; упоминания EV#020–022"),
    "F_CAVE": (31, "упоминание EV#031; канва 16"),
    "ATRIA": (33, "упоминания EV#026–027; глава 26 «Invasion of the Vents»"),
    "SEGINUS": (36, "упоминание EV#028; плотность EV#049–050"),
    "VEGA": (36, "упоминание EV#036"),
    "TARAZED": (37, "плотность упоминаний EV#036–040"),
    "PHERKAD": (40, "канва 30 «Malpercio's afterlings»; упоминания EV#032, 065"),
    "HOLOHOLO": (50, "глава 39 «Holy Holoholo!» (≈ EV#050)"),
    "SFIEDA": (54, "глава 42 «Repairing the Sfida»"),
    "TALK_SFIEDA": (54, "глава 42"),
    "NUNKI": (55, "упоминания EV#049, 055; канва 33–36"),
    "HQ": (60, "штаб: реплики EV#016, 048, 060, 064–065"),
    "ALGORAB": (57, "упоминание EV#057"),
    "OPU": (58, "упоминания EV#057, 066"),
    "GEMMA": (58, "упоминание EV#058"),
    "ELNATH": (62, "упоминания EV#061, 064; канва 24"),
    "CEBALRAI": (65, "упоминание EV#065"),
}


def build():
    batches = json.loads((SCENES / "bk2_batches.json").read_text(encoding="utf-8"))["batches"]
    events = [b for b in batches if b["kind"] == "event_block"]
    fields = [b for b in batches if b["kind"] == "field_block"]
    sets = [b for b in batches if b["kind"] not in ("event_block", "field_block")]

    def loc(b):
        return b["scene_ids"][0].split(":")[1].split("#")[0]

    placed = {}
    unplaced = []
    for b in fields:
        if loc(b) in ANCHORS:
            placed.setdefault(ANCHORS[loc(b)][0], []).append(b)
        else:
            unplaced.append(b)
    entries = []

    def add(b, block, chapter, conf, basis, memory):
        entries.append({"batch_id": b["batch_id"], "original_index": int(b["batch_id"].split("B")[1]), "family": loc(b) if b["kind"] == "field_block" else "EV",
                        "block": block, "chapter": chapter, "order_confidence": conf, "basis": basis,
                        "story_memory": memory, "count": b["count"]})

    for i, ev in enumerate(events):
        add(ev, "event", i, "high", "порядок сюжетных событий EV#" + ev["batch_id"].split("B")[1], True)
    for anchor in sorted(placed):
        for b in placed[anchor]:
            add(b, "location", anchor, "approximate", ANCHORS[loc(b)][1], True)
    for b in unplaced:
        add(b, "unplaced", None, "unknown", "нет надёжного сигнала о месте в сюжете: без сюжетной памяти (D-030)", False)
    for b in sets:
        add(b, "set", None, "n/a", "служебный набор (интерфейс, справка, названия)", False)
    return {"game": "bk2", "note": "python -m tools.bkloc.queue_bk2; D-036. Сюжетные события по номеру, места с сигналом — после блока события "
                                   "(приблизительно), остальные — отдельным блоком без сюжетной памяти.",
            "order": [e["batch_id"] for e in entries], "entries": entries}


def main():
    q = build()
    write_json(SCENES / "bk2_queue.json", q)
    import collections
    print(len(q["entries"]), "пакетов;", dict(collections.Counter(e["block"] for e in q["entries"])))


if __name__ == "__main__":
    main()
