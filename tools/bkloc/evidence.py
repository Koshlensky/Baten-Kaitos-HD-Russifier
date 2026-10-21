"""Сбор улик о говорящем, роде и адресате реплики.

Источники: японский текст (местоимения, окончания), французский/испанский/
итальянский/немецкий (только BK1; родовые формы 1-го и 2-го лица), английский
(обращения по имени, самоназвания), японские обращения по имени.

Скрипт ничего не назначает: он выдаёт улики с весами и сводку «м/ж/конфликт/нет».
Разметчик (человек или модель) принимает решение с учётом сцены.
"""
import collections
import json
import re
import sys

from tools.bkloc import categories, tags
from tools.bkloc.paths import BASE, SOURCE, read_jsonl, write_jsonl

# --- Японский ------------------------------------------------------------------
JP_PRONOUNS = [
    (r"俺|オレ|おれ", "m", 0.9, "俺"),
    (r"僕|ボク|ぼく", "m", 0.7, "僕"),
    (r"わし|ワシ", "m", 0.6, "わし (старик/архаика)"),
    (r"拙者|おいら|オイラ", "m", 0.8, "拙者/おいら"),
    (r"あたし|アタシ|あたい", "f", 0.9, "あたし"),
    (r"わたくし", "f", 0.4, "わたくし (вежливое, чаще жен.)"),
    (r"(?<!わたく)わたし", "f", 0.25, "わたし (часто жен.)"),
    (r"あなた", "f", 0.25, "あなた в обращении (часто жен.)"),
    (r"きみ|(?<![ァ-ヴー])君(?=[はがをのにもと、。！？])", "m", 0.25, "きみ в обращении (часто муж.)"),
    (r"おまえ|お前", "m", 0.35, "おまえ в обращении (чаще муж.)"),
]
END = r"(?=[。、！？!?…ー〜～\s」』]|$)"
KANA_BEFORE = r"(?<=[ぁ-んァ-ヶ一-龠ー])"
JP_ENDINGS = [
    (KANA_BEFORE + r"(?:わ|わよ|わね|のよ|かしら|ですわ|なのよ|だわ)" + END, "f", 0.6, "жен. окончание"),
    (KANA_BEFORE + r"(?:ぜ|だぜ|ぞ|だぞ|じゃねえ|だろうが)" + END, "m", 0.6, "муж. окончание"),
]
# Стилевые улики без рода: помогают узнать персонажа (например, архаичную речь махины Guillo).
JP_STYLES = [
    (r"(?:ておる|でおる|おった|ておらん|ておらぬ|らぬか|ぬのか|よかろう|なかろう|じゃのう|じゃろう|わしは|わしの|わしが|わしも|"
     r"(?<=[ぁ-んァ-ヶ一-龠ー])じゃ(?=[。！？!?…\s」』]|$))", "archaic", 0.6, "архаичная речь (わし, ～ておる, ～ぬ, ～じゃ)"),
    (r"(?:です|ます|ました|でした|ません|ください)" + END, "polite", 0.4, "вежливая речь (です/ます)"),
]

# --- Европейские языки (только BK1) -------------------------------------------
FR_FIRST = re.compile(r"\bje (?:suis|me suis|serai|serais|étais|fus|reste|resterai)\s+"
                      r"(?:(?:très|si|tellement|vraiment|bien|tout|toute|déjà|encore|pas|plus|trop|enfin|ravi de|bien)\s+)*([a-zàâçéèêëîïôûùüÿœ-]{3,})", re.I)
FR_SECOND = re.compile(r"\btu (?:es|étais|seras|serais|t'es|restes)\s+(?:(?:très|si|tellement|vraiment|bien|toute?|déjà|encore|pas|plus|trop)\s+)*([a-zàâçéèêëîïôûùüÿœ-]{3,})", re.I)
ES_FIRST = re.compile(r"\b(?:estoy|soy|estaba|era|seré|estaré|me siento|me quedé|quedé)\s+(?:(?:muy|tan|bastante|un poco|demasiado|algo)\s+)*([a-záéíóúñ]{3,})", re.I)
ES_SECOND = re.compile(r"\b(?:estás|eres|estabas|eras)\s+(?:(?:muy|tan|bastante|un poco|demasiado)\s+)*([a-záéíóúñ]{3,})", re.I)
IT_FIRST = re.compile(r"\b(?:sono|ero|sarò|sarei|mi sento|mi sono|resto)\s+(?:(?:così|molto|davvero|proprio|già|ancora|troppo|tanto|un po')\s+)*([a-zàèéìòù]{3,})", re.I)
IT_SECOND = re.compile(r"\b(?:sei|eri|sarai|saresti)\s+(?:(?:così|molto|davvero|proprio|già|ancora|troppo)\s+)*([a-zàèéìòù]{3,})", re.I)
DE_FIRST = re.compile(r"\bich bin (?:die|eine|deine|seine|ihre|keine)?\s*([A-ZÄÖÜ][a-zäöüß]+in)\b")

FR_FEM = re.compile(r"(?:ée|ie|ue|ise|ite|ète|euse|ive|elle|enne|ère|ante|ente|aine|ine|ète|ôte|tte|sse|rte|ure|ule|ée?s?)$")
FR_MASC_ONLY = re.compile(r"(?:é|i|u|is|it|et|eux|if|el|en|er|ant|ent|ain|in|ot|ort|ur|ul|ier)$")
FR_NEUTRAL = {"libre", "triste", "malade", "seule", "sûre", "désolée", "capable", "faible", "riche", "pauvre", "jeune",
              "même", "sage", "calme", "digne", "utile", "inutile", "possible", "impossible", "honnête", "stupide"}
FR_NOT_ADJ = {"là", "ici", "pas", "plus", "en", "dans", "avec", "sans", "pour", "par", "sur", "sous", "chez", "comme",
              "une", "un", "le", "la", "les", "des", "ton", "ta", "ma", "mon", "votre", "son", "sa", "ce", "cette",
              "toujours", "jamais", "aussi", "encore", "venu", "allé", "parti"}
ES_NOT_ADJ = {"aquí", "allí", "allá", "bien", "mal", "para", "por", "con", "sin", "una", "uno", "como", "todo", "toda",
              "nada", "algo", "muy", "más", "menos", "solo", "sola", "esto", "esta", "este", "eso", "esa", "ese", "tuya",
              "tuyo", "mía", "mío", "suya", "suyo", "nuestra", "nuestro", "otra", "otro", "cada", "casa", "hora", "vida",
              "persona", "gente", "idea", "nunca", "siempre", "ahora", "ella", "ello", "nosotros", "nosotras", "ustedes"}
IT_NOT_ADJ = {"qui", "qua", "una", "uno", "come", "solo", "sola", "questa", "questo", "quella", "quello", "tutto",
              "tutta", "ancora", "molto", "poco", "stata", "stato", "proprio", "vero", "nessuna", "nessuno", "altra",
              "altro", "persona", "casa", "ora", "vita", "idea", "ella"}


def _fr_gender(word):
    w = word.lower()
    if w in FR_NOT_ADJ or w in FR_NEUTRAL or w.endswith("e") and w[:-1] + "" in FR_NOT_ADJ:
        return None
    if re.search(r"(?:ée|ie|ue|ise|ite|euse|ive|elle|enne|ère|ante|ente|aine|tte)s?$", w):
        return "f"
    if re.search(r"(?:é|i|u|is|it|eux|if|el|en|er|ant|ent|ain|ier|ot|et)s?$", w) and not w.endswith("e"):
        return "m"
    return None


def _oa_gender(word, stop):
    w = word.lower()
    if w in stop or len(w) < 4:
        return None
    if re.search(r"(?:ado|ido|oso|ivo|ento|ano|ero|uro|isto|esto|ado|ito|ato|uto|ico|anco|enco|io)$", w):
        return "m"
    if re.search(r"(?:ada|ida|osa|iva|enta|ana|era|ura|ista|esta|ita|ata|uta|ica|anca|enca|ia)$", w):
        return "f"
    return None


def jp_evidence(jp):
    text = tags.strip_tags(jp)
    found = []
    for pattern, gender, weight, label in JP_PRONOUNS:
        if re.search(pattern, text):
            found.append({"src": "jp", "role": "speaker", "gender": gender, "weight": weight, "cue": label})
    styles = set()
    for pattern, style, weight, label in JP_STYLES:
        if re.search(pattern, text):
            styles.add(style)
            found.append({"src": "jp", "role": "speaker", "style": style, "weight": weight, "cue": label})
    for pattern, gender, weight, label in JP_ENDINGS:
        hits = re.findall(pattern, text)
        if hits:
            # В архаичной речи частицы ぞ/じゃ не говорят о поле (например, Guillo).
            if gender == "m" and "archaic" in styles:
                weight = 0.15
            found.append({"src": "jp", "role": "speaker", "gender": gender, "weight": min(0.8, weight + 0.1 * (len(hits) - 1)),
                          "cue": f"{label}: {hits[0]}"})
    return found


def euro_evidence(row):
    found = []
    specs = [
        ("fr", FR_FIRST, "speaker", _fr_gender), ("fr", FR_SECOND, "addressee", _fr_gender),
        ("es", ES_FIRST, "speaker", lambda w: _oa_gender(w, ES_NOT_ADJ)),
        ("es", ES_SECOND, "addressee", lambda w: _oa_gender(w, ES_NOT_ADJ)),
        ("it", IT_FIRST, "speaker", lambda w: _oa_gender(w, IT_NOT_ADJ)),
        ("it", IT_SECOND, "addressee", lambda w: _oa_gender(w, IT_NOT_ADJ)),
    ]
    for lang, pattern, role, judge in specs:
        text = tags.strip_tags(row.get(lang, ""))
        for match in pattern.finditer(text):
            gender = judge(match.group(1))
            if gender:
                found.append({"src": lang, "role": role, "gender": gender, "weight": 0.5,
                              "cue": match.group(0)[:60]})
    de = tags.strip_tags(row.get("de", ""))
    for match in DE_FIRST.finditer(de):
        found.append({"src": "de", "role": "speaker", "gender": "f", "weight": 0.6, "cue": match.group(0)[:60]})
    return found


def name_evidence(row, names_en, jp_to_en):
    found = []
    en = tags.strip_tags(row["en"])
    for match in re.finditer(r"(?:^|[.!?…]\s+|\n)([A-Z][a-z]+(?:-[A-Z][a-z]+)?)[,!?](?=\s|$)|,\s*([A-Z][a-z]+)[!?.…]*(?=\s*$|\n)", en):
        name = match.group(1) or match.group(2)
        if name in names_en:
            found.append({"src": "en", "role": "addressee", "name": name, "weight": 0.7, "cue": f"обращение: {name}"})
    for match in re.finditer(r"\b(?:I'm|I am|My name is|name's|It's me,)\s+(?:just\s+|only\s+|the\s+great\s+|the\s+)?([A-Z][a-z]+)\b", en, re.I):
        if match.group(1) in names_en:
            found.append({"src": "en", "role": "speaker", "name": match.group(1), "weight": 0.8, "cue": match.group(0)})
    if "<n0>" in row["en"]:
        found.append({"src": "en", "role": "addressee", "name": "GUARDIAN_SPIRIT", "weight": 0.6, "cue": "<n0>"})
    jp = tags.strip_tags(row.get("jp", ""))
    for match in re.finditer(r"(?:^|[。！？\n])\s*([ァ-ヴー・]{2,})(?:さま|様|さん|殿)?[、！？]", jp):
        en_name = jp_to_en.get(match.group(1))
        if en_name:
            found.append({"src": "jp", "role": "addressee", "name": en_name, "weight": 0.6, "cue": f"обращение JP: {match.group(1)}"})
    return found


def summarize(items, role):
    score = collections.Counter()
    for item in items:
        if item["role"] == role and "gender" in item:
            score[item["gender"]] += item["weight"]
    if not score:
        return {"verdict": "none", "m": 0.0, "f": 0.0}
    m, f = round(score["m"], 2), round(score["f"], 2)
    if m and f and min(m, f) >= 0.5 * max(m, f) and min(m, f) >= 0.25:
        return {"verdict": "conflict", "m": m, "f": f}
    lead, value = ("m", m) if m >= f else ("f", f)
    if value >= 0.5:
        verdict = lead
    elif value >= 0.25:
        verdict = "weak_" + lead
    else:
        verdict = "none"
    return {"verdict": verdict, "m": m, "f": f}


def candidates(items, role):
    names = collections.Counter()
    for item in items:
        if item["role"] == role and "name" in item:
            names[item["name"]] += item["weight"]
    return [name for name, _ in names.most_common()]


def name_maps(rows):
    """EN-имена из списков игры и соответствие японских написаний английским."""
    names_en, jp_to_en = set(), {}
    for row in rows:
        cat, _ = categories.classify(row["id"], row["en"], row.get("jp"))
        if cat in ("npc_name", "party_name"):
            en = row["en"].strip()
            jp = re.sub(r"\s+", "", row.get("jp", ""))
            if re.fullmatch(r"[A-Z][a-z]+(?:-[A-Z][a-z]+)?", en):
                names_en.add(en)
                if re.fullmatch(r"[ァ-ヴー・]{2,}", jp):
                    jp_to_en[jp] = en
    return names_en, jp_to_en


def collect(game):
    rows = read_jsonl(SOURCE / game / "lines.jsonl")
    names_en, jp_to_en = name_maps(rows)
    out = []
    for row in rows:
        cat, form = categories.classify(row["id"], row["en"], row.get("jp"))
        if cat not in ("dialogue", "battle_voice", "movie_subtitle", "letter", "story_summary"):
            continue
        items = jp_evidence(row.get("jp", "")) + euro_evidence(row) + name_evidence(row, names_en, jp_to_en)
        if not items:
            continue
        styles = sorted({i["style"] for i in items if "style" in i})
        out.append({"id": row["id"], "form": form, "speaker_gender": summarize(items, "speaker"),
                    "addressee_gender": summarize(items, "addressee"), "speech_style": styles,
                    "speaker_candidates": candidates(items, "speaker"),
                    "addressee_candidates": candidates(items, "addressee"), "items": items})
    return out, names_en, jp_to_en


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    for game in ("bk1", "bk2"):
        out, names_en, jp_to_en = collect(game)
        write_jsonl(BASE / "evidence" / f"{game}.jsonl", out)
        verdicts = collections.Counter(x["speaker_gender"]["verdict"] for x in out)
        addressees = sum(1 for x in out if any(i["role"] == "addressee" and "name" in i for i in x["items"]))
        sources = collections.Counter(i["src"] for x in out for i in x["items"])
        print(f"{game}: строк с уликами {len(out)}; род говорящего {dict(verdicts)}; с адресатом по имени {addressees}; "
              f"источники {dict(sources)}; имён EN {len(names_en)}, JP→EN {len(jp_to_en)}")


if __name__ == "__main__":
    main()
