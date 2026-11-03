"""Регулярные выражения и разбор чисел/дат. Здесь нет ни одного значения из документов."""
from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal, InvalidOperation

DATE_PAT = r"(\d{2}\.\d{2}\.\d{4})"
NUM_PAT = r"(\d{1,3}(?:[   ]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)"

CLAUSE_RE = re.compile(r"^(\d+(?:\.\d+)*)\.\s")
DATE_RE = re.compile(DATE_PAT)
ANY_RUB_RE = re.compile(NUM_PAT + r"(?:\s*\([^)]*\))?\s*(?:руб\w*\.?|₽)", re.I)

# «Общая стоимость поставки составляет <число> (<прописью>) рублей»
AMOUNT_RE = re.compile(
    r"(?:общая\s+стоимость|итоговая\s+стоимость|стоимость\s+(?:договора|поставки)|цена\s+договора|цена)"
    r"[^.:;]{0,60}?\s*(?:составляет|равна|равняется|определена\s+в\s+размере|:|—|-)\s*" + NUM_PAT
    + r"(?:\s*\([^)]*\))?\s*(?:руб\w*\.?|₽)",
    re.I,
)
AMOUNT_EXCLUDE = re.compile(r"лимит|аванс|предлага|оплат", re.I)

# «Поставщик обязуется поставить ... не позднее ДД.ММ.ГГГГ»
DEADLINE_RE = re.compile(
    r"(?:поставить|поставк\w+|срок\w*\s+поставки)[^.;]{0,80}?"
    r"(?:не\s+позднее|до|в\s+срок\s+до|:|—|-)\s*" + DATE_PAT + r"(?:\s*г(?:ода|\.)?)?",
    re.I,
)
DEADLINE_RELATIVE_RE = re.compile(r"(?:поставить|срок\w*\s+поставки)[^.;]{0,80}?в\s+течение\s+\d+", re.I)
DEADLINE_EXCLUDE = re.compile(r"оплат|приемк|приёмк|аванс|предлага", re.I)

# «Неустойка ... составляет <N>% ... за каждый день, но не более <M>% ...»
PENALTY_RE = re.compile(
    r"неустойк\w*[^.]{0,80}?(?:составляет|равна|равняется|в\s+размере)\s+" + NUM_PAT + r"\s*%"
    r"[^.]{0,80}?(?:за\s+каждый\s+день|в\s+день|ежедневно)"
    r"(?:[^.]{0,20}?не\s+более\s+" + NUM_PAT + r"\s*%)?",
    re.I,
)

# «Пункт 2.1 договора изложить в следующей редакции: «...»»
REPLACE_RE = re.compile(
    r"пункт\w*\s+(\d+(?:\.\d+)*)\s+договора\s+изложить\s+в\s+(?:следующей\s+)?редакции\s*:?\s*[«\"“](.*)[»\"”]",
    re.I | re.S,
)
CLAUSE_MENTION_RE = re.compile(r"пункт\w*\s+(\d+(?:\.\d+)*)", re.I)
PROPOSE_RE = re.compile(r"предлага(?:ем|ю|ет|ют|ется|ются)\b", re.I)


def parse_date(s: str) -> dt.date | None:
    try:
        d, m, y = s.split(".")
        return dt.date(int(y), int(m), int(d))
    except (ValueError, AttributeError):
        return None


def parse_number(s: str) -> Decimal | None:
    s = re.sub(r"[   ]", "", s).replace(",", ".")
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def fmt_date(d: dt.date) -> str:
    return d.strftime("%d.%m.%Y")


def fmt_amount(v: Decimal) -> str:
    if v == v.to_integral_value():
        return f"{int(v):,}".replace(",", " ") + " ₽"
    return f"{v:,.2f}".replace(",", " ").replace(".", ",") + " ₽"


def _pct(v: Decimal) -> str:
    return f"{v.normalize():f}".replace(".", ",")


def fmt_penalty(rate: Decimal, cap: Decimal | None) -> str:
    s = f"{_pct(rate)}% в день"
    return s + (f", не более {_pct(cap)}%" if cap is not None else "")


def norm_party(s: str) -> str:
    s = re.sub(r"[«»\"“”'.\s]+", " ", s.lower()).strip()
    return re.sub(r"^(ооо|оао|зао|ао|ип)\s+", "", s)


def sentence_prefix(text: str, pos: int) -> str:
    """Текст от начала предложения до позиции pos (контекст для исключений)."""
    start = text.rfind(". ", 0, pos)
    return text[start + 2 : pos] if start != -1 else text[:pos]


def clause_of(text: str) -> str | None:
    m = CLAUSE_RE.match(text)
    return m.group(1) if m else None
