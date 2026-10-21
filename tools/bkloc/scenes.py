"""Скелет сцен: группировка строк, порядок, привязка к локациям и главам.

BK1: сцена = (скрипт, событие) из ID SCR_{скрипт}_{событие}_{смещение}[_{вариант}],
порядок внутри — по смещению (порядок в коде скрипта). Межсценовый порядок
приблизительный: по официальным сводкам SaveStoryLine и порядку точек сохранения.

BK2: сюжет EV_0000…EV_4332 идёт по порядку, но границы сцен в тексте не отмечены —
разбиение эвристическое (provisional). Полевые реплики — блоки по локациям.
Привязка к главам — монотонное выравнивание блоков с дневником STORYOUTLINE.
"""
import collections
import datetime
import math
import re
import sys

from tools.bkloc import categories, tags
from tools.bkloc.paths import SCENES, SOURCE, read_jsonl, write_json

SET_CHUNK = 150
FIELD_CHUNK = 60
EV_MIN, EV_TARGET, EV_MAX = 30, 60, 110
STOP = set("""The A An I I'm I'll I've You You're We We're They He She It It's What Why How Where When Who
Yes No Oh Ah Hey Well But And So Then This That There These Those Now Here Let Let's Don't Do Did Is Are Was
Were Be My Your Our Their His Her Its Not Just All Hmm Huh Okay Ok Right Wait Come Go Look See Please Thank
Thanks Sorry Good Great Mr Mrs Ms Sir Lady Lord Master Of In On At For With From To By As If Or Maybe Even
Still Only Very Too Can Could Would Should Will Shall May Might Must Have Has Had Ha Heh Whoa Wow Uh Um Er
Ugh Hm Mm Eh Gah Argh Yeah Nope Sure Fine Wh Th Wha Ahh Ohh Hah Hoo One Two Three First Last Next End
Magnus Back Cancel Yes Stock Buy Sell Shop Item Items Lv Debug Save Load""".split())


def parse_scr(text_id):
    parts = text_id.split("_")[1:]
    i = next(k for k, p in enumerate(parts) if p.isdigit())
    return "_".join(parts[:i]), int(parts[i]), int(parts[i + 1]), [int(v) for v in parts[i + 2:]]


def proper_nouns(text, vocab=None):
    found = re.findall(r"\b[A-Z][a-z]+(?:-[A-Z]?[a-z]+)?\b", tags.strip_tags(text))
    names = [w for w in found if w not in STOP]
    if vocab is not None:
        names = [w for w in names if w in vocab]
    return names


def tfidf(docs):
    df = collections.Counter()
    for doc in docs:
        df.update(set(doc))
    n = len(docs)
    vectors = []
    for doc in docs:
        tf = collections.Counter(doc)
        vec = {w: c * math.log((n + 1) / (df[w] + 0.5)) for w, c in tf.items()}
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        vectors.append({w: v / norm for w, v in vec.items()})
    return vectors


def cosine(a, b):
    if len(a) > len(b):
        a, b = b, a
    return sum(v * b.get(w, 0.0) for w, v in a.items())


def known_names(rows):
    """Имена собственные: из списков имён и локаций игры плюс слова с заглавной буквы
    в середине предложения, встретившиеся хотя бы дважды."""
    vocab = set()
    mid = collections.Counter()
    for row in rows:
        cat, _ = categories.classify(row["id"], row["en"], row.get("jp"))
        if cat in ("npc_name", "party_name", "location_name", "enemy_name") or row["id"].startswith("GRP_"):
            vocab.update(re.findall(r"\b[A-Z][a-z]+(?:-[A-Z]?[a-z]+)?\b", row["en"]))
        if cat == "dialogue" or cat == "story_summary":
            mid.update(re.findall(r"(?<=[a-z,;] )([A-Z][a-z]{2,})\b", tags.strip_tags(row["en"])))
    vocab.update(w for w, c in mid.items() if c >= 2)
    return vocab - STOP


def make_scene(game, scene_id, kind, rows, boundary, confidence, extra=None):
    scene = {
        "scene_id": f"{game}:{scene_id}",
        "kind": kind,
        "group": categories.group_of(rows[0]["id"]),
        "family": categories.family_of(rows[0]["id"]),
        "category": categories.classify(rows[0]["id"], rows[0]["en"], rows[0].get("jp"))[0],
        "ids": [r["id"] for r in rows],
        "count": len(rows),
        "words": sum(len(re.findall(r"[A-Za-z0-9']+", tags.strip_tags(r["en"]))) for r in rows),
        "boundary": boundary,
        "order_confidence": confidence,
    }
    if extra:
        scene.update(extra)
    return scene


def chunk(rows, size):
    return [rows[i:i + size] for i in range(0, len(rows), size)]


def location_guesses(families, vocab_locations):
    """Код семейства скриптов -> наиболее часто упоминаемая локация (догадка с цифрами)."""
    result = {}
    for family, rows in families.items():
        counts = collections.Counter()
        clerk = None
        for r in rows:
            text = tags.strip_tags(r["en"])
            m = re.search(r"(?:store clerk|shopkeeper|shop attendant)[^.]*?\bin ([A-Z][a-z]+)|"
                          r"Conversation with ([A-Z][a-z]+) shopkeeper|^([A-Z][a-z]+(?: [A-Z][a-z]+)?) (?:Shop Attendant|shopkeeper)", text)
            if m:
                clerk = next(g for g in m.groups() if g)
            for name in vocab_locations:
                if re.search(r"\b" + re.escape(name) + r"\b", text):
                    counts[name] += 1
        total = sum(counts.values())
        top = counts.most_common(3)
        result[family] = {
            "location_guess": clerk or (top[0][0] if top else None),
            "basis": "shop_clerk_label" if clerk else ("mentions" if top else "none"),
            "confidence": "high" if clerk else ("low" if top else "none"),
            "top_mentions": top,
            "share": round(top[0][1] / total, 2) if total else 0.0,
            "lines": len(rows),
        }
    return result


def location_vocab(rows):
    names = set()
    for row in rows:
        cat, _ = categories.classify(row["id"], row["en"], row.get("jp"))
        if cat == "location_name" or row["id"].startswith(("GRP_", "STG_", "SVP_", "STN_", "SAVE_POINT_")):
            core = re.split(r",|:", row["en"])[0].strip()
            core = re.sub(r"^The ", "", core)
            if re.fullmatch(r"[A-Z][a-z]+(?: [A-Z][a-z]+)?", core) and core not in STOP:
                names.add(core)
    return names


def build_bk1(rows):
    vocab = known_names(rows)
    scenes = []
    scr = collections.OrderedDict()
    others = collections.OrderedDict()
    for r in rows:
        if r["id"].startswith("SCR_"):
            script, event, offset, variants = parse_scr(r["id"])
            r["_key"] = (offset, variants)
            scr.setdefault((script, event), []).append(r)
        else:
            others.setdefault(categories.group_of(r["id"]), []).append(r)

    # Привязка скриптов к сводкам SaveStoryLine по именам собственным
    summaries = [r for r in rows if r["id"].startswith("SaveStoryLine_")]
    by_script = collections.defaultdict(list)
    for (script, _event), lines in scr.items():
        by_script[script].extend(lines)
    script_names = sorted(by_script)
    docs = [proper_nouns(" ".join(x["en"] for x in by_script[s]), vocab) for s in script_names]
    sdocs = [proper_nouns(s["en"], vocab) for s in summaries]
    vecs = tfidf(docs + sdocs)
    svecs, dvecs = vecs[len(docs):], vecs[:len(docs)]
    chapter = {}
    for s, vec in zip(script_names, dvecs):
        scores = [cosine(vec, sv) for sv in svecs]
        best = max(range(len(scores)), key=scores.__getitem__) if scores else None
        if best is not None and scores[best] >= 0.15:
            chapter[s] = {"index": best + 1, "id": summaries[best]["id"], "score": round(scores[best], 3),
                          "summary": summaries[best]["en"][:160]}

    families = collections.defaultdict(list)
    for (script, _event), lines in scr.items():
        families[categories.family_of("SCR_" + script + "_0_0")].extend(lines)
    loc_vocab = location_vocab(rows)
    fam_guess = location_guesses(families, loc_vocab)

    for (script, event), lines in scr.items():
        lines.sort(key=lambda x: x["_key"])
        family = categories.family_of("SCR_" + script + "_0_0")
        scenes.append(make_scene(
            "bk1", f"SCR_{script}_{event}", "event", lines, "exact_script_function", "within_scene_code_order",
            {"script": script, "event": event, "chapter_guess": chapter.get(script),
             "location_guess": fam_guess.get(family, {}).get("location_guess")}))
    for group, lines in others.items():
        for n, part in enumerate(chunk(lines, SET_CHUNK)):
            scenes.append(make_scene("bk1", f"{group}#{n:02d}", "set", part, "chunk", "id_order"))

    def sort_key(scene):
        ch = scene.get("chapter_guess")
        rank = ch["index"] if ch else 99
        if scene["kind"] != "event":
            rank = 200
        return (rank, scene.get("script", scene["group"]), scene.get("event", 0), scene["scene_id"])

    scenes.sort(key=sort_key)
    return scenes, {"families": fam_guess, "summaries": [s["id"] for s in summaries]}


def segment_ev(lines, vocab):
    """Эвристические границы сцен в сплошной нумерации EV: минимум общих имён по обе стороны разреза."""
    names = [set(proper_nouns(r["en"], vocab)) | ({"<n0>"} if "<n0>" in r["en"] else set()) for r in lines]
    blocks, start, n = [], 0, len(lines)
    while start < n:
        if n - start <= EV_MAX:
            blocks.append((start, n))
            break
        best, best_score = None, None
        for k in range(start + EV_MIN, min(n, start + EV_MAX)):
            before = set().union(*names[max(start, k - 8):k])
            after = set().union(*names[k:k + 8])
            union = before | after
            jaccard = len(before & after) / len(union) if union else 1.0
            score = jaccard + abs(k - start - EV_TARGET) / 400.0
            if best_score is None or score < best_score:
                best, best_score = k, score
        blocks.append((start, best))
        start = best
    return blocks


def build_bk2(rows):
    vocab = known_names(rows)
    scenes = []
    ev = [r for r in rows if re.fullmatch(r"EV_\d+", r["id"])]
    ev.sort(key=lambda r: int(r["id"][3:]))
    others = collections.OrderedDict()
    for r in rows:
        if not re.fullmatch(r"EV_\d+", r["id"]):
            others.setdefault(categories.group_of(r["id"]), []).append(r)

    # Хронологический дневник: STORYOUTLINE идёт от конца к началу, обучающие записи исключаются
    outline = [r for r in rows if r["id"].startswith("STORYOUTLINE_")]
    outline = [r for r in outline if not r["en"].startswith("<s0>")][::-1]
    titles = [r for r in rows if r["id"].startswith("STORYOUTLINENAME_") and "<a" in r["en"]]

    blocks = segment_ev(ev, vocab)
    block_rows = [ev[a:b] for a, b in blocks]
    docs = [proper_nouns(" ".join(x["en"] for x in br), vocab) for br in block_rows]
    odocs = [proper_nouns(o["en"], vocab) for o in outline]
    vecs = tfidf(docs + odocs)
    bvec, ovec = vecs[:len(docs)], vecs[len(docs):]
    # Монотонное выравнивание блоков с записями дневника (динамическое программирование)
    nb, no = len(bvec), len(ovec)
    sim = [[cosine(bvec[i], ovec[j]) for j in range(no)] for i in range(nb)]

    def prior(i, j):
        # Слабый приоритет диагонали: при скудных уликах выравнивание идёт пропорционально.
        return 0.1 * max(0.0, 1 - abs(i / max(1, nb - 1) - j / max(1, no - 1)) * 5)

    score = [[sim[i][j] + prior(i, j) for j in range(no)] for i in range(nb)]
    dp = [[0.0] * no for _ in range(nb)]
    back = [[0] * no for _ in range(nb)]
    for j in range(no):
        dp[0][j] = score[0][j]
    for i in range(1, nb):
        best_j, best_v = 0, dp[i - 1][0]
        for j in range(no):
            if dp[i - 1][j] > best_v:
                best_j, best_v = j, dp[i - 1][j]
            dp[i][j] = best_v + score[i][j]
            back[i][j] = best_j
    j = max(range(no), key=lambda x: dp[nb - 1][x]) if no else 0
    assign = [0] * nb
    for i in range(nb - 1, -1, -1):
        assign[i] = j
        j = back[i][j]

    for n, br in enumerate(block_rows):
        o = outline[assign[n]] if outline else None
        guess = {"outline_id": o["id"], "outline_rank": assign[n] + 1, "score": round(sim[n][assign[n]], 3),
                 "outline": o["en"][:160]} if o else None
        scenes.append(make_scene("bk2", f"EV#{n:03d}", "event_block", br, "provisional_heuristic",
                                 "id_order_story", {"ev_range": [br[0]["id"], br[-1]["id"]], "chapter_guess": guess}))
    for group, lines in others.items():
        cat = categories.classify(lines[0]["id"], lines[0]["en"], lines[0].get("jp"))[0]
        size = FIELD_CHUNK if cat == "dialogue" else SET_CHUNK
        kind = "field_block" if cat == "dialogue" else "set"
        for n, part in enumerate(chunk(lines, size)):
            scenes.append(make_scene("bk2", f"{group}#{n:02d}", kind, part, "chunk", "id_order"))
    return scenes, {"outline_entries": len(outline), "chapter_titles": len(titles), "ev_blocks": len(block_rows)}


BATCH_MIN, BATCH_MAX = 40, 150


def make_batches(game, scenes):
    """Рабочие пакеты для перевода: короткие события одного скрипта объединяются."""
    batches, current = [], []

    def flush():
        if current:
            batches.append({
                "batch_id": f"{game}:B{len(batches):04d}",
                "scene_ids": [s["scene_id"] for s in current],
                "kind": current[0]["kind"],
                "category": current[0]["category"],
                "count": sum(s["count"] for s in current),
                "words": sum(s["words"] for s in current),
            })
            current.clear()

    for scene in scenes:
        if scene["kind"] == "event":
            size = sum(s["count"] for s in current)
            if current and (current[0]["family"] != scene["family"] or size >= BATCH_MIN
                            or size + scene["count"] > BATCH_MAX):
                flush()
            current.append(scene)
        else:
            flush()
            current.append(scene)
            flush()
    flush()
    return batches


def link(scenes):
    """prev/next внутри сцены и глобальный порядок."""
    order = {}
    pos = 0
    for scene in scenes:
        ids = scene["ids"]
        for i, text_id in enumerate(ids):
            order[text_id] = {"scene_id": scene["scene_id"], "order": i, "global_order": pos,
                              "prev": ids[i - 1] if i else None, "next": ids[i + 1] if i + 1 < len(ids) else None}
            pos += 1
    return order


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    for game, builder in (("bk1", build_bk1), ("bk2", build_bk2)):
        rows = read_jsonl(SOURCE / game / "lines.jsonl")
        scenes, meta = builder(rows)
        order = link(scenes)
        assert len(order) == len(rows), (len(order), len(rows))
        doc = {"game": game, "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
               "method": __doc__.strip(), "meta": meta, "scenes": scenes}
        write_json(SCENES / f"{game}_scenes.json", doc)
        write_json(SCENES / f"{game}_order.json", order, indent=None)
        batches = make_batches(game, scenes)
        write_json(SCENES / f"{game}_batches.json", {"game": game, "min": BATCH_MIN, "max": BATCH_MAX, "batches": batches})
        kinds = collections.Counter(s["kind"] for s in scenes)
        sizes = sorted(s["count"] for s in scenes if s["kind"] in ("event", "event_block"))
        bsizes = sorted(b["count"] for b in batches)
        print(f"{game}: сцен {len(scenes)} {dict(kinds)}; размер событий: медиана {sizes[len(sizes)//2]}, макс {sizes[-1]}; "
              f"пакетов {len(batches)}, строк в пакете: медиана {bsizes[len(bsizes)//2]}, макс {bsizes[-1]}")


if __name__ == "__main__":
    main()
