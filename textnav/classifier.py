"""Тип и статус документа определяются по содержимому: заголовок, строка «Статус:», дата вступления в силу."""
from __future__ import annotations

import re

from .models import Document
from .patterns import DATE_PAT, parse_date


def classify(doc: Document) -> None:
    paras = doc.paragraphs
    if not paras:
        return
    title = paras[0].text
    up = title.upper()
    if up.startswith("ПРОЕКТ"):
        doc.kind = "draft"
    elif "ДОПОЛНИТЕЛЬНОЕ СОГЛАШЕНИЕ" in up:
        doc.kind = "amendment"
    elif up.startswith("ДОГОВОР"):
        doc.kind = "contract"
    elif up.startswith("ПИСЬМО") or any(re.match(r"(?i)кому\s*:", p.text) for p in paras[:8]):
        doc.kind = "letter"
    else:
        doc.kind = "unknown"

    if doc.kind == "contract":
        m = re.search(r"№\s*([^\s,;]+)", title)
        doc.number = m.group(1).rstrip(".") if m else None
    if doc.kind in ("amendment", "draft"):
        m = re.search(r"СОГЛАШЕНИ\w+\s+№\s*(\d+)", title, re.I)
        doc.amendment_no = int(m.group(1)) if m else None
        m = re.search(r"К\s+ДОГОВОРУ\s+(?:№\s*)?([^\s,;]+)", title, re.I)
        doc.ref_number = m.group(1).rstrip(".") if m else None

    # статус подписания
    for p in paras[:10]:
        m = re.match(r"(?i)статус\s*:\s*(.*)", p.text)
        if not m:
            continue
        s = m.group(1).lower()
        doc.status_text = p.text
        if re.search(r"не\s+подписан|проект|на\s+согласовани|одной\s+стороной|односторонн", s):
            doc.status = "unsigned"
        elif re.search(r"подписан\w*\s+(?:обеими|всеми|двумя)\s+сторонами", s):
            doc.status = "signed"
            dm = re.search(DATE_PAT, s)
            doc.signed_date = parse_date(dm.group(1)) if dm else None
        break
    if doc.kind == "draft" and doc.status == "unknown":
        doc.status = "unsigned"

    # дата вступления в силу (только для соглашений)
    if doc.kind in ("amendment", "draft"):
        for p in paras:
            m = re.search(r"вступа\w*\s+в\s+силу\s+(?:с\s+)?" + DATE_PAT, p.text, re.I)
            if m:
                doc.effective_date = parse_date(m.group(1))
                break
            if doc.status == "signed" and re.search(
                r"вступа\w*\s+в\s+силу\s+(?:с\s+(?:даты|момента)\s+)?подписания\s*\.?$", p.text, re.I
            ):
                doc.effective_date = doc.signed_date

    # стороны
    for p in paras:
        m = re.search(r"Поставщик\s*:\s*(.+)$", p.text)
        if m and not doc.supplier:
            doc.supplier = m.group(1).strip().rstrip(".")
        m = re.search(r"Покупатель\s*:\s*([^.]+)", p.text)
        if m and not doc.buyer:
            doc.buyer = m.group(1).strip()
