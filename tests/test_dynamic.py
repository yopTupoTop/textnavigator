"""Динамическая проверка: изменённая копия договора проходит ту же обработку, что и обычные документы.

Копия договора получает новые сумму, срок и поставщика и произвольное имя файла.
Проверяется: тип определяется по содержимому, новые значения и цитаты верны, старые значения нигде не остаются,
имя файла ни на что не влияет, отсутствующие значения не заменяются догадками.
"""
import dataclasses
import json
from decimal import Decimal

import pytest

from textnav import CONFIRMED, NO_DATA, analyze, from_upload

from .conftest import AS_OF, ORIGINALS

SOURCE = "03_contract.txt"
OLD = {"amount": "840 000", "deadline": "20.03.2026", "supplier": "Контур Электро"}
NEW = {"amount_text": "1 234 500 (один миллион двести тридцать четыре тысячи пятьсот)",
       "deadline": "14.05.2026", "supplier": "Альфа Снаб"}
RANDOM_NAME = "scan_0042 (копия) финал.txt"


def modified_text() -> str:
    t = (ORIGINALS / SOURCE).read_text(encoding="utf-8")
    for old, new in (("840 000 (восемьсот сорок тысяч)", NEW["amount_text"]),
                     (OLD["deadline"], NEW["deadline"]),
                     (OLD["supplier"], NEW["supplier"])):
        assert old in t
        t = t.replace(old, new)
    return t


def upload(name: str, text: str):
    """Тот же путь, что в интерфейсе: имя и байты файла → from_upload → analyze."""
    return analyze([from_upload(name, text.encode("utf-8"))], AS_OF)


@pytest.fixture
def result():
    return upload(RANDOM_NAME, modified_text())


def dump(obj) -> str:
    return json.dumps(dataclasses.asdict(obj), ensure_ascii=False, default=str)


# 1. Договор определяется по содержимому
def test_1_contract_detected_by_content(result):
    assert [d.kind for d in result.documents] == ["contract"]
    assert len(result.contracts) == 1 and result.contracts[0].number is not None
    assert result.warnings == []


# 2. Новые сумма, срок и поставщик
def test_2_new_values_are_extracted(result):
    c = result.contracts[0]
    assert (c.amount.state, c.amount.value, c.amount.display) == (CONFIRMED, Decimal(1234500), "1 234 500 ₽")
    assert (c.deadline.state, c.deadline.display) == (CONFIRMED, NEW["deadline"])
    assert c.supplier.display == f"ООО «{NEW['supplier']}»"
    assert not c.amount.changed and not c.deadline.changed and c.proposals == []


# 3. Цитаты соответствуют изменённому тексту
def test_3_quotes_match_modified_text(result):
    c = result.contracts[0]
    text = modified_text()
    for f, expected in ((c.amount, "1 234 500"), (c.deadline, NEW["deadline"]), (c.supplier, NEW["supplier"])):
        ev = f.evidence
        assert expected in ev.quote
        assert text[ev.doc_start:ev.doc_end] == ev.quote               # координаты в изменённом файле
        assert ev.file == RANDOM_NAME
    assert c.amount.evidence.quote.startswith("Общая стоимость")


# 4. Старые значения не сохраняются
def test_4_old_values_do_not_survive(result):
    blob = dump(result)
    for old in (OLD["amount"], OLD["deadline"], OLD["supplier"], "840000"):
        assert old not in blob, f"в результате остались старые данные: {old!r}"


# 5. Название файла не влияет
@pytest.mark.parametrize("name", [
    RANDOM_NAME, "05_supplier_email.txt", "06_amendment_draft.txt", "02_amendment_signed.txt",
    "ДОГОВОР_STN-999_подписан.txt", "x.TXT", "ё" * 40 + ".txt",
])
def test_5_file_name_does_not_affect_classification(name):
    r = upload(name, modified_text())
    assert [d.kind for d in r.documents] == ["contract"]
    c = r.contracts[0]
    assert (c.amount.value, c.deadline.display, c.supplier.display) == (
        Decimal(1234500), NEW["deadline"], f"ООО «{NEW['supplier']}»")
    assert c.amount.evidence.file == name


def test_5_misleading_names_do_not_make_other_documents_contracts():
    """Файл, названный «contract», но содержащий письмо, остаётся письмом."""
    letter = (ORIGINALS / "05_supplier_email.txt").read_text(encoding="utf-8")
    r = analyze([from_upload("01_contract.txt", letter.encode("utf-8"))], AS_OF)
    assert [d.kind for d in r.documents] == ["letter"] and r.contracts == []


# 6. Отсутствующие значения не заменяются догадками
@pytest.mark.parametrize("field, old, new, check", [
    ("amount", "Общая стоимость поставки составляет " + NEW["amount_text"] + " рублей, включая НДС.",
     "Стоимость согласуется отдельно.", lambda c: c.amount),
    ("deadline", "Поставщик обязуется поставить комплектующие не позднее " + NEW["deadline"] + ".",
     "Срок поставки согласуется отдельно.", lambda c: c.deadline),
    ("supplier", "Поставщик: ООО «" + NEW["supplier"] + "».", "", lambda c: c.supplier),
])
def test_6_missing_values_are_not_guessed(field, old, new, check):
    text = modified_text()
    assert old in text
    r = upload(RANDOM_NAME, text.replace(old, new))
    fv = check(r.contracts[0])
    assert (fv.state, fv.value, fv.display) == (NO_DATA, None, NO_DATA)
    assert dump(r).count(OLD["deadline"]) == 0 and dump(r).count(OLD["amount"]) == 0


def test_6_other_fields_survive_when_one_is_missing():
    t = modified_text().replace("Поставщик обязуется поставить комплектующие не позднее " + NEW["deadline"] + ".", "Срок по согласованию.")
    c = upload(RANDOM_NAME, t).contracts[0]
    assert c.deadline.state == NO_DATA and c.amount.value == Decimal(1234500)


# Связанные документы реагируют на изменение договора
def test_related_letter_is_flagged_against_changed_contract():
    """Письмо ссылается на прежний срок: система показывает расхождение, а не молча принимает письмо."""
    letter = (ORIGINALS / "05_supplier_email.txt").read_bytes()
    r = analyze([from_upload(RANDOM_NAME, modified_text().encode("utf-8")), from_upload("any.txt", letter)], AS_OF)
    c = r.contracts[0]
    assert c.deadline.display == NEW["deadline"]
    prop = next(p for p in c.proposals if p.field == "deadline")
    assert prop.display == "27.03.2026" and prop.kind == "letter"
    assert prop.from_display == OLD["deadline"] and NEW["deadline"] in prop.note   # расхождение указано явно
