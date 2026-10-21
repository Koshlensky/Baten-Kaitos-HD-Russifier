"""Категории строк: по группе ID и по форме строки."""
import re

from tools.bkloc.tags import choices, placeholders, strip_tags


def group_of(text_id):
    """Группа ID: для SCR — код скрипта, иначе ID без числовых хвостов."""
    if text_id.startswith("SCR_"):
        return "SCR_" + text_id.split("_")[1]
    return re.sub(r"(_\d+)+$", "", text_id)


def family_of(text_id):
    """Семейство групп: SCR_ + буквенная часть кода карты (SCR_KOM17 -> SCR_KOM)."""
    group = group_of(text_id)
    if group.startswith("SCR_"):
        return "SCR_" + re.sub(r"\d.*$", "", group[4:])
    return group


# Группы с фиксированной категорией (остальные считаются диалоговыми и классифицируются по форме).
FIXED = {
    # Магнусы, предметы, приёмы
    "MGN": "magnus_name", "MGNNAME": "magnus_name", "SPS": "magnus_name", "ITS": "magnus_name",
    "QMG": "magnus_name", "PIT": "item_name", "PRCS_NAME": "item_name", "EXCombo": "magnus_name",
    "MGI": "magnus_desc", "MGNINF": "magnus_desc", "PII": "item_desc", "PRCS_INFO": "item_desc",
    "EXCombo_info": "magnus_desc", "EFCLIST": "ui_battle",
    # Имена
    "NPC": "npc_name", "NPCName": "npc_name", "PCN": "party_name", "Enemy": "enemy_name",
    # Места
    "SVP": "location_name", "STG": "location_name", "STN": "location_name", "SAVE_POINT": "location_name",
    # Сюжетные сводки и кино
    "SaveStoryLine": "story_summary", "STORYOUTLINE": "story_summary", "STORYOUTLINENAME": "story_summary",
    "OMV": "movie_subtitle", "TDM": "movie_subtitle",
    # Боевые реплики
    "BattleVoice": "battle_voice",
    # Интерфейс
    "Menu_Add": "ui", "POP": "ui", "UsefulFunctions": "ui", "GRP": "ui", "GRP_BTL": "ui_battle",
    "GRP_CNP": "ui", "SYS": "ui", "CMNSTR": "ui", "MEA": "ui", "CHARA_WND": "ui", "DECKWIZ": "ui",
    "MGN_SRT": "ui", "MGN_HLD": "ui", "MGN_VIEW": "ui", "NOTE_WND": "ui", "TREE_VIEW": "ui",
    "DATA_SELECTOR": "ui", "ROOT_CPP": "ui", "CWNDQMAGNUS_CPP": "ui", "CMAGNUSHOLDER_H": "ui",
    "CDISPBTLRESULT_CPP": "ui_battle", "BC_CPP": "ui_battle", "HELP_CPP": "ui", "PERSON_CPP": "ui",
    "ITEM_CPP": "ui", "PLAYER_CPP": "ui", "CMPINIT": "ui", "DECKDATA": "ui", "CMP_DATA": "ui",
    "GATHERING_DATA": "ui", "AdvancedNewGame": "ui", "ANG": "ui", "Battle_Add": "ui_battle",
    "BTM": "ui_battle", "BFF": "ui_battle", "BMG": "ui_battle", "ABN": "ui_battle", "ELM": "ui_battle",
    "battleparam": "ui_battle", "WEAKPOINT": "ui_battle", "COLOSSEUM_ADD": "ui", "CHURCH_ADD": "ui",
    "LIB_DDMV": "ui", "DIA_DATA": "ui",
    # Справка и достижения
    "Help_info": "help", "Help_Name": "help", "HPI": "help", "HPN": "help",
    "Trophy": "trophy", "Trophy_Name": "trophy", "TRP": "trophy", "TRN": "trophy",
    # Прочее
    "MSC": "music_title", "BOOK": "encyclopedia", "LETTER": "letter", "LETTERNAME": "letter",
    "SUBQUEST": "quest_log", "FMN": "quest_log", "FMO": "quest_log", "CREDITS": "credits",
    "PRZ": "minigame", "BK2_ADD": "internal", "EVテンプレ": "internal",
}

INTERNAL_PATTERNS = [
    re.compile(r"FLAG_"),
    re.compile(r"(?i)\bdebug\b"),
    re.compile(r"(?i)(store clerk|shopkeeper|shop attendant).{0,20}(conversation|talks)"),
    re.compile(r"(?i)conversation with .{0,30}(store clerk|shopkeeper)"),
]


def form_of(en, jp=None):
    """Подтип строки по её форме (для диалоговых групп)."""
    text = strip_tags(en).strip()
    if not text:
        return "empty"
    if any(p.search(en) for p in INTERNAL_PATTERNS):
        return "internal"
    if jp is not None and en == jp and not re.search(r"[A-Za-z]{3,}\s+[a-z]{3,}", text):
        return "same_as_jp"
    if choices(en) and re.search(r"<[qQ]>", en):
        return "choice"
    if placeholders(en) or (en.startswith("<s0>") and "<v" not in en):
        return "system"
    if not re.search(r"<w|<p>|<v", en) and len(text.split()) <= 6 and not re.search(r"[.!?…]$", text):
        return "label"
    return "line"


def classify(text_id, en, jp=None):
    """Возвращает (category, form)."""
    group = group_of(text_id)
    fixed = FIXED.get(group)
    if fixed:
        return fixed, ("same_as_jp" if jp is not None and en == jp and en.strip() else "fixed")
    if group == "KYO" or group.startswith("KYO_"):
        form = form_of(en, jp)
        return ("magnus_name" if form == "label" else "dialogue"), form
    form = form_of(en, jp)
    return "dialogue", form


def flags(en):
    result = []
    if "<v" in en:
        result.append("voiced")
    if "<n0>" in en:
        result.append("mentions_spirit")
    if re.search(r"\bLv\. ?\d+\b", en) and "<w" not in en:
        result.append("internal_suspect")
    return result
