"""Правила извлечения: сумма, срок, неустойка, поставщик, предложения об изменении, пункты допсоглашений."""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from . import patterns as P
from .models import (CONFIRMED, KIND_LABELS, NO_DATA, REVIEW, Document, Evidence, FieldValue,
                     HistoryItem, IgnoredValue, Paragraph, Proposal)


@dataclass
class Candidate:
    value: Any
    display: str
    start: int
    end: int
    relative: bool = False


def find_candidates(field: str, text: str, offset: int = 0) -> list[Candidate]:
    """Кандидаты значения поля в одном тексте (абзац или вставка из допсоглашения)."""
    out: list[Candidate] = []
    if field == "penalty":
        for m in P.PENALTY_RE.finditer(text):
            rate = P.parse_number(m.group(1))
            cap = P.parse_number(m.group(2)) if m.group(2) else None
            if rate is not None:
                out.append(Candidate((rate, cap), P.fmt_penalty(rate, cap), m.start() + offset, m.end() + offset))
    elif field == "amount":
        for m in P.AMOUNT_RE.finditer(text):
            if P.AMOUNT_EXCLUDE.search(P.sentence_prefix(text, m.start()) + m.group(0)):
                continue
            v = P.parse_number(m.group(1))
            if v is not None:
                out.append(Candidate(v, P.fmt_amount(v), m.start() + offset, m.end() + offset))
    else:  # deadline
        for m in P.DEADLINE_RE.finditer(text):
            if P.DEADLINE_EXCLUDE.search(P.sentence_prefix(text, m.start()) + m.group(0)):
                continue
            d = P.parse_date(m.group(1))
            if d is not None:
                out.append(Candidate(d, P.fmt_date(d), m.start() + offset, m.end() + offset))
        if not out:
            for m in P.DEADLINE_RELATIVE_RE.finditer(text):
                out.append(Candidate(None, "", m.start() + offset, m.end() + offset, relative=True))
    return out


def source_label(doc: Document) -> str:
    return f"{doc.label} ({doc.name})"


def extract_base(doc: Document, field: str) -> FieldValue:
    """Значение поля из самого договора (до применения допсоглашений)."""
    fv = FieldValue(name=field)
    found: list[tuple[Paragraph, Candidate]] = []
    for p in doc.paragraphs:
        for c in find_candidates(field, p.text):
            found.append((p, c))
    relative = [(p, c) for p, c in found if c.relative]
    found = [(p, c) for p, c in found if not c.relative]
    distinct = {c.value for _, c in found}

    if len(distinct) == 1:
        p, c = found[0]
        fv.state, fv.value, fv.display, fv.clause = CONFIRMED, c.value, c.display, P.clause_of(p.text)
        fv.evidence = doc.evidence(p, c.start, c.end)
        fv.source = source_label(doc)
        fv.history.append(HistoryItem(c.display, fv.source, fv.evidence))
    elif len(distinct) > 1:
        fv.state, fv.display = REVIEW, REVIEW
        fv.note = "В договоре найдено несколько разных значений — выберите вручную."
        fv.candidates = [HistoryItem(c.display, source_label(doc), doc.evidence(p, c.start, c.end)) for p, c in found]
    elif relative:
        p, c = relative[0]
        fv.state, fv.display = REVIEW, REVIEW
        fv.note = "Срок задан относительным периодом, а не датой — дату вычислить нельзя."
        fv.evidence = doc.evidence(p, c.start, c.end)
    else:
        # объясняем отсутствие, если в тексте есть явное указание
        key = {"amount": r"стоимост|цена", "penalty": r"неустойк"}.get(field, r"срок\w*\s+поставки|поставить")
        notes = {"amount": "Числовое значение в тексте договора не указано.",
                 "penalty": "Ставка неустойки в тексте договора не установлена."}
        for p in doc.paragraphs:
            if re.search(key, p.text, re.I) and re.search(r"не\s+указан|не\s+установлен|спецификаци|приложени", p.text, re.I):
                fv.note = notes.get(field, "Срок поставки в тексте договора не указан.")
                fv.evidence = doc.evidence(p, 0, len(p.text))
                break
    return fv


def extract_supplier(doc: Document) -> FieldValue:
    fv = FieldValue(name="supplier")
    for p in doc.paragraphs:
        m = re.search(r"Поставщик\s*:\s*(.+)$", p.text)
        if m:
            name = m.group(1).strip().rstrip(".")
            s = m.start(1)
            fv.state, fv.value, fv.display = CONFIRMED, name, name
            fv.evidence = doc.evidence(p, s, s + len(name))
            fv.source = source_label(doc)
            return fv
    return fv


def ignored_values(doc: Document, base: FieldValue, kind: str) -> list[IgnoredValue]:
    """Суммы и даты, которые встречаются в договоре, но не приняты за значение поля, — с причиной."""
    out = []
    ev = base.evidence
    for p in doc.paragraphs:
        if re.match(r"(?i)(дата|статус)\s*:", p.text):
            continue
        rx = P.ANY_RUB_RE if kind == "amount" else P.DATE_RE
        for m in rx.finditer(p.text):
            if ev and ev.paragraph == p.no and ev.start <= m.start() < ev.end:
                continue
            low = p.text.lower()
            if kind == "amount":
                reason = ("внутренний лимит покупателя, не стоимость договора" if "лимит" in low
                          else "аванс — часть общей стоимости" if "аванс" in low
                          else "контекст не указывает на стоимость договора")
                v = P.parse_number(m.group(1))
                disp = P.fmt_amount(v) if v is not None else m.group(1)
            else:
                reason = ("срок оплаты, не срок поставки" if "оплат" in low
                          else "приёмка, не срок поставки" if ("приемк" in low or "приёмк" in low)
                          else "дата вне условия о сроке поставки")
                disp = m.group(1)
            out.append(IgnoredValue(disp, reason, doc.evidence(p, m.start(), m.end())))
    return out


@dataclass
class Point:
    clause: str
    para: Paragraph
    text: str     # новая редакция пункта
    offset: int   # позиция новой редакции внутри абзаца


def amendment_points(doc: Document) -> tuple[list[Point], list[Paragraph]]:
    """Пункты «изложить в редакции» и абзацы, где пункт упомянут, но разобрать его не удалось."""
    points, unparsed = [], []
    for p in doc.paragraphs:
        m = P.REPLACE_RE.search(p.text)
        if m:
            points.append(Point(m.group(1), p, m.group(2), m.start(2)))
        elif P.CLAUSE_MENTION_RE.search(p.text) and re.match(r"\d+\.", p.text):
            unparsed.append(p)
    return points, unparsed


def status_for_proposal(doc: Document, as_of) -> tuple[str, str]:
    """(вид предложения, человекочитаемый статус) для документа, который условия не меняет."""
    if doc.kind == "letter":
        return "letter", "письмо поставщика: предложение, не согласовано — условия не меняет"
    if doc.status == "signed" and doc.effective_date and doc.effective_date > as_of:
        return "pending", f"подписано, вступает в силу {P.fmt_date(doc.effective_date)} — позже даты проверки"
    if doc.status == "signed" and doc.effective_date is None:
        return "unclear", "подписано, но дата вступления в силу не определена — требуется проверка"
    if doc.status == "unsigned":
        return "draft", "не подписано обеими сторонами — условия не меняет"
    return "unclear", "статус документа не определён — требуется проверка"


def extract_proposals(doc: Document, contract: str, kind: str, status_label: str) -> list[Proposal]:
    """Предложения об изменении условий из документа, который условия не меняет."""
    out: list[Proposal] = []

    def make(field, value, display, para, s, e, from_display=None):
        return Proposal(contract, field, value, display, kind, doc.label, status_label,
                        doc.evidence(para, s, e), from_display)

    points, _ = amendment_points(doc)
    for pt in points:
        for field in ("amount", "deadline", "penalty"):
            for c in find_candidates(field, pt.text, pt.offset):
                if not c.relative:
                    out.append(make(field, c.value, c.display, pt.para, c.start, c.end))
    for p in doc.paragraphs[1:]:  # заголовок предложением не считается
        t = p.text
        pm = P.PROPOSE_RE.search(t)
        if not pm or P.REPLACE_RE.search(t):
            continue
        found = False
        if re.search(r"срок\w*\s+поставки|поставк\w*\s+срок", t, re.I):
            dates = list(P.DATE_RE.finditer(t))
            d = P.parse_date(dates[-1].group(1)) if dates else None
            if d:
                frm = P.fmt_date(P.parse_date(dates[0].group(1))) if len(dates) >= 2 and P.parse_date(dates[0].group(1)) else None
                out.append(make("deadline", d, P.fmt_date(d), p, pm.start(), dates[-1].end(), frm))
                found = True
        if re.search(r"стоимост|цен[ауы]\b|сумм", t, re.I):
            rubs = list(P.ANY_RUB_RE.finditer(t))
            v = P.parse_number(rubs[-1].group(1)) if rubs else None
            if v is not None:
                out.append(make("amount", v, P.fmt_amount(v), p, pm.start(), rubs[-1].end()))
                found = True
        if not found:
            out.append(make("other", None, "—", p, pm.start(), len(t)))
    return out
