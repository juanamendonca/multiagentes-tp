"""Pestaña "Preguntar" (FR-053, FR-055, FR-056, FR-054).

Chat de sesión: cada pregunta válida se agrega a ``st.session_state[HISTORY_KEY]`` y se responde
con ``run_analyst(..., on_update=...)``, mostrando en vivo el nodo en curso. Cada respuesta muestra
el texto, un badge de estado, los datos faltantes, las tablas usadas (con sus datos), el código
generado, la salida de la ejecución, los reintentos y el link a la traza de LangSmith. Si el
Analista falla, el error queda en el historial como una respuesta con error (FR-054).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import pandas as pd
import streamlit as st

from agents.analyst.schemas import AnalystResult, Candidate
from services.table_store import TableStore
from ui.errors import run_safely
from ui.render import lazy_expander, load_table_or_none, source_text, status_badge

UpdateCallback = Callable[[str, Mapping[str, Any]], None]
AnalystRunner = Callable[[str, UpdateCallback], AnalystResult]
RunUrlFn = Callable[[str | None], str | None]

HISTORY_KEY = "chat_history"
MAX_QUESTION_CHARS = 1000

NODE_LABELS: dict[str, str] = {
    "understand_query": "Entendiendo la pregunta",
    "retrieve": "Buscando tablas en Pinecone",
    "select_tables": "Eligiendo tablas",
    "generate_code": "Generando código",
    "execute": "Ejecutando el código en el sandbox",
    "decide_status": "Decidiendo el estado",
    "respond": "Redactando la respuesta",
}


def validate_question(text: str | None) -> str | None:
    """FR-056: ``None`` si la pregunta es válida; si no, el aviso a mostrar (vacía o solo espacios,
    o más de ``MAX_QUESTION_CHARS`` caracteres sin contar espacios de los extremos)."""
    question = (text or "").strip()
    if not question:
        return "La pregunta está vacía: escribí una pregunta sobre las tablas indexadas."
    if len(question) > MAX_QUESTION_CHARS:
        return (
            f"La pregunta tiene {len(question)} caracteres; el máximo es {MAX_QUESTION_CHARS}. "
            "Acortala y volvé a enviarla."
        )
    return None


@dataclass
class ChatTurn:
    """Un mensaje del historial: pregunta del usuario o respuesta del Analista (o su error)."""

    role: str  # "user" | "assistant"
    content: str
    result: AnalystResult | None = None
    run_url: str | None = None
    error: str | None = None
    progress: list[str] = field(default_factory=list)


def describe_update(node: str, update: Mapping[str, Any]) -> str:
    """Una línea por evento del ``stream`` del Analista."""
    if node == "understand_query":
        plan = update.get("plan")
        parts = len(plan.parts) if plan is not None else 0
        return f"understand_query: {parts} partes en el plan"
    if node == "retrieve":
        line = f"retrieve: {len(update.get('candidates') or [])} tablas candidatas"
        if update.get("warnings"):
            line += f" · {len(update['warnings'])} avisos"
        return line
    if node == "select_tables":
        selection = update.get("selection")
        if selection is None:
            return "select_tables: sin selección"
        covered = len(selection.covered_part_ids)
        return (
            f"select_tables: {len(selection.selected_table_ids)} tablas elegidas · "
            f"{covered} de {len(selection.coverage)} partes cubiertas"
        )
    if node == "generate_code":
        return f"generate_code: intento {update.get('code_attempts', '?')}"
    if node == "execute":
        execution = update.get("execution")
        if execution is None:
            return "execute: sin ejecución"
        line = f"execute: {execution.status} en {execution.duration_s:.1f} s"
        if execution.error:
            line += f" — {execution.error[:120]}"
        return line
    if node == "decide_status":
        return f"decide_status: {update.get('status')}"
    if node == "respond":
        return "respond: respuesta redactada"
    return f"{node}: actualizado"


def submit_question(
    raw: str | None,
    history: list[ChatTurn],
    runner: AnalystRunner,
    *,
    on_update: UpdateCallback | None = None,
    run_url: RunUrlFn | None = None,
) -> ChatTurn | None:
    """Valida la pregunta (FR-056) y, si es válida, la agrega al historial, corre el Analista con
    ``run_safely`` (FR-054) y agrega la respuesta (o el error). Devuelve la respuesta, o ``None`` si
    la pregunta no pasó la validación (en ese caso el Analista no se ejecuta)."""
    problem = validate_question(raw)
    if problem is not None:
        st.warning(problem)
        return None
    question = (raw or "").strip()
    history.append(ChatTurn(role="user", content=question))
    errors: list[str] = []
    progress: list[str] = []

    def record(node: str, update: Mapping[str, Any]) -> None:
        progress.append(describe_update(node, update))
        if on_update is not None:
            on_update(node, update)

    result = run_safely(
        "Analista", runner, question, record, on_error=lambda service, msg: errors.append(f"{service}: {msg}")
    )
    if result is None:
        turn = ChatTurn(
            role="assistant", content="", error=errors[0] if errors else "Error", progress=progress
        )
    else:
        url = None
        if run_url is not None:
            try:
                url = run_url(result.run_id)
            except Exception:  # el link es opcional
                url = None
        turn = ChatTurn(
            role="assistant", content=result.answer, result=result, run_url=url, progress=progress
        )
    history.append(turn)
    return turn


def format_value(value: Any) -> str:
    if isinstance(value, dict):
        return "; ".join(f"{k}: {format_value(v)}" for k, v in value.items())
    if isinstance(value, list):
        return ", ".join(format_value(v) for v in value)
    if isinstance(value, float):
        text = f"{value:,.4f}".rstrip("0").rstrip(".")
        return text.replace(",", "_").replace(".", ",").replace("_", ".")
    return str(value)


def results_frame(result: AnalystResult) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "parte": [r.part_id for r in result.results],
            "resultado": [r.label for r in result.results],
            "valor": [format_value(r.value) for r in result.results],
            "unidad": [r.unit or "" for r in result.results],
        }
    )


def render_trace_link(turn: ChatTurn) -> None:
    result = turn.result
    if result is None or not result.run_id:
        st.caption("Traza de LangSmith: no disponible (trazas desactivadas).")
        return
    if turn.run_url:
        st.markdown(
            f"[Ver la traza en LangSmith]({turn.run_url}) "
            "<small>(puede tardar unos segundos en aparecer)</small>",
            unsafe_allow_html=True,
        )
    else:
        st.caption(f"Traza de LangSmith: run `{result.run_id}` (no se pudo armar el link).")


def _render_candidate(candidate: Candidate, table_store: TableStore) -> None:
    st.caption(candidate.description)
    df = load_table_or_none(table_store, candidate.table_id)
    if df is None:
        st.warning(f"No hay datos locales para `{candidate.table_id}`.")
    else:
        st.dataframe(df, hide_index=True, width="stretch")


def render_answer(turn: ChatTurn, table_store: TableStore, key: str = "answer") -> None:
    """Respuesta del Analista (FR-053): texto, estado, faltantes, valores, tablas, código, salida,
    reintentos y traza."""
    if turn.error is not None:
        st.error(f"No se pudo responder. {turn.error}")
        if turn.progress:
            st.caption("Último paso: " + turn.progress[-1])
        return
    result = turn.result
    if result is None:
        st.markdown(turn.content)
        return
    status_badge(result.status)
    st.markdown(result.answer or "_(sin respuesta)_")
    retries = max(result.code_attempts - 1, 0)
    st.caption(
        f"Reintentos de código: {retries} · intentos: {result.code_attempts} · "
        f"llamadas al LLM: {result.llm_calls} · {result.elapsed_s:.0f} s"
    )
    if result.status != "cannot_answer" and result.results:
        st.markdown("**Valores calculados**")
        st.dataframe(results_frame(result), hide_index=True, width="stretch")
        if result.conversions:
            st.caption("Conversiones: " + "; ".join(result.conversions))
    if result.status != "answered" and result.missing:
        st.markdown("**Datos faltantes**")
        for item in result.missing:
            line = f"- **{item.part_id}**: {item.reason}"
            if item.related_table_note:
                line += f" _(tabla relacionada: {item.related_table_note})_"
            st.markdown(line)
    for warning in result.warnings:
        st.warning(warning)
    tables = result.selected_tables
    if tables:
        st.markdown("**Tablas seleccionadas**")
        for alias, candidate in enumerate(tables, start=1):
            label = f"t{alias} · {candidate.name} — {source_text(candidate.file_name, candidate.pages)}"
            lazy_expander(
                label,
                f"{key}_t{alias}",
                lambda c=candidate: _render_candidate(c, table_store),
                icon=":material/table:",
            )
    if result.code:
        with st.expander(f"Código generado (intento {result.code_attempts})", icon=":material/code:"):
            st.code(result.code, language="python")
    execution = result.execution
    if execution is not None:
        with st.expander(f"Salida de la ejecución: {execution.status}", icon=":material/terminal:"):
            st.caption(
                f"returncode: {execution.returncode} · duración: {execution.duration_s:.2f} s"
                + (" · salida truncada" if execution.truncated else "")
            )
            if execution.error:
                st.error(execution.error)
            st.markdown("stdout")
            st.code(execution.stdout or "(vacío)", language=None)
            if execution.stderr:
                st.markdown("stderr")
                st.code(execution.stderr, language=None)
    if turn.progress:
        with st.expander("Pasos del grafo", icon=":material/account_tree:"):
            st.code("\n".join(turn.progress), language=None)
    render_trace_link(turn)


def render_ask_tab(
    runner: AnalystRunner | None,
    table_store: TableStore,
    disabled_reason: str | None,
    run_url: RunUrlFn | None = None,
) -> None:
    st.subheader("Preguntar")
    history: list[ChatTurn] = st.session_state.setdefault(HISTORY_KEY, [])
    if runner is None:
        st.info(disabled_reason or "El Analista no está disponible.")
    chat = st.container()
    question = st.chat_input("Preguntá sobre las tablas indexadas…", disabled=runner is None, key="ask_input")
    with chat:
        for index, turn in enumerate(history):
            with st.chat_message(turn.role):
                if turn.role == "user":
                    st.markdown(turn.content)
                else:
                    render_answer(turn, table_store, key=f"turn{index}")
        if question is None or runner is None:
            return
        problem = validate_question(question)
        if problem is not None:  # FR-056: no se ejecuta el Analista
            st.warning(problem)
            return
        with st.chat_message("user"):
            st.markdown(question.strip())
        with st.chat_message("assistant"):
            status = st.status("Analizando la pregunta…", expanded=False)

            def on_update(node: str, update: Mapping[str, Any]) -> None:
                status.write(describe_update(node, update))
                status.update(label=NODE_LABELS.get(node, node) + "…")

            turn = submit_question(question, history, runner, on_update=on_update, run_url=run_url)
            if turn is None:  # no pasa: ya se validó arriba
                return
            status.update(
                label="Error" if turn.error else "Listo", state="error" if turn.error else "complete"
            )
            render_answer(turn, table_store, key=f"turn{len(history) - 1}")
