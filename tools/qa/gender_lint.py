"""Проверка грамматического рода в русском переводе (pymorphy3).

Находит в клаузах с подлежащим «я» и «ты» формы с родом: прошедшее время,
краткие прилагательные и причастия, «сам/сама», «один/одна» — и сравнивает
с разметкой строки (speaker_gender, addressee_gender). Подлежащее, опущенное
во второй части предложения, наследуется из предыдущей. В меню выбора
(реплики духа) клауза без подлежащего считается первым лицом.

Это помощник редактора, а не замена ему: возможны ложные срабатывания на
омонимах и пропуски в сложных конструкциях.
"""
import re
import sys

import pymorphy3

from tools.bkloc import tags

MORPH = pymorphy3.MorphAnalyzer()
WORD = re.compile(r"[А-Яа-яЁё]+(?:-[А-Яа-яЁё]+)?")
CLAUSE_SPLIT = re.compile(r"[,;:—–]|\b(?:и|а|но|что|чтобы|потому что|когда|если)\b", re.I)
SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])\s+|\n")
SUBJECTS = {"я": "1", "ты": "2", "мы": "pl", "вы": "pl", "они": "pl", "он": "3", "она": "3", "оно": "3"}
NOUN_NACHALA_PREV = {"самого", "с", "до", "от", "из", "в", "до", "самом"}
THIRD_PRON = {"кто", "кто-то", "кто-нибудь", "кое-кто", "никто", "некто", "каждый", "любой", "всякий"}
PRONOUN_ADJ = {"сам": "masc", "сама": "femn", "один": "masc", "одна": "femn"}
GENDER = {"masc": "m", "femn": "f"}


def gendered(word, verbs_only=False):
    lower = word.lower()
    if verbs_only:
        for parse in MORPH.parse(lower)[:2]:
            if parse.score >= 0.2 and "VERB" in parse.tag and "past" in parse.tag and "plur" not in parse.tag:
                for gram, g in GENDER.items():
                    if gram in parse.tag:
                        return g
        return None
    if lower in PRONOUN_ADJ:
        return GENDER[PRONOUN_ADJ[lower]]
    for parse in MORPH.parse(lower)[:2]:
        if parse.score < 0.2:
            continue
        t = parse.tag
        if ("VERB" in t and "past" in t) or "ADJS" in t or "PRTS" in t:
            if "plur" in t:
                return None
            for gram, g in GENDER.items():
                if gram in t:
                    return g
            return None
    return None


PAGE_TAG = re.compile(r"^<(?:p|w\d*)>$")


def sentences_text(ru):
    """Текст без тегов: паузы и страницы (<w>, <p>) — граница предложения, прочие теги — пробел.
    Иначе «…грустная…<w><p>Я тебя…» склеивается в одно предложение с чужим подлежащим."""
    return tags.TOKEN.sub(lambda m: "\n" if PAGE_TAG.match(m.group(0)) else " ", ru)


def noun_subject(word):
    """Существительное в именительном падеже — своё подлежащее клаузы («…, что муж вернулся»)."""
    parse = MORPH.parse(word.lower())[0]
    return parse.score >= 0.4 and "NOUN" in parse.tag and "nomn" in parse.tag


def analyze(ru, first_person_default=False):
    """Список (лицо, род, слово) для форм с родом в клаузах с «я»/«ты»."""
    found = []
    for sentence in SENTENCE_SPLIT.split(sentences_text(ru)):
        subject = None
        for clause in CLAUSE_SPLIT.split(sentence):
            if not clause or not clause.strip():
                continue
            words = WORD.findall(clause)
            own = next((SUBJECTS[w.lower()] for w in words if w.lower() in SUBJECTS), None)
            if own:
                subject = own
                person = own
            elif any(noun_subject(w) or w.lower() in THIRD_PRON for w in words):
                person = "3"  # «кто-то выбрался», «те, кто убил» — мужской род по правилу, не род говорящего
            else:
                person = subject or ("1" if first_person_default else None)
            if person not in ("1", "2"):
                continue
            implicit = not own and not subject and first_person_default == "verbs"
            for idx, w in enumerate(words):
                if w.lower() in SUBJECTS:
                    continue
                if idx > 0 and w[0].isupper() and not w.isupper():
                    continue  # имя собственное внутри клаузы («квестора Веруса»), не причастие
                if w.lower() == "начала" and idx > 0 and words[idx - 1].lower() in NOUN_NACHALA_PREV:
                    continue  # «с самого начала» — существительное
                if w.lower().startswith("нужн"):
                    continue  # «нужна помощь» — сказуемое при предмете, не род говорящего
                g = gendered(w, verbs_only=implicit)
                if g:
                    found.append((person, g, w))
    return found


_CAST = None


def cast_ids():
    """id персонажей с карточками: их реплики не считаются репликами безымянных NPC."""
    global _CAST
    if _CAST is None:
        try:
            from tools.bkloc import pipeline
            _CAST = set(pipeline.character_cards())
        except Exception:
            _CAST = set()
    return _CAST


def lint(record):
    """Проблемы рода для одной записи матрицы."""
    ru = record.get("ru", "")
    if not ru:
        return []
    spirit_line = (record.get("variant") or {}).get("kind") == "spirit_gender" or record.get("form") == "choice"
    # D-036: реплика без явного подлежащего («Не сомневался, что получится») у говорящего с неизвестным родом — первое лицо
    unknown_speaker = (record.get("speaker_gender") in ("unknown", None, "") and record.get("form") != "choice"
                       and (record.get("speaker") or {}).get("id") not in cast_ids())
    issues = []
    for person, g, word in analyze(ru, first_person_default=True if spirit_line else ("verbs" if unknown_speaker else False)):
        expected = record.get("speaker_gender") if person == "1" else record.get("addressee_gender")
        role = "говорящего" if person == "1" else "адресата"
        if expected in ("m", "f"):
            if g != expected:
                issues.append(("error", "gender_mismatch", f"«{word}» — {'муж.' if g == 'm' else 'жен.'} род, а у {role} {expected}"))
        elif expected == "n":
            issues.append(("warning", "gender_neutral_expected", f"«{word}» имеет род, а для {role} выбран нейтральный вариант"))
        else:
            issues.append(("warning", "gender_unverified", f"«{word}» имеет род, а род {role} не установлен"))
        if person == "2" and "<n0>" in record.get("en", ""):
            issues.append(("warning", "spirit_gendered_address", f"«{word}»: род в обращении к духу-хранителю (D-002)"))
    return issues


def self_test():
    cases = [
        ({"ru": "Я устала.", "speaker_gender": "m"}, {"gender_mismatch"}),
        ({"ru": "Я готова идти.", "speaker_gender": "f"}, set()),
        ({"ru": "Ты пришёл вовремя.", "addressee_gender": "f"}, {"gender_mismatch"}),
        ({"ru": "Я был там.", "speaker_gender": "unknown"}, {"gender_unverified"}),
        ({"ru": "Мне пора.", "speaker_gender": "unknown"}, set()),
        ({"ru": "Я не слышала, чтобы кто-то выбрался отсюда.", "speaker_gender": "f"}, set()),
        ({"ru": "Я была одной из тех, кто убил его.", "speaker_gender": "f"}, set()),
        ({"ru": "Я прибыл по поручению квестора Веруса.", "speaker_gender": "m"}, set()),
        ({"ru": "Я с самого начала всё решил.", "speaker_gender": "m"}, set()),
        ({"ru": "Всё готово.", "speaker_gender": "m"}, set()),
        ({"ru": "Я думал, что ты ушла.", "speaker_gender": "m", "addressee_gender": "f"}, set()),
        ({"ru": "Я думал, что ты ушла.", "speaker_gender": "m", "addressee_gender": "m"}, {"gender_mismatch"}),
        ({"ru": "Согласен.", "form": "choice", "variant": {"kind": "spirit_gender", "gender": "f"}, "speaker_gender": "f"}, {"gender_mismatch"}),
        ({"ru": "Я согласна.", "form": "choice", "speaker_gender": "f"}, set()),
        ({"ru": "Ты видел это, <n0>?", "en": "Did you see that, <n0>?", "addressee_gender": "unknown"}, {"gender_unverified", "spirit_gendered_address"}),
        ({"ru": "Мы пришли и сразу ушли.", "speaker_gender": "f"}, set()),
        ({"ru": "Ты такая грустная…<w><p>Я тебя чем-то обидел?<w>", "speaker_gender": "m", "addressee_gender": "f"}, set()),
        ({"ru": "Я просто рада, что муж вернулся ко мне.", "speaker_gender": "f"}, set()),
        ({"ru": "Я рада, что пришёл.", "speaker_gender": "f"}, {"gender_mismatch"}),
        ({"ru": "Не сомневался, что у вас получится.", "speaker_gender": "unknown"}, {"gender_unverified"}),
        ({"ru": "Не сомневался, что у вас получится.", "speaker_gender": "m"}, set()),
        ({"ru": "Было ясно, что у вас получится.", "speaker_gender": "unknown"}, set()),
        ({"ru": "Мы ждали вас.", "speaker_gender": "unknown"}, set()),
    ]
    failed = 0
    for record, expected in cases:
        record.setdefault("en", "")
        got = {code for _sev, code, _msg in lint(record)}
        ok = got == expected
        failed += not ok
        print(("ok  " if ok else "FAIL"), record["ru"], "→", sorted(got), "" if ok else f"(ожидалось {sorted(expected)})")
    print(f"самотест: {len(cases) - failed}/{len(cases)}")
    return failed


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    return 1 if self_test() else 0


if __name__ == "__main__":
    raise SystemExit(main())
