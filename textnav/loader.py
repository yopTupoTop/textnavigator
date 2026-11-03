"""Чтение TXT-файлов. Имя файла нужно только для показа пользователю."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RawFile:
    name: str
    text: str


def decode_bytes(data: bytes) -> str:
    for enc in ("utf-8-sig", "cp1251"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def from_upload(name: str, data: bytes) -> RawFile:
    """Для загрузки через интерфейс: имя и байты файла."""
    return RawFile(name, decode_bytes(data))


def read_txt_files(paths) -> list[RawFile]:
    """Чтение набора файлов с диска, в порядке переданных путей."""
    return [RawFile(Path(p).name, decode_bytes(Path(p).read_bytes())) for p in paths]
