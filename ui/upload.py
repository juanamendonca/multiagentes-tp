"""Pestaña "Subir PDF" (FR-050, FR-025, FR-026, FR-029).

Por cada PDF subido corre ``run_ingestion(..., on_update=...)`` dentro de un ``st.status``: cada
evento del ``stream`` del grafo agrega una línea con el nodo y sus contadores (tablas detectadas,
vuelta de enriquecimiento/validación, inválidas, avisos). Al terminar, el resultado queda en
``st.session_state`` y se muestra un resumen por tabla con sus avisos.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import pandas as pd
import streamlit as st

from agents.ingestion.graph import IngestionResult
from services.table_store import TableStore
from ui.errors import run_safely
from ui.render import lazy_expander, render_record, source_text

UpdateCallback = Callable[[str, Mapping[str, Any]], None]
IngestionRunner = Callable[[str, bytes, UpdateCallback], IngestionResult]

RESULTS_KEY = "ingestion_results"
REINGEST_HINT = "Reingerir el mismo PDF completa la ingesta sin duplicados."

NODE_LABELS: dict[str, str] = {
    "load_pdf": "Leyendo el PDF",
    "extract_tables": "Extrayendo tablas",
    "collect_context": "Recolectando contexto",
    "enrich": "Enriqueciendo con el LLM",
    "validate": "Validando metadatos",
    "repair": "Reparando metadatos",
    "index": "Indexando en Pinecone",
}


@dataclass
class IngestionProgress:
    """Acumula los eventos de ``stream`` de la Ingesta y los describe en una línea cada uno."""

    file_name: str = ""
    n_pages: int = 0
    n_detected: int = 0
    enrich_round: int = 0
    n_invalid: int = 0
    n_warnings: int = 0
    llm_calls: int = 0
    current: str = ""
    lines: list[str] = field(default_factory=list)

    def update(self, node: str, update: Mapping[str, Any]) -> str:
        """Registra el evento ``{node: update}`` y devuelve la línea que lo describe."""
        self.current = node
        self.n_warnings += len(update.get("warnings") or [])
        tables = update.get("tables") or []
        if node == "load_pdf":
            if update.get("status") == "pdf_error":
                line = f"load_pdf: {update.get('message') or 'no se pudo abrir el PDF'}"
            else:
                self.n_pages = len(update.get("pages") or [])
                line = f"load_pdf: {self.n_pages} páginas leídas"
        elif node == "extract_tables":
            self.n_detected = len(tables)
            line = f"extract_tables: {self.n_detected} tablas detectadas · {self.n_warnings} avisos"
            if update.get("status") == "no_tables":
                line += " · no se encontraron tablas legibles"
        elif node == "collect_context":
            line = f"collect_context: contexto de {len(tables)} tablas"
        elif node == "enrich":
            self.enrich_round = int(update.get("enrich_round") or self.enrich_round + 1)
            calls = int(update.get("llm_calls") or 0)
            self.llm_calls += calls
            line = f"enrich: intento {self.enrich_round} · {calls} llamadas al LLM"
        elif node == "validate":
            self.n_invalid = sum(1 for t in tables if getattr(t, "status", "") == "invalid")
            valid = len(tables) - self.n_invalid
            line = (
                f"validate: intento de validación {self.enrich_round} · {valid} válidas · "
                f"{self.n_invalid} inválidas"
            )
        elif node == "repair":
            repaired = sum(1 for t in tables if getattr(t, "status", "") == "repaired")
            line = f"repair: {repaired} tablas con metadatos degradados (reparación determinística)"
        elif node == "index":
            line = f"index: {update.get('message') or str(update.get('n_indexed', 0)) + ' tablas indexadas'}"
        else:
            line = f"{node}: actualizado"
        self.lines.append(line)
        return line

    @property
    def label(self) -> str:
        """Etiqueta del ``st.status`` mientras corre: nodo en curso y contadores."""
        parts = [NODE_LABELS.get(self.current, self.current) or "Iniciando"]
        if self.n_detected:
            parts.append(f"{self.n_detected} tablas")
        if self.enrich_round:
            parts.append(f"intento {self.enrich_round}")
        return f"{self.file_name}: " + " · ".join(parts) + "…"


def warnings_frame(result: IngestionResult) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "página": [w.page if w.page is not None else "" for w in result.warnings],
            "tabla": [w.table_ref or "" for w in result.warnings],
            "motivo": [w.reason for w in result.warnings],
        }
    )


def result_message(result: IngestionResult) -> tuple[str, str]:
    """``(nivel, texto)`` del resumen: ``success`` | ``warning`` | ``error`` (FR-026/027/029)."""
    if result.status == "ok":
        text = f"{result.message} en {result.elapsed_s:.0f} s."
        if result.deleted_ids:
            text += f" Se borraron {len(result.deleted_ids)} tablas obsoletas de una ingesta anterior."
        return "success", text
    if result.status == "no_tables":
        return "warning", "No se encontraron tablas legibles."
    if result.status == "pdf_error":
        return "error", result.message or "No se pudo abrir el PDF."
    message = result.message or "Falló la indexación."
    if "Reingerir" not in message:
        message += " " + REINGEST_HINT
    return "error", message


def render_result(
    result: IngestionResult, table_store: TableStore, progress_lines: list[str], key: str = "result"
) -> None:
    """Resumen de una ingesta terminada (FR-025): mensaje, avisos y detalle por tabla."""
    st.markdown(f"#### {result.file_name}")
    level, text = result_message(result)
    getattr(st, level)(text)
    if result.n_degraded:
        st.warning(
            f"{result.n_degraded} tablas quedaron con metadatos degradados (se reconstruyeron desde "
            "los datos tras agotar los reintentos de validación)."
        )
    st.caption(
        f"{result.n_detected} tablas detectadas · {result.n_indexed} indexadas · "
        f"{len(result.warnings)} avisos · {result.llm_calls} llamadas al LLM · "
        f"{result.enrich_rounds} vueltas de enriquecimiento"
    )
    if result.warnings:
        with st.expander(f"Avisos ({len(result.warnings)})", icon=":material/warning:"):
            st.dataframe(warnings_frame(result), hide_index=True, width="stretch")
    if progress_lines:
        with st.expander("Pasos del grafo", icon=":material/account_tree:"):
            st.code("\n".join(progress_lines), language=None)
    for i, record in enumerate(result.records):
        label = f"{record.name} — {source_text(record.source.file_name, record.source.pages)}"
        lazy_expander(
            label, f"{key}_{record.table_id}", lambda r=record: render_record(r, table_store), expanded=i == 0
        )


def ingest_files(files: list[Any], runner: IngestionRunner) -> list[tuple[IngestionResult, list[str]]]:
    """Ingiere cada archivo mostrando el progreso en vivo; devuelve los que terminaron."""
    done: list[tuple[IngestionResult, list[str]]] = []
    for uploaded in files:
        progress = IngestionProgress(file_name=uploaded.name)
        with st.status(f"{uploaded.name}: iniciando…", expanded=True) as status:

            def on_update(node: str, update: Mapping[str, Any], *, _p=progress, _s=status) -> None:
                line = _p.update(node, update)
                _s.write(line)
                _s.update(label=_p.label, expanded=True)

            result = run_safely("Ingesta", runner, uploaded.name, uploaded.getvalue(), on_update)
            if result is None:
                status.update(label=f"{uploaded.name}: error", state="error", expanded=True)
                continue
            final_state = "complete" if result.status in ("ok", "no_tables") else "error"
            status.update(
                label=f"{uploaded.name}: {result_message(result)[1]}", state=final_state, expanded=False
            )
        done.append((result, progress.lines))
    return done


def render_upload_tab(
    runner: IngestionRunner | None, table_store: TableStore, disabled_reason: str | None
) -> None:
    st.subheader("Subir PDF")
    if runner is None:
        st.info(disabled_reason or "La ingesta no está disponible.")
        return
    st.caption(
        "Subí uno o más PDFs con capa de texto. Cada uno pasa por el Agente de Ingesta: extracción de "
        "tablas, enriquecimiento con el LLM, validación e indexación en Pinecone."
    )
    with st.form("upload_form", clear_on_submit=True):
        files = st.file_uploader("PDFs", type="pdf", accept_multiple_files=True)
        submitted = st.form_submit_button("Ingerir", type="primary", icon=":material/upload:")
    if submitted:
        if not files:
            st.warning("Elegí al menos un PDF.")
        else:
            done = ingest_files(list(files), runner)
            st.session_state.setdefault(RESULTS_KEY, [])
            st.session_state[RESULTS_KEY] = done[::-1] + st.session_state[RESULTS_KEY]
    results = st.session_state.get(RESULTS_KEY) or []
    if results:
        st.divider()
        st.markdown("### Resultados de esta sesión")
        for position, (result, lines) in enumerate(results):
            render_result(result, table_store, lines, key=f"res{len(results) - position}")
