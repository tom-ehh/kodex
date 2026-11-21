"""Decides whether a PDF page's text layer is usable or needs OCR."""

import re

_LETTER = re.compile(r"[A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż]")
_SPACE = re.compile(r"\s")
_FILL = re.compile(
    r"[.\u2026_\-–—:,;/|()\[\]0-9☐□■]"
)  # fill-in lines, punctuation, digits
# Private-use / replacement chars and (cid:N) markers show a broken font encoding.
_BROKEN = re.compile(r"[-�]|\(cid:\d+\)")
_WORD = re.compile(r"[A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż]{2,}")
_POLISH = re.compile(r"[ąćęłńóśźżĄĆĘŁŃÓŚŹŻ]")
_COMMON = frozenset(
    [
        "i",
        "w",
        "na",
        "z",
        "do",
        "nie",
        "się",
        "że",
        "o",
        "od",
        "lub",
        "oraz",
        "dla",
        "jest",
        "data",
        "podpis",
        "imię",
        "nazwisko",
        "adres",
    ]
)

MIN_CHARS = 40


def needs_ocr(text: str) -> bool:
    """True if the text layer is missing or garbled."""
    stripped = _SPACE.sub("", text)
    if len(stripped) < MIN_CHARS:
        return True
    if len(_BROKEN.findall(text)) > 0.02 * len(stripped):
        return True
    content = _FILL.sub("", stripped)
    if len(content) < MIN_CHARS // 2:
        return True
    letters = len(_LETTER.findall(content))
    if letters < 0.7 * len(content):
        return True
    words = [w.lower() for w in _WORD.findall(text)]
    if len(words) >= 30:
        # Polish prose has common short words and diacritics; garbled layers have neither.
        common = sum(w in _COMMON for w in words)
        if common == 0 and not _POLISH.search(text):
            return True
    return False
