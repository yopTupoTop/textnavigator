"""Определение действующих условий: применяются только подписанные и вступившие в силу допсоглашения."""
from __future__ import annotations

import datetime as dt
import re

from . import patterns as P
from .extractor import (amendment_points, extract_base, extract_proposals, extract_supplier, find_candidates,
                        ignored_values, status_for_proposal)
from .models import (CONFIRMED, KIND_LABELS, REVIEW, ContractResult, Document, FieldValue, HistoryItem,
                     RelatedDoc)

FIELDS = ("amount", "deadline", "penalty")
FIELD_RU = {"amount": "Сумма", "deadline": "Срок", "penalty": "Неустойка"}


def _mark_review(fv: FieldValue, note: str) -> None:
    fv.state, fv.display, fv.note = REVIEW, REVIEW, note


def _number_evidence(c: Document):
    if not c.paragraphs or not c.number:
        return None
    p = c.paragraphs[0]
    m = re.search(r"№\s*([^\s,;]+)", p.text)
    return c.evidence(p, m.start(1), m.start(1) + len(c.number)) if m else None


def resolve_contract(c: Document, by_number: dict, linked: list[tuple[Document, bool]], as_of: dt.date) -> ContractResult:
    flags: list[str] = []
    related: list[RelatedDoc] = []
    proposals = []
    dup = len(by_number.get(c.number or "", [])) > 1

    fields = {f: extract_base(c, f) for f in FIELDS}
    ignored = {"amount": ignored_values(c, fields["amount"], "amount"),
               "deadline": ignored_values(c, fields["deadline"], "deadline")}

    if c.status != "signed":
        flags.append("Договор не помечен как подписанный обеими сторонами — проверьте статус.")
    if dup:
        flags.append(f"В загрузке несколько договоров с номером {c.number} — допсоглашения не применены.")

    # 1. Какие документы вправе менять условия
    applicable: list[Document] = []
    for d, unambiguous in linked:
        mismatch = []
        if d.supplier and c.supplier and P.norm_party(d.supplier) != P.norm_party(c.supplier):
            mismatch.append(f"поставщик «{d.supplier}» ≠ «{c.supplier}»")
        if d.buyer and c.buyer and P.norm_party(d.buyer) != P.norm_party(c.buyer):
            mismatch.append(f"покупатель «{d.buyer}» ≠ «{c.buyer}»")
        if not unambiguous:
            flags.append(f"«{d.name}» упоминает несколько договоров — привязка неоднозначна.")
        in_force = (d.kind == "amendment" and d.status == "signed" and d.effective_date is not None
                    and d.effective_date <= as_of)
        if in_force and not mismatch and not dup and unambiguous:
            applicable.append(d)
            related.append(RelatedDoc(d.id, d.name, d.label, "applied",
                                      f"подписано обеими сторонами, вступило в силу {P.fmt_date(d.effective_date)} — учтено"))
            continue
        kind, status_label = status_for_proposal(d, as_of)
        if mismatch:
            flags.append(f"«{d.name}»: стороны не совпадают с договором ({'; '.join(mismatch)}) — не учтено, требуется проверка.")
        if kind == "unclear" or dup or mismatch:
            kind = "unclear"
            if not mismatch and not dup:
                flags.append(f"«{d.name}»: {status_label}.")
        related.append(RelatedDoc(d.id, d.name, d.label, "proposal" if kind != "unclear" else "review", status_label))
        proposals += extract_proposals(d, c.number or "", kind, status_label)

    # 2. Применение вступивших в силу допсоглашений в хронологическом порядке
    applicable.sort(key=lambda d: (d.effective_date, d.amendment_no or 0, d.name))
    events: dict[str, list] = {f: [] for f in FIELDS}
    for d in applicable:
        points, unparsed = amendment_points(d)
        for p in unparsed:
            flags.append(f"«{d.name}», абз. {p.no}: не удалось разобрать изменение — требуется проверка.")
            cl = P.CLAUSE_MENTION_RE.search(p.text).group(1)
            for fv in fields.values():
                if fv.clause == cl:
                    _mark_review(fv, f"п. {cl} затронут допсоглашением «{d.name}», изменение не распознано.")
        if not points and not unparsed:
            flags.append(f"«{d.name}»: в допсоглашении не найдено изменений в распознаваемом виде — требуется проверка.")
        for pt in points:
            hit = False
            for f in FIELDS:
                for cd in find_candidates(f, pt.text, pt.offset):
                    if not cd.relative:
                        hit = True
                        events[f].append((d, pt, cd))
            if not hit:
                for fv in fields.values():
                    if fv.clause == pt.clause:
                        _mark_review(fv, f"п. {pt.clause} изменён допсоглашением «{d.name}», но значение не распознано.")
                        flags.append(fv.note)

    for f, evs in events.items():
        if not evs:
            continue
        fv = fields[f]
        evs.sort(key=lambda e: (e[0].effective_date, e[0].amendment_no or 0))
        last_date = evs[-1][0].effective_date
        last = [e for e in evs if e[0].effective_date == last_date]

        def src(d):
            return f"{d.label} ({d.name}), в силе с {P.fmt_date(d.effective_date)}"

        for d, pt, cd in evs:
            fv.history.append(HistoryItem(cd.display, src(d), d.evidence(pt.para, cd.start, cd.end)))
        if len({cd.value for _, _, cd in last}) > 1:
            _mark_review(fv, "Несколько допсоглашений с одной датой вступления в силу задают разные значения.")
            fv.value = None
            fv.candidates = [HistoryItem(cd.display, src(d), d.evidence(pt.para, cd.start, cd.end)) for d, pt, cd in last]
            flags.append(f"{FIELD_RU[f]}: конфликт допсоглашений с одной датой вступления в силу.")
            continue
        d, pt, cd = last[-1]
        fv.original = fv.display if fv.state == CONFIRMED else "нет данных"
        fv.state, fv.value, fv.display = CONFIRMED, cd.value, cd.display
        fv.evidence = d.evidence(pt.para, cd.start, cd.end)
        fv.changed, fv.source = True, src(d)
        fv.note = f"Изменено допсоглашением; исходное значение: {fv.original}"

    # 3. Пояснения к предложениям
    for pr in proposals:
        cur = fields.get(pr.field)
        if pr.from_display and cur and cur.display and pr.from_display != cur.display:
            pr.note = f"В предложении указано исходное значение {pr.from_display}, а действующее — {cur.display}."
        if cur and cur.value is not None and pr.value == cur.value:
            pr.note = (pr.note + " " if pr.note else "") + "Совпадает с действующим значением."

    return ContractResult(
        number=c.number, number_evidence=_number_evidence(c), supplier=extract_supplier(c),
        amount=fields["amount"], deadline=fields["deadline"], penalty=fields["penalty"],
        file=c.name, doc_id=c.id, proposals=proposals, related=related, ignored=ignored, flags=flags,
    )
