"""Разбор управляющих тегов и плейсхолдеров в строках Baten Kaitos HD."""
import re

PLACEHOLDER_RE = r"%%|%[-+#0]*\d*(?:\.\d+)?[sdfxX]"
VARIABLE_RE = r"\$\$[A-Z0-9_]+\$\$"
TOKEN = re.compile(r"<[^<>\n]{1,40}>|" + PLACEHOLDER_RE + "|" + VARIABLE_RE)
PLACEHOLDER = re.compile(PLACEHOLDER_RE + "|" + VARIABLE_RE)

# Описание тегов. status: confirmed — следует из структуры текста однозначно,
# likely — согласуется со всеми примерами, hypothesis — назначение не установлено.
TAG_INFO = {
    "<w>": ("Ожидание нажатия: конец облачка/окна", "confirmed"),
    "<wN>": ("Автопереход через N мс", "likely"),
    "<w$$NAME$$>": ("Автопереход по именованной константе времени", "likely"),
    "<wv>": ("Ожидание конца озвучки", "hypothesis"),
    "<wvN>": ("Ожидание конца озвучки с задержкой N мс", "hypothesis"),
    "<p>": ("Новая страница в том же окне", "confirmed"),
    "<v>": ("Привязка реплики к следующему клипу озвучки", "hypothesis"),
    "<vN>": ("Привязка к клипу озвучки N", "hypothesis"),
    "<v-N>": ("Сброс/отключение привязки озвучки", "hypothesis"),
    "<nN>": ("Подстановка имени: <n0> — имя духа-хранителя, введённое игроком", "confirmed"),
    "<aN>": ("Пункт меню выбора с возвращаемым значением N", "confirmed"),
    "<a%d>": ("Пункт меню, номер подставляется кодом", "confirmed"),
    "<q>": ("Конец меню выбора (вариант 1)", "likely"),
    "<Q>": ("Конец меню выбора (вариант 2)", "likely"),
    "<s>": ("Возврат обычной скорости вывода текста", "likely"),
    "<sN>": ("Скорость вывода текста: <s0> мгновенно, большие N — медленнее", "likely"),
    "<cN>": ("Начало цветного выделения N", "confirmed"),
    "<c>": ("Конец цветного выделения", "confirmed"),
    "<BN>": ("Начало обозначения кнопки контроллера стиля N", "confirmed"),
    "</B>": ("Конец обозначения кнопки", "confirmed"),
    "<kN>": ("Неизвестно (встречается в песнях и отдельных сценах)", "hypothesis"),
    "<eN>": ("Режим окна: <e1> в начале писем и титров, <e0>/<e> в конце", "hypothesis"),
    "<e>": ("Конец режима окна", "hypothesis"),
    "<fN>": ("Вероятно, шрифт или стиль", "hypothesis"),
    "<tN>": ("Позиция/табуляция по X в таблицах", "hypothesis"),
    "<yN>": ("Позиция по Y в таблицах", "hypothesis"),
    "<t>": ("Сброс позиции по X", "hypothesis"),
    "<y>": ("Сброс позиции по Y", "hypothesis"),
    "<rN>": ("Неизвестно (реплики NPC в BK2)", "hypothesis"),
    "%s": ("Подстановка строки кодом игры", "confirmed"),
    "%d": ("Подстановка числа кодом игры", "confirmed"),
    "%0Nd": ("Подстановка числа с ведущими нулями", "confirmed"),
    "%Nd": ("Подстановка числа с шириной поля N", "confirmed"),
    "%Ns": ("Подстановка строки с шириной поля N", "confirmed"),
    "%N.Nf": ("Подстановка дробного числа (отладка)", "confirmed"),
    "%0NX": ("Шестнадцатеричное число (отладка)", "confirmed"),
    "%f": ("Подстановка дробного числа", "confirmed"),
    "%%": ("Знак процента в форматной строке", "confirmed"),
    "$$NAME$$": ("Переменная движка: $$NAME_PLAYER$$, $$NAME_KALAS$$ и т.п. — имя подставляет игра", "confirmed"),
}

# Теги, порядок и количество которых обязаны совпадать с оригиналом.
STRUCTURAL = re.compile(
    r"^(<w.*>|<p>|<v-?\d*>|<a(\d+|%d)>|<[qQ]>|<k\d+>|<e\d*>|<f\d+>|<t\d*>|<y\d*>|<r\d+>|<s\d*>|<n\d+>|"
    + PLACEHOLDER_RE + "|" + VARIABLE_RE + ")$"
)
PAIRED = {"<c>": re.compile(r"^<c\d+>$"), "</B>": re.compile(r"^<B\d+>$")}


def tokens(text):
    return TOKEN.findall(text)


def shape(token):
    if token.startswith("<w$$"):
        return "<w$$NAME$$>"
    if token.startswith("$$"):
        return "$$NAME$$"
    if token in ("<a%d>", "%%", "%s", "%d", "%f"):
        return token
    if re.fullmatch(r"%0\d+d", token):
        return "%0Nd"
    if re.fullmatch(r"%0?\d+X", token):
        return "%0NX"
    if re.fullmatch(r"%\d+\.\d+f", token):
        return "%N.Nf"
    if re.fullmatch(r"<v-\d+>", token):
        return "<v-N>"
    return re.sub(r"\d+", "N", token)


def structural(text):
    return [t for t in tokens(text) if STRUCTURAL.match(t)]


def placeholders(text):
    return PLACEHOLDER.findall(text)


def strip_tags(text):
    return TOKEN.sub("", text)


def pages(text):
    return text.count("<p>") + 1


def choices(text):
    return re.findall(r"<a(?:\d+|%d)>", text)


def bubbles(text):
    """Куски текста между ожиданиями <w...> — то, что игрок видит в одном облачке."""
    return [part for part in re.split(r"<w[^>]*>|<p>", text) if strip_tags(part).strip()]


MASK_OPEN, MASK_CLOSE = "⟦", "⟧"


def mask(text):
    """Заменяет теги защищёнными маркерами для передачи модели."""
    return TOKEN.sub(lambda m: MASK_OPEN + m.group(0).strip("<>") + MASK_CLOSE, text)


def unmask(text):
    def restore(match):
        inner = match.group(1)
        return inner if inner.startswith(("%", "$$")) else "<" + inner + ">"
    return re.sub(re.escape(MASK_OPEN) + r"([^" + MASK_CLOSE + r"]{1,40})" + re.escape(MASK_CLOSE), restore, text)
