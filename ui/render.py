"""Piezas de render compartidas por las pestañas: detalle de una tabla, badges y carga de datos."""

from __future__ import annotations

from collections.abc import Callable, Sequence

import pandas as pd
import streamlit as st

from agents.ingestion.schemas import ColumnMeta, TableRecord
from services.table_store import TableStore
from services.vectorstore import format_pages

STATUS_LABELS: dict[str, tuple[str, str, str]] = {
    # status → (texto, color de st.badge, ícono)
    "answered": ("Respondida", "green", ":material/check_circle:"),
    "partial": ("Parcial", "orange", ":material/rule:"),
    "cannot_answer": ("No se puede responder", "red", ":material/block:"),
}
QUALITY_LABELS: dict[str, tuple[str, str]] = {
    "ok": ("metadatos ok", "green"),
    "degraded": ("metadatos degradados", "orange"),
}
PREVIEW_ROWS = 5


def source_text(file_name: str, pages: Sequence[int]) -> str:
    """``"informe.pdf, págs. 3–4"``."""
    return f"{file_name}, {format_pages(pages)}"


def status_badge(status: str) -> None:
    label, color, icon = STATUS_LABELS.get(status, (status, "gray", ":material/help:"))
    st.badge(label, color=color, icon=icon)  # type: ignore[arg-type]


def quality_badge(quality: str) -> None:
    label, color = QUALITY_LABELS.get(quality, (quality, "gray"))
    st.badge(label, color=color)  # type: ignore[arg-type]


def columns_frame(columns: Sequence[ColumnMeta], dtypes: dict[str, str] | None = None) -> pd.DataFrame:
    """Tabla de columnas (nombre, tipo, unidad, descripción) para mostrar con ``st.dataframe``."""
    dtypes = dtypes or {}
    return pd.DataFrame(
        {
            "columna": [c.name for c in columns],
            "tipo": [c.dtype for c in columns],
            "dtype": [dtypes.get(c.name, "") for c in columns],
            "unidad": [c.unit or "" for c in columns],
            "descripción": [c.description for c in columns],
        }
    )


def lazy_expander(
    label: str, key: str, render: Callable[[], None], *, icon: str | None = None, expanded: bool = False
) -> None:
    """Expander que solo renderiza su contenido cuando está abierto (``on_change="rerun"``): evita
    mandar al navegador decenas de tablas ocultas en cada rerun."""
    container = st.expander(label, expanded=expanded, key=key, icon=icon, on_change="rerun")
    if container.open is False:
        return
    with container:
        render()


def load_table_or_none(table_store: TableStore, table_id: str) -> pd.DataFrame | None:
    """Datos de la tabla, o ``None`` si falta el Parquet (FR-042) o no se puede leer."""
    try:
        return table_store.load_table(table_id)
    except (OSError, ValueError):
        return None


def render_record(
    record: TableRecord,
    table_store: TableStore,
    *,
    preview_rows: int | None = PREVIEW_ROWS,
    show_description: bool = True,
) -> None:
    """Detalle de una tabla (FR-025): descripción, fuente, calidad, columnas con descripción y
    unidad, encabezados de fila y vista previa (``preview_rows`` filas; ``None`` = completa)."""
    if show_description:
        st.markdown(record.description or "_(sin descripción)_")
    cols = st.columns([3, 1])
    with cols[0]:
        st.caption(
            f"Fuente: {source_text(record.source.file_name, record.source.pages)}"
            + (f" · Sección: {record.section}" if record.section else "")
            + f" · {record.n_rows} filas × {record.n_cols} columnas · id `{record.table_id}`"
        )
    with cols[1]:
        quality_badge(record.metadata_quality)
    st.markdown("**Columnas**")
    st.dataframe(columns_frame(record.columns, record.dtypes), hide_index=True, width="stretch")
    if record.row_headers:
        shown = record.row_headers[:30]
        extra = len(record.row_headers) - len(shown)
        st.markdown("**Encabezados de fila:** " + " · ".join(shown) + (f" … (+{extra})" if extra > 0 else ""))
    else:
        st.markdown("**Encabezados de fila:** _(ninguno)_")
    df = load_table_or_none(table_store, record.table_id)
    if df is None:
        st.warning(f"No hay datos locales para `{record.table_id}`: reingerí {record.source.file_name}.")
        return
    if preview_rows is None:
        st.markdown("**Datos**")
        st.dataframe(df, hide_index=True, width="stretch")
    else:
        st.markdown(f"**Vista previa ({min(preview_rows, len(df))} de {len(df)} filas)**")
        st.dataframe(df.head(preview_rows), hide_index=True, width="stretch")
