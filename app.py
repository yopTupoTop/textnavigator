"""TextNavigator — интерфейс на Streamlit. Работает только с реально загруженными файлами."""
from __future__ import annotations

import datetime as dt
import html
import itertools
import re

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from textnav import CONFIRMED, NO_DATA, REVIEW, analyze, from_upload
from textnav.models import FIELD_LABELS, ContractResult, Evidence, FieldValue

st.set_page_config(page_title="TextNavigator", layout="wide")

STATE_COLOR = {CONFIRMED: "green", NO_DATA: "gray", REVIEW: "orange", "Неполные данные": "gray"}


def md(s: str) -> str:
    """Экранирование разметки Markdown в цитатах."""
    return re.sub(r"([\\`*_{}\[\]()#+!|>~$])", r"\\\1", s)


def goto(ev: Evidence) -> None:
    st.session_state["view"] = {"doc": ev.doc_id, "file": ev.file, "paragraph": ev.paragraph,
                                "start": ev.start, "end": ev.end}
    st.session_state["viewer_doc"] = ev.doc_id


KEYS = itertools.count()


def show_evidence(ev: Evidence | None, label: str = "Источник") -> None:
    if ev is None:
        return
    st.caption(f"{label}: **{ev.file}** · абзац {ev.paragraph} (строка {ev.line}) · "
               f"позиции в абзаце {ev.start}–{ev.end}, в файле {ev.doc_start}–{ev.doc_end}")
    st.markdown(f"> «{md(ev.quote)}»")
    st.caption(f"Основание: {ev.doc_status}")
    st.button("Перейти к абзацу ↗", key=f"go{next(KEYS)}", on_click=goto, args=(ev,))


def show_field(title: str, fv: FieldValue) -> None:
    with st.container(border=True):
        color = STATE_COLOR.get(fv.state, "gray")
        st.markdown(f"**{title}:** {md(fv.display)}  :{color}[{fv.state}]")
        if fv.changed:
            st.markdown(f"Изменено: {md(fv.source)}. Было: **{md(fv.original or '')}**.")
        elif fv.state == CONFIRMED:
            st.caption("Значение из договора, изменений нет.")
        if fv.note and not fv.changed:
            st.caption(fv.note)
        show_evidence(fv.evidence)
        for cand in fv.candidates:
            st.markdown(f"Кандидат: **{md(cand.display)}** — {md(cand.source)}")
            show_evidence(cand.evidence, "Кандидат")
        if len(fv.history) > 1:
            with st.expander("История значения"):
                for h in fv.history:
                    st.markdown(f"- **{md(h.display)}** — {md(h.source)}")


def build_viewer_html(doc, hl) -> str:
    rows = []
    for p in doc.paragraphs:
        text, hit = html.escape(p.text), ""
        if hl and hl["paragraph"] == p.no:
            s, e = hl["start"], hl["end"]
            text = (html.escape(p.text[:s]) + "<mark>" + html.escape(p.text[s:e]) + "</mark>" + html.escape(p.text[e:]))
            hit = ' id="hit" class="p hit"'
        rows.append(f'<div{hit or " class=p"}><span class="n" title="строка {p.line}">{p.no}</span><span>{text}</span></div>')
    css = ("body{font:14px/1.45 system-ui,sans-serif;margin:0;color:#1c2330;background:#fff}"
           ".p{display:flex;gap:10px;padding:6px 8px;border-bottom:1px solid #e6e9ef}"
           ".n{min-width:24px;text-align:right;color:#667085}.hit{background:#fff3d9}"
           "mark{background:#ffe58a;padding:0 1px;border-radius:2px}")
    js = "var h=document.getElementById('hit');if(h){h.scrollIntoView({block:'center'});}"
    return f"<style>{css}</style>{''.join(rows)}<script>{js}</script>"


def render_viewer(result) -> None:
    st.subheader("Исходный текст")
    docs = {d.id: d for d in result.documents}
    if st.session_state.get("viewer_doc") not in docs:
        st.session_state["viewer_doc"] = next(iter(docs))
    st.selectbox("Документ", list(docs), format_func=lambda i: f"{docs[i].name} — {docs[i].label}", key="viewer_doc")
    doc = docs[st.session_state["viewer_doc"]]
    view = st.session_state.get("view")
    hl = view if view and view["doc"] == doc.id and view["file"] == doc.name else None
    st.caption(doc.status_summary())
    components.html(build_viewer_html(doc, hl), height=560, scrolling=True)


def contracts_table(result) -> pd.DataFrame:
    return pd.DataFrame([{
        "Номер договора": c.number or "—",
        "Поставщик": c.supplier.display,
        "Сумма, ₽": c.amount.display,
        "Срок поставки": c.deadline.display,
        "Неустойка": c.penalty.display,
        "Статус проверки": c.state,
        "Несогласованных предложений": sum(1 for p in c.proposals if p.field != "other"),
    } for c in result.contracts])


def render_details(c: ContractResult) -> None:
    st.markdown(f"#### Договор {c.number}  ·  файл {c.file}")
    show_field("Сумма", c.amount)
    show_field("Срок поставки", c.deadline)
    show_field("Неустойка", c.penalty)
    if c.related:
        st.markdown("**Связанные документы**")
        for r in c.related:
            st.markdown(f"- **{md(r.label)}** ({md(r.file)}) — {md(r.text)}")
    ign = [(k, i) for k, v in c.ignored.items() for i in v]
    if ign:
        with st.expander("Упомянуто в договоре, но не принято за значение"):
            for _, i in ign:
                st.markdown(f"**{md(i.display)}** — {md(i.reason)}")
                show_evidence(i.evidence)
    for f in c.flags:
        st.warning(f)


def render_proposals(result) -> None:
    st.subheader("Предложения об изменении условий")
    st.caption("Письма, проекты, неподписанные и ещё не вступившие в силу соглашения. "
               "Условия договора они не меняют и в таблице выше не учтены.")
    any_ = False
    for c in result.contracts:
        for p in c.proposals:
            any_ = True
            with st.container(border=True):
                st.markdown(f"**{md(c.number or '—')} · {FIELD_LABELS[p.field]}:** предлагается **{md(p.display)}**  "
                            f":orange[не согласовано]")
                st.markdown(f"{md(p.doc_label)} — {md(p.status_label)}")
                if p.note:
                    st.caption(p.note)
                show_evidence(p.evidence)
    if not any_:
        st.write("Предложений об изменении условий не найдено.")


def render_queries(result) -> None:
    st.subheader("Готовые запросы")
    nums = [c.number for c in result.contracts if c.number]
    if not nums:
        return
    num = st.selectbox("Договор для запросов 1–2", nums, key="q_contract")
    c = next(x for x in result.contracts if x.number == num)
    left, right = st.columns(2)
    if left.button("1. Срок поставки на дату проверки", use_container_width=True):
        st.session_state["query"] = 1
    if right.button("2. Сумма договора", use_container_width=True):
        st.session_state["query"] = 2
    if left.button("3. Договоры без суммы в тексте", use_container_width=True):
        st.session_state["query"] = 3
    if right.button("4. Несогласованные изменения срока", use_container_width=True):
        st.session_state["query"] = 4
    q = st.session_state.get("query")
    if q == 1:
        st.markdown(f"**Срок поставки по договору {num} на {result.as_of:%d.%m.%Y}**")
        show_field("Срок поставки", c.deadline)
    elif q == 2:
        st.markdown(f"**Сумма договора {num}**")
        show_field("Сумма", c.amount)
    elif q == 3:
        hits = [x for x in result.contracts if x.amount.state == NO_DATA]
        st.markdown("**Договоры, где сумма в тексте не указана:** " + (", ".join(x.number or "—" for x in hits) or "нет"))
        for x in hits:
            show_evidence(x.amount.evidence, f"Договор {x.number}")
    elif q == 4:
        hits = [x for x in result.contracts if any(p.field == "deadline" for p in x.proposals)]
        st.markdown("**Договоры с несогласованными изменениями срока:** " + (", ".join(x.number or "—" for x in hits) or "нет"))
        for x in hits:
            st.markdown(f"{md(x.number or '—')}: действует **{md(x.deadline.display)}**")
            for p in (p for p in x.proposals if p.field == "deadline"):
                st.markdown(f"предлагается **{md(p.display)}** — {md(p.doc_label)}: {md(p.status_label)}")
                show_evidence(p.evidence)


# ------------------------------------------------------------------ страница
st.title("TextNavigator")
st.caption("Проверка условий договоров поставки: сумма, срок, неустойка — с цитатой из исходного документа.")

with st.sidebar:
    st.header("Параметры")
    as_of = st.date_input("Дата проверки", value=dt.date(2026, 3, 10), format="DD.MM.YYYY",
                          help="На эту дату определяется, какие допсоглашения вступили в силу.")
    st.caption("Условия меняет только допсоглашение, подписанное обеими сторонами и вступившее в силу "
               "не позднее даты проверки. Дата и имя файла приоритет не определяют.")

uploads = st.file_uploader("Загрузите TXT-файлы (договоры, допсоглашения, письма, проекты)",
                           type=["txt"], accept_multiple_files=True)
if not uploads:
    st.info("Загрузите один или несколько TXT-файлов — таблица появится здесь.")
    st.stop()

result = analyze([from_upload(u.name, u.getvalue()) for u in uploads], as_of)
for w in result.warnings:
    st.warning(w)
if not result.contracts:
    st.error("Среди загруженных файлов договоры не найдены.")
    st.stop()

st.subheader(f"Договоры на {as_of:%d.%m.%Y}")
st.dataframe(contracts_table(result), hide_index=True, use_container_width=True)

main, side = st.columns([3, 2], gap="large")
with main:
    render_queries(result)
    st.subheader("Подробности по договору")
    nums = [c.number or c.file for c in result.contracts]
    pick = st.selectbox("Выберите договор", nums, key="details_contract")
    render_details(result.contracts[nums.index(pick)])
    render_proposals(result)
with side:
    render_viewer(result)
