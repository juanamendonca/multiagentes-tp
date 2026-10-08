"""Pestaña "Explorar tablas" (FR-051, FR-052).

Lista las tablas de ``TABLES_DIR`` (``TableStore.list_records()``, es decir, ``*.json``) agrupadas por
documento, con nombre, descripción, fuente y un expander con la tabla completa. Cada documento se
puede borrar (con confirmación): primero sus vectores de Pinecone y después sus archivos locales.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

import streamlit as st

from agents.ingestion.schemas import TableRecord
from services.table_store import TableStore, doc_prefix
from services.vectorstore import VectorStore, delete_stale
from ui.errors import run_safely
from ui.render import lazy_expander, render_record, source_text

VectorStoreProvider = Callable[[], VectorStore | None]

CONFIRM_KEY = "explore_confirm_delete"
NOTICE_KEY = "explore_notice"
COUNTS_KEY = "explore_vector_counts"
DELETE_WAIT_S = 20.0
DELETE_POLL_S = 2.0


@dataclass
class Document:
    """Tablas de un mismo PDF (mismo ``doc_hash``)."""

    doc_hash: str
    file_name: str
    records: list[TableRecord] = field(default_factory=list)

    @property
    def prefix(self) -> str:
        return doc_prefix(self.doc_hash)


def group_by_document(records: list[TableRecord]) -> list[Document]:
    """Agrupa los registros por ``doc_hash`` (orden: nombre de archivo; tablas por página)."""
    docs: dict[str, Document] = {}
    for record in records:
        doc = docs.setdefault(record.doc_hash, Document(record.doc_hash, record.source.file_name))
        doc.records.append(record)
    for doc in docs.values():
        doc.records.sort(key=lambda r: (r.source.pages[:1], r.table_id))
    return sorted(docs.values(), key=lambda d: (d.file_name.lower(), d.doc_hash))


@dataclass
class DeleteOutcome:
    doc_hash: str
    vectors_deleted: list[str]
    files_deleted: list[str]
    remaining_vectors: int | None  # None: no hay índice de Pinecone


def delete_document(
    doc_hash: str,
    *,
    table_store: TableStore,
    vector_store: VectorStore | None,
    wait_s: float = DELETE_WAIT_S,
    poll_s: float = DELETE_POLL_S,
    sleep: Callable[[float], None] = time.sleep,
) -> DeleteOutcome:
    """FR-052: borra los vectores del documento (ids con prefijo ``doc_hash[:16]-``) y después sus
    Parquet/JSON. Espera hasta ``wait_s`` a que Pinecone deje de listar ids del documento (consistencia
    eventual) y devuelve cuántos quedan. Si falla Pinecone, no toca los archivos locales."""
    vectors: list[str] = []
    remaining: int | None = None
    if vector_store is not None:
        vectors = delete_stale(vector_store, doc_hash, [])
        prefix = doc_prefix(doc_hash)
        remaining = len(vector_store.list_ids(prefix))
        deadline = time.monotonic() + wait_s
        while remaining and time.monotonic() < deadline:
            sleep(poll_s)
            remaining = len(vector_store.list_ids(prefix))
    files = table_store.delete_document(doc_hash)
    return DeleteOutcome(doc_hash, vectors, files, remaining)


def count_document_vectors(vector_store: VectorStore | None, docs: list[Document]) -> dict[str, int]:
    """Vectores en Pinecone por documento (``prefix → n``) y el total del índice (``"*"``)."""
    if vector_store is None:
        return {"*": 0, **{doc.prefix: 0 for doc in docs}}
    counts = {doc.prefix: len(vector_store.list_ids(doc.prefix)) for doc in docs}
    counts["*"] = vector_store.count()
    return counts


def _do_delete(doc: Document, table_store: TableStore, provider: VectorStoreProvider) -> DeleteOutcome:
    return delete_document(doc.doc_hash, table_store=table_store, vector_store=provider())


def _render_delete_controls(
    doc: Document, table_store: TableStore, provider: VectorStoreProvider | None
) -> None:
    if provider is None:
        st.caption("Borrar documentos requiere la configuración de Pinecone.")
        return
    if st.session_state.get(CONFIRM_KEY) != doc.doc_hash:
        if st.button("Borrar documento", key=f"delete_{doc.doc_hash}", icon=":material/delete:"):
            st.session_state[CONFIRM_KEY] = doc.doc_hash
            st.rerun()
        return
    st.warning(
        f"¿Borrar **{doc.file_name}**? Se borran sus {len(doc.records)} tablas de `data/tables/` y sus "
        "vectores de Pinecone. Para recuperarlo hay que volver a ingerir el PDF."
    )
    yes, no = st.columns(2)
    if yes.button(
        "Confirmar borrado", key=f"confirm_{doc.doc_hash}", type="primary", icon=":material/delete_forever:"
    ):
        st.session_state.pop(CONFIRM_KEY, None)
        with st.spinner(f"Borrando {doc.file_name}…"):
            outcome = run_safely("Pinecone", _do_delete, doc, table_store, provider)
        if outcome is not None:
            remaining = (
                "sin índice de Pinecone"
                if outcome.remaining_vectors is None
                else f"vectores del documento que quedan en Pinecone: {outcome.remaining_vectors}"
            )
            st.session_state[NOTICE_KEY] = (
                f"Se borró {doc.file_name}: {len(outcome.vectors_deleted)} vectores y "
                f"{len(outcome.files_deleted)} tablas locales ({remaining})."
            )
            counts = st.session_state.get(COUNTS_KEY)
            if counts is not None:
                counts.pop("*", None)  # el total quedó viejo: se vuelve a contar con el botón
            st.rerun()
    if no.button("Cancelar", key=f"cancel_{doc.doc_hash}"):
        st.session_state.pop(CONFIRM_KEY, None)
        st.rerun()


def render_explore_tab(table_store: TableStore, provider: VectorStoreProvider | None) -> None:
    st.subheader("Explorar tablas")
    notice = st.session_state.pop(NOTICE_KEY, None)
    if notice:
        st.success(notice)
    docs = group_by_document(table_store.list_records())
    n_tables = sum(len(d.records) for d in docs)
    head, button = st.columns([3, 1])
    head.caption(
        f"{n_tables} tablas indexadas en {len(docs)} documentos (`{table_store.tables_dir.as_posix()}`)."
    )
    if provider is not None and button.button("Contar vectores en Pinecone", icon=":material/database:"):
        counts = run_safely("Pinecone", count_document_vectors, provider(), docs)
        if counts is not None:
            st.session_state[COUNTS_KEY] = counts
    counts: dict[str, int] | None = st.session_state.get(COUNTS_KEY)
    if counts is not None and "*" in counts:
        st.caption(f"Vectores en el índice de Pinecone: {counts['*']}")
    if not docs:
        st.info("Todavía no hay tablas: subí un PDF en la pestaña «Subir PDF».")
        return
    for doc in docs:
        with st.container(border=True):
            st.markdown(f"### {doc.file_name}")
            caption = f"{len(doc.records)} tablas · doc_hash `{doc.doc_hash[:16]}…`"
            if counts is not None and doc.prefix in counts:
                caption += f" · vectores en Pinecone: {counts[doc.prefix]}"
            st.caption(caption)
            _render_delete_controls(doc, table_store, provider)
            for record in doc.records:
                st.markdown(f"**{record.name}**")
                st.markdown(record.description)
                st.caption(f"Fuente: {source_text(record.source.file_name, record.source.pages)}")
                lazy_expander(
                    "Ver tabla completa",
                    f"exp_{record.table_id}",
                    lambda r=record: render_record(r, table_store, preview_rows=None, show_description=False),
                )
