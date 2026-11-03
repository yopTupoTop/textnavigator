"""Разбиение текста на абзацы с сохранением координат."""
from __future__ import annotations

from .models import Paragraph


def split_paragraphs(text: str) -> list[Paragraph]:
    """Абзац = непустая строка. Хранятся номер абзаца, номер строки и позиция в полном тексте."""
    out: list[Paragraph] = []
    pos = no = 0
    for line_no, raw in enumerate(text.splitlines(keepends=True), 1):
        stripped = raw.strip()
        if stripped:
            no += 1
            out.append(Paragraph(no, line_no, pos + (len(raw) - len(raw.lstrip())), stripped))
        pos += len(raw)
    return out
