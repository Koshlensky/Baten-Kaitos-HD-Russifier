"""Улики по персонажу из его собственных реплик (по табличкам имени из скриптов).

Для каждой карточки с полем name_tags собирает:
- реплики персонажа (табличка имени или продолжение окна) и первую сцену;
- род, который выбирают другие локализации в речи персонажа о себе (FR/ES/IT/DE, только BK1),
  и японские маркеры (первая персона, окончания) — маркеры не доказывают пол;
- вежливость и обращения к собеседникам: собеседник — персонаж, чьи реплики окружают
  данную (обмен двух говорящих), или имя в обращении; для BK1 — tu/vous во французском;
- упоминания персонажа в репликах других и английские местоимения рядом с именем.

Результат: base/characters/_evidence/<game>.json. Это материал для карточек, а не решения:
каждый вывод в карточке получает уровень достоверности и ссылки на строки.
"""
import argparse
import json
import re
import sys
from collections import Counter, defaultdict

import yaml

from tools.bkloc.paths import BASE, ROOT, read_jsonl, write_json

JP_FIRST = {
    "僕": "boku", "ぼく": "boku", "俺": "ore", "オレ": "ore", "おれ": "ore", "わたくし": "watakushi",
    "私": "watashi/watakushi", "わたし": "watashi", "あたし": "atashi", "わし": "washi", "ワシ": "washi",
    "我輩": "wagahai", "吾輩": "wagahai", "拙者": "sessha", "あたい": "atai", "おいら": "oira", "わらわ": "warawa",
}
JP_SECOND = {"お前": "omae", "おまえ": "omae", "あなた": "anata", "あんた": "anta", "君": "kimi", "きみ": "kimi",
             "貴様": "kisama", "てめえ": "temee", "おぬし": "onushi", "そなた": "sonata", "貴方": "anata"}
JP_POLITE = re.compile(r"(です|ます|でした|ました|ません|ございま|ください|下さい)")
FR_TU = re.compile(r"(?i)\b(tu|te|toi|ton|ta|tes)\b|\bt'")
FR_VOUS = re.compile(r"(?i)\b(vous|votre|vos)\b")
EN_HE = re.compile(r"\b(he|him|his|himself)\b", re.I)
EN_SHE = re.compile(r"\b(she|her|hers|herself)\b", re.I)


def load_cards(game):
    cards = {}
    for path in sorted((BASE / "characters").glob("*/*.yaml")):
        card = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        tags = [t.split(":", 1)[1] for t in card.get("name_tags") or [] if t.startswith(game + ":")]
        if tags:
            cards[card["id"]] = {"en_name": card.get("en_name"), "tags": tags, "aliases": card.get("aliases_en") or []}
    return cards


def strip(text):
    return re.sub(r"<[^<>\n]{1,40}>", " ", text or "")


def build(game):
    cards = load_cards(game)
    tag_owner = {t: cid for cid, c in cards.items() for t in c["tags"]}
    lines = {r["id"]: r for r in read_jsonl(ROOT / "source" / game / "lines.jsonl")}
    script = {r["id"]: r for r in read_jsonl(BASE / "evidence" / f"{game}_script_speakers.jsonl")}
    evidence = {r["id"]: r for r in read_jsonl(BASE / "evidence" / f"{game}.jsonl")}
    order = json.loads((BASE / "scenes" / f"{game}_order.json").read_text(encoding="utf-8"))
    sequences = json.loads((BASE / "scenes" / f"{game}_script_sequences.json").read_text(encoding="utf-8"))

    def speaker_card(text_id):
        sc = script.get(text_id)
        if sc and sc["status"] in ("name_tag", "continued") and sc["names"]:
            return tag_owner.get(sc["names"][0]["id"])
        return None

    # собеседник: в последовательности файла соседние реплики другого одного персонажа
    partner = defaultdict(Counter)
    for seq in sequences.values():
        who = [(item[1], tag_owner.get(item[2]) if item[2] else None) for item in seq]
        for i, (text_id, cid) in enumerate(who):
            if not cid:
                continue
            prev_c = who[i - 1][1] if i > 0 else None
            next_c = who[i + 1][1] if i + 1 < len(who) else None
            others = {c for c in (prev_c, next_c) if c and c != cid}
            if len(others) == 1:
                partner[text_id][others.pop()] += 1

    names_re = {cid: re.compile(r"\b(" + "|".join(re.escape(n) for n in [c["en_name"]] + c["aliases"] if n) + r")\b")
                for cid, c in cards.items() if c["en_name"]}
    result = {}
    for cid, card in cards.items():
        own = sorted((t for t in lines if speaker_card(t) == cid), key=lambda t: order.get(t, {}).get("global_order", 1e9))
        first = order.get(own[0], {}) if own else {}
        jp_first, jp_second, polite = Counter(), Counter(), 0
        eu = {"m": 0.0, "f": 0.0, "examples": []}
        jp_marks = Counter()
        pairs = defaultdict(lambda: {"lines": 0, "fr_tu": 0, "fr_vous": 0, "jp_polite": 0, "jp_second": Counter(), "examples": []})
        for t in own:
            row = lines[t]
            jp = row.get("jp") or ""
            for k, v in JP_FIRST.items():
                if k in jp:
                    jp_first[v] += 1
            is_polite = bool(JP_POLITE.search(jp))
            polite += is_polite
            ev = evidence.get(t) or {}
            for item in ev.get("items", []):
                if item.get("role") != "speaker" or not item.get("gender"):
                    continue
                if item["src"] in ("fr", "es", "it", "de"):
                    eu[item["gender"]] = eu.get(item["gender"], 0.0) + item.get("weight", 0)
                    if len(eu["examples"]) < 6:
                        eu["examples"].append(f"{t} [{item['src']}] {item.get('cue', '')}")
                elif item["src"] == "jp":
                    jp_marks[f"{item['gender']}: {item.get('cue', '')}"] += 1
            addressees = set(partner.get(t, {}))
            en = strip(row.get("en"))
            for other, rx in names_re.items():
                if other != cid and rx.search(en):
                    addressees.add(other)
            for other in addressees:
                p = pairs[other]
                p["lines"] += 1
                fr = row.get("fr") or ""
                p["fr_tu"] += bool(FR_TU.search(fr))
                p["fr_vous"] += bool(FR_VOUS.search(fr))
                p["jp_polite"] += is_polite
                for k, v in JP_SECOND.items():
                    if k in jp:
                        p["jp_second"][v] += 1
                if len(p["examples"]) < 4:
                    p["examples"].append(t)
        mentions = {"he": 0, "she": 0, "examples": []}
        rx = names_re.get(cid)
        if rx:
            for t, row in lines.items():
                if speaker_card(t) == cid:
                    continue
                en = strip(row.get("en"))
                m = rx.search(en)
                if not m:
                    continue
                tail = en[m.end():m.end() + 120]
                he, she = bool(EN_HE.search(tail)), bool(EN_SHE.search(tail))
                mentions["he"] += he
                mentions["she"] += she
                if (he or she) and len(mentions["examples"]) < 8:
                    mentions["examples"].append(f"{t}: {en.strip()[:140]}")
        result[cid] = {
            "lines": len(own), "first_line": own[0] if own else None, "first_scene": first.get("scene_id"),
            "jp_first_person": dict(jp_first.most_common()), "jp_polite_share": round(polite / len(own), 2) if own else None,
            "eu_self_gender": {"m": round(eu["m"], 2), "f": round(eu["f"], 2), "examples": eu["examples"]},
            "jp_markers": dict(jp_marks.most_common(6)),
            "partners": {o: {**{k: v for k, v in p.items() if k != "jp_second"}, "jp_second": dict(p["jp_second"])}
                         for o, p in sorted(pairs.items(), key=lambda kv: -kv[1]["lines"])},
            "mentions": mentions,
        }
    write_json(BASE / "characters" / "_evidence" / f"{game}.json", result)
    return result


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--game", default="bk1")
    args = parser.parse_args()
    result = build(args.game)
    for cid, r in result.items():
        print(f"{cid}: реплик {r['lines']}, первая {r['first_line']} ({r['first_scene']}); JP 1-е лицо {r['jp_first_person']}; "
              f"вежливость {r['jp_polite_share']}; род в FR/ES/IT/DE m={r['eu_self_gender']['m']} f={r['eu_self_gender']['f']}; "
              f"упоминания he={r['mentions']['he']} she={r['mentions']['she']}")
        for o, p in list(r["partners"].items())[:5]:
            print(f"    → {o}: {p['lines']} стр., FR tu={p['fr_tu']} vous={p['fr_vous']}, JP вежл.={p['jp_polite']}, JP 2-е лицо {p['jp_second']}")


if __name__ == "__main__":
    main()
