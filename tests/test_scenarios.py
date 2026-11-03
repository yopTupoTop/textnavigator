"""12 сценариев из задания + проверки вшитых значений, штрафа, статусов «Требуется проверка»."""
import json
import random
import re
from decimal import Decimal

import pytest

from textnav import CONFIRMED, NO_DATA, REVIEW, analyze, from_upload
from textnav.models import PARTIAL

from .conftest import AS_OF, ORIGINALS, ROOT, by_number, edit, run, write

AMENDMENT = "02_amendment"
DRAFT = "06_amendment"
LETTER = "05_supplier"


# --- 1. Извлечение суммы и срока из договора -------------------------------------------
def test_01_amount_and_deadline_from_contract(workdir):
    c = by_number(run(workdir, only=["03_contract"]))["STN-102"]
    assert (c.amount.state, c.amount.value) == (CONFIRMED, Decimal(840000))
    assert c.amount.display == "840 000 ₽"
    assert c.deadline.display == "20.03.2026"
    assert c.supplier.display == "ООО «Контур Электро»"
    assert not c.amount.changed and not c.deadline.changed


def test_01_amounts_not_confused_with_advance_limit_or_payment_date(workdir):
    res = by_number(run(workdir, only=["03_contract", "04_contract"]))
    assert res["STN-102"].amount.value == Decimal(840000)            # не аванс 252 000
    assert res["STN-102"].deadline.display == "20.03.2026"           # не срок оплаты 10.04.2026
    reasons = [i.reason for v in res["STN-102"].ignored.values() for i in v]
    assert any("аванс" in r for r in reasons) and any("оплаты" in r for r in reasons)
    assert any("лимит" in i.reason for i in res["STN-103"].ignored["amount"])  # 600 000 — лимит


def test_01_exclusion_guards_for_advance_limit_and_payment_dates(workdir):
    """Формулировки, которые дошли бы до значения без исключений (аванс, лимит, оплата, приёмка)."""
    edit(workdir, "03_contract", "4.1. Неустойка", "2.3. Стоимость поставки в части аванса составляет 100 000 рублей.\n"
         "2.4. Стоимость поставки в пределах лимита составляет 500 000 рублей.\n"
         "3.3. Оплата после поставки производится не позднее 10.05.2026.\n"
         "3.4. Приемка после поставки завершается не позднее 25.04.2026.\n4.1. Неустойка")
    c = by_number(run(workdir, only=["03_contract"]))["STN-102"]
    assert c.amount.value == Decimal(840000) and c.amount.state == CONFIRMED
    assert c.deadline.display == "20.03.2026" and c.deadline.state == CONFIRMED
    reasons = " ".join(i.reason for v in c.ignored.values() for i in v)
    assert "срок оплаты" in reasons and "приёмка" in reasons


def test_05_letter_disguised_as_signed_amendment_changes_nothing(workdir):
    """Письмо не меняет условия, даже если в тексте написано «подписано обеими сторонами» и «изложить в редакции»."""
    edit(workdir, LETTER, "Дата: 05.03.2026", "Статус: подписано обеими сторонами 05.03.2026.\nДата: 05.03.2026")
    edit(workdir, LETTER, "Коллеги,", "1. Пункт 3.1 договора изложить в следующей редакции: «Поставщик обязуется поставить комплектующие не позднее 01.04.2026».\nКоллеги,")
    edit(workdir, LETTER, "договор STN-102", "договор STN-102\nВступает в силу 05.03.2026.")
    c = by_number(run(workdir))["STN-102"]
    assert c.deadline.display == "20.03.2026" and not c.deadline.changed
    assert any(p.kind == "letter" and p.display == "01.04.2026" for p in c.proposals)


# --- 2. Подписанное допсоглашение, вступившее до даты проверки ------------------------------
def test_02_signed_effective_amendment_is_applied(workdir):
    c = by_number(run(workdir))["STN-101"]
    assert (c.amount.value, c.deadline.display) == (Decimal(1380000), "30.04.2026")
    assert c.amount.changed and c.deadline.changed
    assert c.deadline.original == "15.04.2026"
    assert "02_amendment" in c.deadline.source
    assert "подписано обеими сторонами" in c.deadline.evidence.doc_status
    assert c.deadline.evidence.file.startswith("02_amendment")


# --- 3. Допсоглашение, вступающее в силу после даты проверки -----------------------------------
def test_03_amendment_effective_after_check_date_is_ignored(workdir):
    edit(workdir, AMENDMENT, "вступает в силу 01.03.2026", "вступает в силу 01.04.2026")
    c = by_number(run(workdir))["STN-101"]
    assert (c.amount.value, c.deadline.display) == (Decimal(1200000), "15.04.2026")
    assert not c.amount.changed
    pending = [p for p in c.proposals if p.kind == "pending"]
    assert {(p.field, p.display) for p in pending} == {("amount", "1 380 000 ₽"), ("deadline", "30.04.2026")}
    assert "позже даты проверки" in pending[0].status_label


def test_03_check_date_before_amendment_effective(workdir):
    import datetime as dt
    c = by_number(run(workdir, as_of=dt.date(2026, 2, 20)))["STN-101"]
    assert c.deadline.display == "15.04.2026"


# --- 4. Неподписанный проект -----------------------------------------------------------------
def test_04_unsigned_draft_is_ignored(workdir):
    c = by_number(run(workdir))["STN-101"]
    assert c.deadline.display == "30.04.2026"   # а не 10.05.2026 из проекта
    assert c.amount.value == Decimal(1380000)   # а не 1 450 000 ₽
    drafts = {(p.field, p.display) for p in c.proposals if p.kind == "draft"}
    assert drafts == {("amount", "1 450 000 ₽"), ("deadline", "10.05.2026")}


def test_04_unsigned_amendment_is_not_applied(workdir):
    edit(workdir, AMENDMENT, "Статус: подписано обеими сторонами 01.03.2026.", "Статус: проект, не подписано.")
    c = by_number(run(workdir))["STN-101"]
    assert (c.amount.value, c.deadline.display) == (Decimal(1200000), "15.04.2026")
    assert "30.04.2026" in [p.display for p in c.proposals]


def test_04_amendment_with_unknown_effective_date_requires_review(workdir):
    edit(workdir, AMENDMENT, "4. Настоящее соглашение вступает в силу 01.03.2026.", "4. Прочие условия без изменений.")
    c = by_number(run(workdir))["STN-101"]
    assert c.deadline.display == "15.04.2026"
    assert c.state == REVIEW and any("дата вступления" in f for f in c.flags)


# --- 5. Письмо поставщика ------------------------------------------------------------------
def test_05_supplier_letter_is_unagreed_proposal(workdir):
    c = by_number(run(workdir))["STN-102"]
    assert c.deadline.display == "20.03.2026"
    letter = [p for p in c.proposals if p.kind == "letter" and p.field == "deadline"]
    assert len(letter) == 1 and letter[0].display == "27.03.2026"
    assert "не согласовано" in letter[0].status_label
    assert letter[0].from_display == "20.03.2026" and not letter[0].note
    assert letter[0].evidence.file.startswith("05_supplier")


def test_05_letter_title_is_not_a_proposal(workdir):
    c = by_number(run(workdir))["STN-102"]
    assert [p.field for p in c.proposals] == ["deadline"]


# --- 6. Отсутствие суммы -----------------------------------------------------------------------
def test_06_missing_amount_is_no_data(workdir):
    c = by_number(run(workdir))["STN-103"]
    assert (c.amount.state, c.amount.value, c.amount.display) == (NO_DATA, None, "Нет данных")
    assert "не указано" in c.amount.evidence.quote
    assert c.state == PARTIAL


def test_06_removed_amount_clause_is_not_guessed(workdir):
    edit(workdir, "01_contract", "2.1. Общая стоимость поставки составляет 1 200 000 (один миллион двести тысяч) рублей, включая НДС. Указанная сумма — итоговая стоимость договора.", "2.1. Стоимость согласуется отдельно.")
    c = by_number(run(workdir, only=["01_contract"]))["STN-101"]
    assert (c.amount.state, c.amount.value) == (NO_DATA, None)


# --- 7. Отсутствие срока --------------------------------------------------------------------
def test_07_missing_deadline_is_no_data(workdir):
    edit(workdir, "04_contract", "3.1. Поставщик обязуется поставить запасные части не позднее 30.04.2026.", "3.1. Срок поставки согласуется отдельно.")
    c = by_number(run(workdir))["STN-103"]
    assert (c.deadline.state, c.deadline.value, c.deadline.display) == (NO_DATA, None, "Нет данных")


def test_07_relative_deadline_requires_review(workdir):
    edit(workdir, "04_contract", "не позднее 30.04.2026", "в течение 30 дней")
    c = by_number(run(workdir))["STN-103"]
    assert (c.deadline.state, c.deadline.value) == (REVIEW, None)


# --- 8. Нет связи между документом и договором ------------------------------------------------
def test_08_letter_without_contract_number_is_not_attached(workdir):
    edit(workdir, LETTER, "STN-102", "договор без номера")
    res = run(workdir)
    assert any("не удалось определить" in w for w in res.warnings)
    assert by_number(res)["STN-102"].proposals == []


def test_08_amendment_for_missing_contract_is_reported(workdir):
    edit(workdir, AMENDMENT, "К ДОГОВОРУ STN-101", "К ДОГОВОРУ STN-999")
    res = run(workdir)
    assert any("STN-999" in w for w in res.warnings)
    assert by_number(res)["STN-101"].deadline.display == "15.04.2026"   # допсоглашение не применено


def test_08_amendment_with_different_parties_is_not_applied(workdir):
    edit(workdir, AMENDMENT, "Поставщик: ООО «Вектор Прибор»", "Поставщик: ООО «Другой»")
    c = by_number(run(workdir))["STN-101"]
    assert c.deadline.display == "15.04.2026" and c.state == REVIEW


def test_08_unrecognized_file_is_skipped_with_warning(workdir):
    write(workdir, "notes.txt", "Просто заметки без заголовка.\nСумма 100 рублей.")
    res = run(workdir)
    assert any("не распознан" in w for w in res.warnings)
    assert len(res.contracts) == 3


# --- 9. Несколько допсоглашений --------------------------------------------------------------
def _second_amendment(workdir, *, date_eff, deadline, name="zz_amendment_3.txt"):
    text = (workdir / next(p.name for p in workdir.glob("*.txt") if AMENDMENT in p.name)).read_text(encoding="utf-8")
    text = text.replace("№ 1", "№ 3").replace("30.04.2026", deadline)
    if date_eff != "01.03.2026":
        text = text.replace("01.03.2026", date_eff)
    write(workdir, name, text)


def test_09_later_amendment_wins_and_history_is_kept(workdir):
    _second_amendment(workdir, date_eff="05.03.2026", deadline="05.05.2026")
    c = by_number(run(workdir))["STN-101"]
    assert c.deadline.display == "05.05.2026"
    assert [h.display for h in c.deadline.history] == ["15.04.2026", "30.04.2026", "05.05.2026"]
    assert "№ 3" in c.deadline.source


def test_09_same_date_conflict_requires_review(workdir):
    _second_amendment(workdir, date_eff="01.03.2026", deadline="05.05.2026")
    c = by_number(run(workdir))["STN-101"]
    assert (c.deadline.state, c.deadline.value) == (REVIEW, None)
    assert len(c.deadline.candidates) == 2 and c.state == REVIEW


def test_09_second_amendment_effective_later_than_check_date_is_ignored(workdir):
    _second_amendment(workdir, date_eff="01.04.2026", deadline="05.05.2026")
    c = by_number(run(workdir))["STN-101"]
    assert c.deadline.display == "30.04.2026"


# --- 10. Изменение исходных значений в загруженном документе --------------------------------------
def test_10_changed_amount_and_deadline_in_uploaded_contract(workdir):
    edit(workdir, "03_contract", "840 000 (восемьсот сорок тысяч)", "915 500,50 (девятьсот пятнадцать тысяч пятьсот рублей пятьдесят копеек)")
    edit(workdir, "03_contract", "не позднее 20.03.2026", "не позднее 25.03.2026")
    c = by_number(run(workdir))["STN-102"]
    assert c.amount.value == Decimal("915500.50") and c.amount.display == "915 500,50 ₽"
    assert c.deadline.display == "25.03.2026"
    assert "915 500,50" in c.amount.evidence.quote and "25.03.2026" in c.deadline.evidence.quote


def test_10_alternative_phrasing(workdir):
    edit(workdir, "03_contract", "Общая стоимость поставки составляет 840 000", "Общая стоимость поставки: 700 000")
    edit(workdir, "03_contract", "Поставщик обязуется поставить комплектующие не позднее 20.03.2026", "Срок поставки — 21.03.2026")
    c = by_number(run(workdir))["STN-102"]
    assert (c.amount.value, c.deadline.display) == (Decimal(700000), "21.03.2026")


# --- 11. Новый договор, которого не было в исходном наборе -----------------------------------------
def test_11_new_contract_from_new_file(workdir):
    text = (ORIGINALS / "01_contract.txt").read_text(encoding="utf-8")
    text = text.replace("STN-101", "ZX-5").replace("1 200 000 (один миллион двести тысяч)", "2 000 000 (два миллиона)")
    text = text.replace("Вектор Прибор", "Новая Компания")
    write(workdir, "something_else.txt", text)
    res = by_number(run(workdir))
    c = res["ZX-5"]
    assert (c.amount.value, c.deadline.display, c.supplier.display) == (Decimal(2000000), "15.04.2026", "ООО «Новая Компания»")
    assert c.proposals == [] and not c.amount.changed      # допсоглашения STN-101 к нему не относятся
    assert len(res) == 4


def test_11_files_names_and_order_do_not_matter(workdir):
    def sig(r):
        return {c.number: (c.amount.display, c.deadline.display, c.penalty.display, c.state,
                           sorted((p.field, p.display, p.kind) for p in c.proposals)) for c in r.contracts}
    base = sig(run(workdir))
    files = [from_upload(f"{random.Random(i).randint(10**5, 10**6)}.dat.txt", p.read_bytes())
             for i, p in enumerate(sorted(workdir.glob("*.txt")))]
    random.Random(3).shuffle(files)
    assert sig(analyze(files, AS_OF)) == base


def test_11_cp1251_file_is_read(workdir):
    p = next(workdir.glob("03_contract.txt"))
    p.write_bytes(p.read_text(encoding="utf-8").encode("cp1251"))
    assert by_number(run(workdir))["STN-102"].amount.value == Decimal(840000)


# --- 12. Точность цитаты и координат ---------------------------------------------------------------
def _all_evidence(result):
    for c in result.contracts:
        if c.number_evidence:
            yield c.number_evidence
        for f in (c.supplier, c.amount, c.deadline, c.penalty):
            if f.evidence:
                yield f.evidence
            for h in (*f.history, *f.candidates):
                yield h.evidence
        for p in c.proposals:
            yield p.evidence
        for lst in c.ignored.values():
            for i in lst:
                yield i.evidence


def test_12_quotes_and_coordinates_are_exact(workdir):
    res = run(workdir)
    docs = {d.id: d for d in res.documents}
    raw = {d.name: (workdir / d.name).read_text(encoding="utf-8") for d in res.documents}
    n = 0
    for ev in _all_evidence(res):
        n += 1
        d = docs[ev.doc_id]
        para = d.paragraphs[ev.paragraph - 1]
        assert para.text[ev.start:ev.end] == ev.quote                     # координаты внутри абзаца
        assert raw[ev.file][ev.doc_start:ev.doc_end] == ev.quote           # координаты в полном тексте файла
        assert raw[ev.file].splitlines()[ev.line - 1].strip() == para.text  # номер строки
        assert ev.quote and ev.doc_status and ev.file == d.name
    assert n >= 25


def test_12_quote_contains_the_value(workdir):
    res = run(workdir)
    digits = lambda s: re.sub(r"\D", "", s)
    for c in res.contracts:
        for f in (c.amount, c.deadline):
            if f.state == CONFIRMED:
                assert digits(f.display) in digits(f.evidence.quote)


def test_12_every_value_has_the_required_fields(workdir):
    res = run(workdir)
    for c in res.contracts:
        for f in (c.supplier, c.amount, c.deadline, c.penalty):
            if f.state == CONFIRMED:
                ev = f.evidence
                assert None not in (f.value, ev.file, ev.quote, ev.paragraph, ev.start, ev.end, ev.doc_status)
                assert ev.end > ev.start


# --- Неустойка (дополнительно) ---------------------------------------------------------------------
def test_penalty_rates_and_missing(workdir):
    res = by_number(run(workdir))
    assert res["STN-101"].penalty.display == "0,1% в день, не более 10%"
    assert res["STN-102"].penalty.display == "0,05% в день, не более 5%"
    assert (res["STN-103"].penalty.state, res["STN-103"].penalty.value) == (NO_DATA, None)
    assert "не установлена" in res["STN-103"].penalty.evidence.quote
    assert not res["STN-101"].penalty.changed   # допсоглашение: «размер неустойки без изменений»


# --- Нет вшитых значений ------------------------------------------------------------------------------
def _source_files():
    return [*sorted((ROOT / "textnav").glob("*.py")), ROOT / "app.py"]


def test_no_hardcoded_values_from_documents():
    """Все числа, даты, номера, названия и имена файлов из документов не должны встречаться в коде."""
    corpus = "\n".join(p.read_text(encoding="utf-8") for p in sorted(ORIGINALS.glob("*.txt")))
    needles = set(re.findall(r"\d{2}\.\d{2}\.\d{4}", corpus))
    needles |= set(re.findall(r"\d{1,3}(?: \d{3})+", corpus))
    needles |= set(re.findall(r"[A-ZА-Я]{2,}-\d+", corpus))
    needles |= set(re.findall(r"«([^»]+)»", corpus))
    needles |= {p.name for p in ORIGINALS.glob("*.txt")} | {p.stem for p in ORIGINALS.glob("*.txt")}
    assert len(needles) > 15
    for path in _source_files():
        src = path.read_text(encoding="utf-8")
        for needle in needles:
            assert needle not in src, f"{path.name}: вшитое значение из документов {needle!r}"


def test_core_package_has_no_dates_at_all():
    for path in sorted((ROOT / "textnav").glob("*.py")):
        assert "2026" not in path.read_text(encoding="utf-8"), path.name


def test_output_does_not_depend_on_unrelated_document_values(workdir):
    """Заменим ВСЕ значения во всех документах — результат должен следовать за ними, а не за старыми."""
    for p in workdir.glob("*.txt"):
        t = p.read_text(encoding="utf-8")
        p.write_text(t.replace("1 200 000", "7 000 000").replace("15.04.2026", "01.06.2026"), encoding="utf-8")
    c = by_number(run(workdir, only=["01_contract"]))["STN-101"]
    assert (c.amount.value, c.deadline.display) == (Decimal(7000000), "01.06.2026")
    assert "1 200 000" not in json.dumps(c.amount.evidence.quote)
