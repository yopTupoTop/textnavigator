"""Общие фикстуры. Тесты работают с копиями файлов во временной папке; оригиналы не меняются."""
import datetime as dt
import hashlib
import shutil
from pathlib import Path

import pytest

from textnav import analyze, read_txt_files

ROOT = Path(__file__).resolve().parent.parent
ORIGINALS = ROOT / "documents"
AS_OF = dt.date(2026, 3, 10)  # дата проверки из задания — параметр тестов, не кода


def _digest() -> dict:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(ORIGINALS.glob("*.txt"))}


@pytest.fixture(scope="session", autouse=True)
def originals_untouched():
    before = _digest()
    yield
    assert _digest() == before, "оригинальные документы в documents/ были изменены тестами"


@pytest.fixture
def workdir(tmp_path):
    """Временная копия шести исходных документов."""
    d = tmp_path / "docs"
    shutil.copytree(ORIGINALS, d)
    return d


def edit(folder: Path, name_part: str, old: str, new: str) -> None:
    """Заменить текст во временной копии файла (все вхождения); old обязан существовать."""
    matches = [p for p in folder.glob("*.txt") if name_part in p.name]
    assert len(matches) == 1, f"ожидался один файл с «{name_part}», найдено {len(matches)}"
    text = matches[0].read_text(encoding="utf-8")
    assert old in text, f"{old!r} не найдено в {matches[0].name}"
    matches[0].write_text(text.replace(old, new), encoding="utf-8")


def write(folder: Path, name: str, text: str) -> Path:
    p = folder / name
    p.write_text(text, encoding="utf-8")
    return p


def run(folder: Path, as_of: dt.date = AS_OF, only=None):
    paths = sorted(folder.glob("*.txt"))
    if only:
        paths = [p for p in paths if any(o in p.name for o in only)]
    return analyze(read_txt_files(paths), as_of)


def by_number(result) -> dict:
    return {c.number: c for c in result.contracts}
