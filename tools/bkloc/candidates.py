"""Автоматические списки кандидатов для справочника персонажей и глоссария.

Русских решений здесь нет: только английский оригинал, японское написание
(из списков игры или по совместной встречаемости), частоты и примеры ID.
"""
import collections
import csv
import io
import re
import sys

from tools.bkloc import categories, tags
from tools.bkloc.paths import BASE, SOURCE, guard_write, read_jsonl

KATAKANA = re.compile(r"[ァ-ヴー・]{2,}")
CONNECTORS = {"of", "the", "de", "del", "la", "le", "von", "and"}
STOP = set("""The A An I You We They He She It What Why How Where When Who Yes No Oh Ah Hey Well But And So Then This
That There These Those Now Here Let Don't Do Did Is Are Was Were Be My Your Our Their His Her Its Not Just All Hmm
Huh Okay Right Wait Come Go Look See Please Thank Thanks Sorry Good Great Mr Mrs Ms Sir Lady Lord Master Of In On At
For With From To By As If Or Maybe Even Still Only Very Too Can Could Would Should Will Shall May Might Must Have Has
Had Ha Heh Whoa Wow Uh Um Er Ugh Hm Mm Eh Yeah Sure Fine One Two Three First Last Next Press Select Use Hp Lv Gold
Stock Back Cancel Buy Sell Item Items Save Load Yes No On Off New End Mom Dad Hello Welcome Hi Ow Hah""".split())


def write_csv(path, header, rows):
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    guard_write(path).write_text("\ufeff" + buf.getvalue(), encoding="utf-8", newline="\n")


def load(game):
    rows = read_jsonl(SOURCE / game / "lines.jsonl")
    for r in rows:
        r["_cat"], r["_form"] = categories.classify(r["id"], r["en"], r.get("jp"))
        r["_plain"] = tags.strip_tags(r["en"])
    return rows


def katakana_index(rows):
    overall = collections.Counter()
    for r in rows:
        overall.update(set(KATAKANA.findall(r.get("jp", ""))))
    return overall


def jp_guess(name, rows, overall):
    """Японское написание имени по совместной встречаемости (подъём частоты)."""
    hits = [r for r in rows if re.search(r"\b" + re.escape(name) + r"\b", r["_plain"])]
    if len(hits) < 3:
        return "", 0, len(hits)
    local = collections.Counter()
    for r in hits:
        local.update(set(KATAKANA.findall(r.get("jp", ""))))
    best, best_score = "", 0.0
    for word, n in local.most_common(15):
        lift = (n / len(hits)) / (overall[word] / max(1, len(rows)))
        share = n / len(hits)
        score = share * min(lift, 50) ** 0.5
        if share >= 0.3 and lift >= 5 and score > best_score:
            best, best_score = word, score
    return best, round(best_score, 1), len(hits)


def list_names(rows, cats):
    """Имена из списков игры с японским написанием."""
    found = collections.OrderedDict()
    for r in rows:
        if r["_cat"] in cats:
            en = r["en"].strip()
            if not en or len(en.split()) > 4 or not re.match(r"[A-Z]", en):
                continue
            item = found.setdefault(en, {"jp": re.sub(r"\s+", " ", r.get("jp", "")).strip(), "ids": [], "category": r["_cat"]})
            item["ids"].append(r["id"])
    return found


def dialogue_terms(rows):
    """Имена собственные и составные термины с заглавными буквами из реплик и описаний.

    Отсекаются сокращения (I'm, It's) и слова, которые в тексте чаще пишутся со строчной буквы.
    """
    lower = collections.Counter()
    for r in rows:
        lower.update(re.findall(r"\b[a-z][a-z'\-]*\b", r["_plain"]))
    counts, mid, examples = collections.Counter(), collections.Counter(), {}
    for r in rows:
        if r["_cat"] not in ("dialogue", "magnus_desc", "item_desc", "help", "story_summary", "encyclopedia", "letter", "battle_voice"):
            continue
        for sentence in re.split(r"(?<=[.!?…])\s+|\n", r["_plain"]):
            words = re.findall(r"[A-Za-z][A-Za-z'\-]*", sentence)
            i = 0
            while i < len(words):
                if words[i][0].isupper() and words[i] not in STOP:
                    j = i + 1
                    while j < len(words) and (words[j][0].isupper() or (words[j] in CONNECTORS and j + 1 < len(words) and words[j + 1][0].isupper())):
                        j += 1
                    term = " ".join(words[i:j])
                    if words[j - 1] in CONNECTORS:
                        term = " ".join(words[i:j - 1])
                    counts[term] += 1
                    if i > 0:
                        mid[term] += 1
                    examples.setdefault(term, r["id"])
                    i = j
                else:
                    i += 1
    merged_counts, merged_mid = collections.Counter(), collections.Counter()
    for term, n in counts.items():
        base = term[:-2] if term.endswith("'s") else term
        merged_counts[base] += n
        merged_mid[base] += mid[term]
        examples.setdefault(base, examples[term])
    counts, mid = merged_counts, merged_mid
    interjection = re.compile(r"^(?:A+h+|O+h+|H+m+|U+h+|E+h+|Wh+o+a+|Ha+h+|He+h+|Hu+h+|Ug+h+|Gr+|M+m+|Aa+h+|Ee+k+|Ye+s+|No+o+)$")
    result = {}
    for term, n in counts.items():
        words = term.split()
        if "'" in term or interjection.match(words[0]):
            continue
        if words[0] in STOP or mid[term] < 1:
            continue
        if len(words) == 1 and lower[term.lower()] >= 0.5 * n:
            continue
        if n >= 5 or (len(words) > 1 and n >= 3):
            result[term] = (n, examples[term])
    return result


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    data = {g: load(g) for g in ("bk1", "bk2")}
    overall = {g: katakana_index(rows) for g, rows in data.items()}

    # --- персонажи ---
    party_seed = {"bk1": ["Kalas", "Xelha", "Gibari", "Lyude", "Savyna", "Mizuti"], "bk2": ["Sagi", "Milly", "Guillo"]}
    evidence = {g: read_jsonl(BASE / "evidence" / f"{g}.jsonl") for g in data}
    for game, rows in data.items():
        names = list_names(rows, ("npc_name", "party_name"))
        for p in party_seed[game]:
            names.setdefault(p, {"jp": "", "ids": [], "category": "party_name"})
            names[p]["category"] = "party_name"
        vocative = collections.Counter(n for e in evidence[game] for n in e["addressee_candidates"])
        selfname = collections.Counter(n for e in evidence[game] for n in e["speaker_candidates"])
        out = []
        for en, info in names.items():
            single = re.fullmatch(r"[A-Z][a-z]+(?:-[A-Z][a-z]+)?", en) is not None
            mentions = sum(1 for r in rows if r["_cat"] == "dialogue" and re.search(r"\b" + re.escape(en) + r"\b", r["_plain"])) if single else 0
            guess, score, _ = jp_guess(en, rows, overall[game]) if single else ("", 0, 0)
            jp = info["jp"] if info["jp"] and info["jp"] != en else guess
            out.append([en, jp, "list" if info["jp"] and info["jp"] != en else ("co-occurrence" if guess else ""),
                        info["category"], mentions, vocative.get(en, 0), selfname.get(en, 0), " ".join(info["ids"][:3])])
        out.sort(key=lambda x: (-int(x[3] == "party_name"), -x[4]))
        write_csv(BASE / "characters" / f"candidates_{game}.csv",
                  ["en", "jp", "jp_source", "category", "dialogue_mentions", "vocatives", "self_names", "source_ids"], out)
        print(f"{game}: кандидатов в персонажи {len(out)}")

    # --- глоссарий ---
    lists = {
        "names": ("npc_name", "party_name", "enemy_name"),
        "locations": ("location_name",),
        "magnus": ("magnus_name", "item_name"),
    }
    for label, cats in lists.items():
        merged = collections.OrderedDict()
        for game, rows in data.items():
            for en, info in list_names(rows, cats).items():
                m = merged.setdefault(en, {"jp_bk1": "", "jp_bk2": "", "ids_bk1": [], "ids_bk2": [], "category": info["category"]})
                m[f"jp_{game}"] = info["jp"]
                m[f"ids_{game}"] = info["ids"]
        out = [[en, m["jp_bk1"], m["jp_bk2"], m["category"],
                "shared" if m["ids_bk1"] and m["ids_bk2"] else ("bk1" if m["ids_bk1"] else "bk2"),
                " ".join((m["ids_bk1"] + m["ids_bk2"])[:3])] for en, m in merged.items()]
        write_csv(BASE / "glossary" / f"candidates_{label}.csv", ["en", "jp_bk1", "jp_bk2", "category", "games", "source_ids"], out)
        print(f"глоссарий/{label}: {len(out)} (общих для двух игр: {sum(1 for o in out if o[4] == 'shared')})")

    terms = collections.OrderedDict()
    for game, rows in data.items():
        for term, (n, example) in dialogue_terms(rows).items():
            t = terms.setdefault(term, {"bk1": 0, "bk2": 0, "examples": []})
            t[game] = n
            t["examples"].append(example)
    known = set()
    for label in lists:
        for game, rows in data.items():
            known.update(list_names(rows, lists[label]))
    out = []
    for term, t in terms.items():
        games = "shared" if t["bk1"] and t["bk2"] else ("bk1" if t["bk1"] else "bk2")
        jp1, _, _ = jp_guess(term, data["bk1"], overall["bk1"]) if t["bk1"] >= 3 and " " not in term else ("", 0, 0)
        jp2, _, _ = jp_guess(term, data["bk2"], overall["bk2"]) if t["bk2"] >= 3 and " " not in term else ("", 0, 0)
        out.append([term, jp1, jp2, "in_lists" if term in known else "", games, t["bk1"], t["bk2"], " ".join(t["examples"][:2])])
    out.sort(key=lambda x: -(x[5] + x[6]))
    write_csv(BASE / "glossary" / "candidates_terms.csv",
              ["en", "jp_bk1_guess", "jp_bk2_guess", "note", "games", "count_bk1", "count_bk2", "example_ids"], out)
    print(f"глоссарий/terms: {len(out)} (общих: {sum(1 for o in out if o[4] == 'shared')})")


if __name__ == "__main__":
    main()
