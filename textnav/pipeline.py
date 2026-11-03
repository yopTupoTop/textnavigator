"""Единая точка входа: набор загруженных файлов + дата проверки → результат."""
from __future__ import annotations

import datetime as dt
from typing import Iterable

from .classifier import classify
from .linker import link_documents
from .loader import RawFile
from .models import AnalysisResult, Document
from .resolver import resolve_contract
from .segmenter import split_paragraphs


def build_document(idx: int, raw: RawFile) -> Document:
    doc = Document(id=f"d{idx}", name=raw.name, text=raw.text)
    doc.paragraphs = split_paragraphs(raw.text)
    classify(doc)
    return doc


def analyze(files: Iterable[RawFile], as_of: dt.date) -> AnalysisResult:
    docs = [build_document(i, f) for i, f in enumerate(files)]
    links = link_documents(docs)
    contracts = [
        resolve_contract(d, links.by_number, links.attached.get(d.number or "", []), as_of)
        for d in docs if d.kind == "contract"
    ]
    return AnalysisResult(as_of=as_of, documents=docs, contracts=contracts, warnings=links.warnings)
