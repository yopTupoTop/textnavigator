"""Связывание дополнительных документов с договорами — по номеру договора в тексте, не по имени файла."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .models import KIND_LABELS, Document


@dataclass
class Links:
    by_number: dict[str, list[Document]] = field(default_factory=dict)
    attached: dict[str, list[tuple[Document, bool]]] = field(default_factory=dict)  # (документ, однозначно?)
    warnings: list[str] = field(default_factory=list)


def referenced_numbers(doc: Document, known: list[str]) -> list[str]:
    """Для соглашений и проектов — номер из заголовка; для остальных — все известные номера в тексте."""
    if doc.kind in ("amendment", "draft") and doc.ref_number:
        return [doc.ref_number]
    text = "\n".join(p.text for p in doc.paragraphs)
    return [n for n in known if re.search(r"(?<![\w-])" + re.escape(n) + r"(?![\w-])", text)]


def link_documents(docs: list[Document]) -> Links:
    links = Links()
    for d in docs:
        if d.kind == "contract" and d.number:
            links.by_number.setdefault(d.number, []).append(d)
        if d.kind == "contract" and not d.number:
            links.warnings.append(f"В договоре «{d.name}» не найден номер.")
        if d.kind in ("unknown", "empty"):
            links.warnings.append(f"Файл «{d.name}» не распознан (нет заголовка договора, соглашения, проекта или письма) — пропущен.")
    links.attached = {n: [] for n in links.by_number}
    known = list(links.by_number)
    for d in docs:
        if d.kind in ("contract", "unknown", "empty"):
            continue
        nums = referenced_numbers(d, known)
        if not nums:
            links.warnings.append(f"«{d.name}» ({KIND_LABELS[d.kind]}): не удалось определить, к какому договору относится.")
            continue
        for n in nums:
            if n not in links.by_number:
                links.warnings.append(f"«{d.name}» ссылается на договор {n}, которого нет среди загруженных файлов.")
            else:
                links.attached[n].append((d, len(nums) == 1))
    return links
