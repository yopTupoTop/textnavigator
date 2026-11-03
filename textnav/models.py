"""Модель данных: документ, абзац, источник значения, извлечённое поле, результат по договору."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

CONFIRMED = "Подтверждено"
NO_DATA = "Нет данных"
REVIEW = "Требуется проверка"
PARTIAL = "Неполные данные"  # значения подтверждены, но часть полей отсутствует в документах

KIND_LABELS = {
    "contract": "Договор",
    "amendment": "Допсоглашение",
    "draft": "Проект допсоглашения",
    "letter": "Письмо",
    "unknown": "Не определён",
    "empty": "Пустой файл",
}
FIELD_LABELS = {"amount": "Сумма", "deadline": "Срок поставки", "penalty": "Неустойка", "other": "Иное"}


@dataclass(frozen=True)
class Paragraph:
    no: int       # порядковый номер непустого абзаца, с 1
    line: int     # номер строки в файле, с 1
    offset: int   # позиция начала абзаца в полном тексте файла
    text: str


@dataclass(frozen=True)
class Evidence:
    """Источник значения: где именно в каком файле и на каком основании принято решение."""
    file: str
    doc_id: str
    paragraph: int
    line: int
    quote: str
    start: int       # позиция цитаты внутри абзаца
    end: int
    doc_start: int   # позиция цитаты в полном тексте файла
    doc_end: int
    doc_status: str  # статус документа, на основании которого принято решение


@dataclass
class HistoryItem:
    display: str
    source: str
    evidence: Evidence


@dataclass
class FieldValue:
    name: str
    state: str = NO_DATA
    value: Any = None
    display: str = NO_DATA
    evidence: Evidence | None = None
    source: str = ""            # человекочитаемо: «Допсоглашение № 1 (файл)…»
    changed: bool = False
    original: str | None = None
    history: list[HistoryItem] = field(default_factory=list)
    note: str = ""
    candidates: list[HistoryItem] = field(default_factory=list)
    clause: str | None = None   # номер пункта, из которого взято значение


@dataclass
class IgnoredValue:
    display: str
    reason: str
    evidence: Evidence


@dataclass
class Proposal:
    contract: str
    field: str
    value: Any
    display: str
    kind: str          # letter | draft | pending | unclear
    doc_label: str
    status_label: str
    evidence: Evidence
    from_display: str | None = None
    note: str = ""


@dataclass
class RelatedDoc:
    doc_id: str
    file: str
    label: str
    role: str          # applied | proposal | review
    text: str


@dataclass
class ContractResult:
    number: str | None
    number_evidence: Evidence | None
    supplier: FieldValue
    amount: FieldValue
    deadline: FieldValue
    penalty: FieldValue
    file: str
    doc_id: str
    proposals: list[Proposal] = field(default_factory=list)
    related: list[RelatedDoc] = field(default_factory=list)
    ignored: dict[str, list[IgnoredValue]] = field(default_factory=dict)
    flags: list[str] = field(default_factory=list)

    @property
    def state(self) -> str:
        fs = (self.supplier, self.amount, self.deadline, self.penalty)
        if self.flags or any(f.state == REVIEW for f in fs):
            return REVIEW
        return PARTIAL if any(f.state == NO_DATA for f in fs) else CONFIRMED


@dataclass
class Document:
    id: str
    name: str
    text: str
    paragraphs: list[Paragraph] = field(default_factory=list)
    kind: str = "empty"
    status: str = "unknown"          # signed | unsigned | unknown
    status_text: str = ""
    signed_date: dt.date | None = None
    effective_date: dt.date | None = None
    number: str | None = None        # номер договора (для договора)
    amendment_no: int | None = None
    ref_number: str | None = None    # номер договора, на который ссылается документ
    supplier: str | None = None
    buyer: str | None = None

    @property
    def label(self) -> str:
        extra = f" № {self.amendment_no}" if self.amendment_no and self.kind in ("amendment", "draft") else ""
        extra = f" {self.number}" if self.kind == "contract" and self.number else extra
        return KIND_LABELS[self.kind] + extra

    def status_summary(self) -> str:
        if self.status_text:
            s = self.status_text.split(":", 1)[-1].strip().rstrip(".")
        elif self.kind == "letter":
            s = "письмо, не является соглашением сторон"
        else:
            s = "статус не указан"
        if self.effective_date:
            s += f"; вступает в силу {self.effective_date.strftime('%d.%m.%Y')}"
        return f"{self.label}: {s}"

    def evidence(self, para: Paragraph, start: int, end: int) -> Evidence:
        return Evidence(
            file=self.name, doc_id=self.id, paragraph=para.no, line=para.line,
            quote=para.text[start:end], start=start, end=end,
            doc_start=para.offset + start, doc_end=para.offset + end,
            doc_status=self.status_summary(),
        )


@dataclass
class AnalysisResult:
    as_of: dt.date
    documents: list[Document]
    contracts: list[ContractResult]
    warnings: list[str]
