"""Построчные проверки перевода. Каждая проверка возвращает список (уровень, код, сообщение).

Уровни: error — блокирует сборку; warning — требует внимания редактора; info — справочно.
"""
import re

import yaml

from tools.bkloc import tags
from tools.bkloc.paths import BASE

LATIN_OK = re.compile(r"^(?:HP|MP|TP|RP|EX|ATK|DEF|AGL|NEW|GAME|Lv|No|OK|ID|[A-Z]{1,3}\d*|[IVX]+)$")
CYR = "А-Яа-яЁё"
LATIN = "A-Za-z"
NEWLINE_STRICT_FORMS = {"choice", "label", "system", "fixed", "same_as_jp"}
NEWLINE_STRICT_CATEGORIES = {"ui", "ui_battle", "help", "credits", "magnus_name", "item_name", "location_name",
                             "npc_name", "enemy_name", "music_title", "trophy"}
TRANSLATED = {"draft", "translated", "auto_checked", "qa_passed", "self_reviewed", "sampled_review", "edited",
              "approved", "needs_fit"}


NAME_SLOT = re.compile(r"<n\d+>|\$\$NAME_[A-Z0-9_]+\$\$")


def plain(text):
    """Текст без тегов; подстановка имени (<n0>, $$NAME_…$$) заменена словом-заглушкой,
    чтобы «Что такое, <n0>?» не читалось как «Что такое, ?»."""
    return tags.strip_tags(NAME_SLOT.sub("Имя", text))


def check_tags(r):
    en, ru = r["en"], r["ru"]
    out = []
    if tags.MASK_OPEN in ru or tags.MASK_CLOSE in ru:
        out.append(("error", "mask_left", "в тексте остался маркер ⟦…⟧"))
    if tags.structural(en) != tags.structural(ru):
        out.append(("error", "tags_structure", f"теги: {tags.structural(en)} → {tags.structural(ru)}"))
    if sorted(tags.placeholders(en)) != sorted(tags.placeholders(ru)):
        out.append(("error", "placeholders", f"подстановки: {tags.placeholders(en)} → {tags.placeholders(ru)}"))
    for close, opener in tags.PAIRED.items():
        bal_ru = len([t for t in tags.tokens(ru) if opener.match(t)]) - tags.tokens(ru).count(close)
        bal_en = len([t for t in tags.tokens(en) if opener.match(t)]) - tags.tokens(en).count(close)
        if bal_ru != bal_en:  # непарность допустима, если она есть в оригинале
            out.append(("error", "tags_unpaired", f"непарный {close}"))
    if tags.choices(en) and tags.choices(en) != tags.choices(ru):
        out.append(("error", "choices", "пункты меню выбора не совпадают"))
    return out


def check_newlines(r):
    if r["form"] in NEWLINE_STRICT_FORMS or r["category"] in NEWLINE_STRICT_CATEGORIES:
        if r["en"].count("\n") != r["ru"].count("\n"):
            return [("warning", "newlines", f"переводов строк: {r['en'].count(chr(10))} → {r['ru'].count(chr(10))}")]
    return []


SAME_OK = {"DUMMY", "DEBUG", "DEBUG Processing", "Baten Kaitos Origins", "Origins", "Baten Kaitos", "EXP", "PAC-MAN", "Pac-Land", "Pac-Mania", "o Player  o", "*QM Selection", "TO BE SCHLEPPED IN"}


def _alphabet_row(text):
    """Ряд клавиатуры экрана ввода: не менее 10 латинских букв подряд в алфавитном порядке."""
    letters = [c.lower() for c in text if c.isalpha()]
    best = run = 1
    for a, b in zip(letters, letters[1:]):
        run = run + 1 if ord(b) == ord(a) + 1 else 1
        best = max(best, run)
    return best >= 10


def check_untranslated(r):
    out = []
    en, ru = plain(r["en"]).strip(), plain(r["ru"]).strip()
    if r["form"] in ("internal", "same_as_jp") or r["category"] in ("internal", "credits"):
        return out
    if en in SAME_OK or re.fullmatch(r"ESP\d", en):  # имена собственные и сокращения, не переводятся
        return out
    if re.fullmatch(r"\w+\s*=\s*", en) or _alphabet_row(en):  # отладочные подписи и ряды клавиатуры, не переводятся
        return out
    if en and ru == en and re.search(r"[A-Za-z]{3,}", en):
        out.append(("error", "untranslated", "перевод совпадает с оригиналом"))
        return out
    latin = [w for w in re.findall(r"[A-Za-z]+", ru) if not LATIN_OK.match(w)]
    if latin:
        out.append(("warning", "latin", "латиница: " + ", ".join(sorted(set(latin))[:5])))
    return out


def check_numbers(r):
    en_nums = re.findall(r"\d+", plain(r["en"]))
    ru_nums = re.findall(r"\d+", plain(r["ru"]))
    missing = [n for n in en_nums if n not in ru_nums]
    return [("warning", "numbers", "нет чисел: " + ", ".join(missing))] if missing else []


def spaced(text):
    """Как plain, но пауза и новое облачко дают пробел — для подсчёта предложений."""
    return plain(re.sub(r"<(?:p|w\d*)>", " ", text))


def check_omission(r):
    out = []
    en, ru = spaced(r["en"]), spaced(r["ru"])
    if len(en) >= 25:
        ratio = len(ru) / max(1, len(en))
        if ratio < 0.55 or ratio > 2.2:
            out.append(("warning", "length_ratio", f"длина RU/EN = {ratio:.2f}"))
    en_s = len(re.findall(r"[.!?…]+(?=\s|$)", en))
    ru_s = len(re.findall(r"[.!?…]+(?=\s|$)", ru))
    if en_s >= 3 and ru_s < 0.6 * en_s:
        out.append(("warning", "sentences", f"предложений: {en_s} → {ru_s}"))
    for mark, code in (("?", "question_lost"), ("!", "exclamation_lost")):
        if en.count(mark) and not ru.count(mark):
            out.append(("warning", code, f"в оригинале есть «{mark}», в переводе нет"))
    return out


def check_typography(r):
    out = []
    ru = plain(r["ru"])
    ru_sep = re.sub(r"<[^>]*>|%[sd]", " ", r["ru"])  # теги и подстановки — границы слов («Анна<w><p>P.S.»)
    if re.search(rf"[{CYR}][{LATIN}]|[{LATIN}][{CYR}]", ru_sep):
        out.append(("error", "mixed_script", "латинские и русские буквы в одном слове"))
    if ru.count("  ") > plain(r["en"]).count("  "):
        out.append(("warning", "double_space", "двойной пробел (в оригинале его нет)"))
    if re.search(r"\s[,.!?;:…]", ru):
        out.append(("warning", "space_before_punct", "пробел перед знаком препинания"))
    if '"' in ru:
        out.append(("info", "straight_quotes", "прямые кавычки (D-008)"))
    if re.search(r"\(а\)|\(ла\)|/а\b", ru):
        out.append(("error", "slash_gender", "форма вида «готов(а)» запрещена"))
    return out


def check_register(r):
    ru = " " + plain(r["ru"]).lower() + " "
    to_group = r.get("addressee_gender") == "group" or (r.get("addressee") or {}).get("id") == "group"
    if r["register"] == "ty" and not to_group and re.search(r"\b(вы|вас|вам|вами|ваш\w*)\b", ru):
        return [("warning", "register", "в паре на «ты» встречается «вы» (проверить: не множественное ли число)")]
    if r["register"] == "vy" and re.search(r"\b(ты|тебя|тебе|тобой|твой|твоя|твоё|твои\w*)\b", ru):
        return [("warning", "register", "в паре на «вы» встречается «ты»")]
    return []


_GLOSSARY = None


def glossary():
    global _GLOSSARY
    if _GLOSSARY is None:
        data = yaml.safe_load((BASE / "glossary" / "glossary.yaml").read_text(encoding="utf-8")) or {}
        _GLOSSARY = [e for e in data.get("entries") or [] if e.get("status") == "approved" and e.get("ru")]
    return _GLOSSARY


def stem(term):
    """Основа без конечной гласной для записей без форм склонения: «Диадема» находит «Диадемы»."""
    word = term.lower()
    return word[:-1] if len(word) > 4 and word[-1] in "аяоеьйы" else word


def check_glossary(r):
    out = []
    en, ru = plain(r["en"]), plain(r["ru"]).lower()
    for e in glossary():
        if re.search(r"\b" + re.escape(e["en"]) + r"\b", en, re.I):
            allowed = [f.lower() for f in (e.get("allowed") or list((e.get("forms") or {}).values()) or [stem(e["ru"])])]
            if not any(a in ru for a in allowed):
                out.append(("warning", "glossary_missing", f"«{e['en']}» → ожидается «{e['ru']}» (или форма)"))
            for bad in e.get("forbidden") or []:
                if bad.lower() in ru:
                    out.append(("error", "glossary_forbidden", f"запрещённый вариант «{bad}» для «{e['en']}»"))
    return out


def _distance(a, b):
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


_NAMES = None


def approved_names():
    """Все утверждённые формы имён персонажей и мест из глоссария."""
    global _NAMES
    if _NAMES is None:
        names = set()
        for e in glossary():
            if e.get("category") in ("character", "location", "organization", "enemy"):
                names.add(e["ru"])
                names.update((e.get("forms") or {}).values())
                names.update(e.get("allowed") or [])
        _NAMES = {n for n in names if n}
    return _NAMES


_MORPH = None


def dictionary_word(word):
    """Обычное слово русского словаря (pymorphy3): «Верно» — не искажённое «Верус»."""
    global _MORPH
    if _MORPH is None:
        import pymorphy3
        _MORPH = pymorphy3.MorphAnalyzer()
    return _MORPH.word_is_known(word.lower())


def check_names(r):
    """Слово с заглавной буквы, похожее на утверждённое имя, но не совпадающее с ним."""
    names = approved_names()
    if not names:
        return []
    out = []
    for word in set(re.findall(rf"\b[А-ЯЁ][{CYR}]+\b", plain(r["ru"]))):
        if word in names or dictionary_word(word):
            continue
        close = [n for n in names if abs(len(n) - len(word)) <= 2 and 0 < _distance(word.lower(), n.lower()) <= 2]
        if close and len(word) >= 4:
            out.append(("warning", "name_spelling", f"«{word}» похоже на «{close[0]}»"))
    return out


RU_INTENSIFIERS = re.compile(r"\b(совсем|ужасно|страшно|жутко|сразу|поскорее|совершенно|абсолютно|невероятно|"
                             r"давно|аж|прямо|до чего|ничуточки|напрочь|вовсю|чертовски)\b", re.I)
EN_INTENSIFIERS = re.compile(r"\b(very|so|really|totally|completely|absolutely|right away|immediately|at once|quite|too|"
                             r"such|extremely|incredibly|terribly|awfully|soon|already|long|hell|damn|whole|all)\b", re.I)
EN_MODALS = re.compile(r"\b(might|may|could|perhaps|maybe|probably|should|would|must|seems?|likely|possibly)\b", re.I)
RU_MODALS = re.compile(r"(мож|мог|наверн|возможн|кажет|похож|долж|стоит|следует|\bбы\b|\bб\b|видим|вероятн|пожалуй|"
                       r"небось|вряд ли|надо|нужн|придётся|получит|сумею|смогу|сможе|верно)", re.I)


def check_intensifier(r):
    """Подсказка редактору: в русском усилитель, в английском усиления нет (D-026)."""
    ru_hit = RU_INTENSIFIERS.search(plain(r["ru"]))
    if ru_hit and not EN_INTENSIFIERS.search(plain(r["en"])):
        return [("info", "intensifier", f"усилитель «{ru_hit.group(1)}» без опоры в оригинале?")]
    return []


def check_modality(r):
    """Подсказка редактору: в оригинале модальность (might, could, should…), в переводе её не видно."""
    en_hit = EN_MODALS.search(plain(r["en"]))
    if en_hit and not RU_MODALS.search(plain(r["ru"])):
        return [("info", "modality", f"в оригинале «{en_hit.group(1)}» — передана ли модальность?")]
    return []


LINE_CHECKS = [check_tags, check_newlines, check_untranslated, check_numbers, check_omission, check_typography,
               check_register, check_glossary, check_names, check_intensifier, check_modality]
EDITOR_HINTS = ("intensifier", "modality", "question_lost", "length_ratio", "sentences")


def run_line_checks(r):
    if not r.get("ru"):
        if r.get("status") in TRANSLATED:
            return [("error", "empty_ru", f"статус {r['status']} без перевода")]
        return []
    issues = []
    for check in LINE_CHECKS:
        issues.extend(check(r))
    return waive(r, issues)


def waive(r, issues):
    """Предупреждения, снятые редактором после проверки: qa_flags содержит waive:<код>.
    Ошибки (error) снять нельзя."""
    waived = {f.split(":", 1)[1] for f in r.get("qa_flags", []) if f.startswith("waive:")}
    return [i for i in issues if not (i[0] != "error" and i[1] in waived)]
