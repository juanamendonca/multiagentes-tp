"""Nodos del grafo de Ingesta (spec §4.2) y sus dependencias inyectables.

Cada nodo recibe el estado (y ``IngestionDeps``) y devuelve **solo** las claves que actualiza;
esas actualizaciones son los eventos de ``graph.stream(stream_mode="updates")``.

Orden: ``load_pdf`` → ``extract_tables`` → ``collect_context`` → ``enrich`` ⇄ ``validate`` →
(``repair``) → ``index``. Los reintentos son **por tabla** (D-021): ``enrich`` solo vuelve a
llamar al LLM para las tablas inválidas a las que les quedan intentos.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

import openai
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel

from agents.ingestion.prompts import build_enrich_messages
from agents.ingestion.schemas import IngestionWarning, TableEnrichment, TableRecord, TableSource
from agents.ingestion.state import IngestionState, TableDraft
from agents.ingestion.validation import apply_unit_hints, repair_enrichment, validate_enrichment
from services.config import Settings
from services.pdf_context import collect_context
from services.pdf_extract import LoadedPdf, PdfOpenError, extract_tables, load_pdf
from services.table_store import TableStore, make_table_id
from services.vectorstore import (
    IndexingError,
    VectorStore,
    build_embedding_text,
    delete_stale,
    embed_texts,
    ensure_index,
    upsert_records,
)

NO_TABLES_MESSAGE = "No se encontraron tablas legibles"
DEFAULT_ENRICH_CONCURRENCY = 4
MAX_ERROR_SUMMARY = 300
# Errores del LLM que cuentan como un intento fallido de esa tabla (salida inválida). El resto
# (conexión, autenticación, presupuesto, 4xx/5xx del proxy) corta la ingesta y lo informa la UI.
RECOVERABLE_LLM_ERRORS: tuple[type[BaseException], ...] = (
    ValueError,  # incluye ValidationError de Pydantic y OutputParserException
    openai.LengthFinishReasonError,
    openai.ContentFilterFinishReasonError,
)


@dataclass
class IngestionDeps:
    """Dependencias del grafo, inyectables para tests.

    - ``chat_model``: se usa como ``chat_model.with_structured_output(TableEnrichment)``.
    - ``embeddings``: modelo de embeddings (``embed_documents`` en lotes de 64).
    - ``vector_store_factory``: devuelve el ``VectorStore``; se llama **una sola vez** y recién en
      ``index`` (un PDF sin tablas no toca Pinecone, FR-026). Por defecto ``ensure_index``.
    - ``table_store``: almacén Parquet + JSON (``TABLES_DIR``).
    - ``enrich_concurrency``: llamadas de enriquecimiento en paralelo (1 = en orden).
    """

    settings: Settings
    chat_model: BaseChatModel
    embeddings: Embeddings
    vector_store_factory: Callable[[], VectorStore]
    table_store: TableStore
    enrich_concurrency: int = DEFAULT_ENRICH_CONCURRENCY
    _vector_store: VectorStore | None = field(default=None, init=False, repr=False)

    @property
    def max_attempts(self) -> int:
        """Intentos de enriquecimiento por tabla: ``1 + MAX_ENRICH_RETRIES`` (BR-011)."""
        return 1 + self.settings.max_enrich_retries

    def vector_store(self) -> VectorStore:
        if self._vector_store is None:
            self._vector_store = self.vector_store_factory()
        return self._vector_store


def build_default_deps(
    settings: Settings, *, enrich_concurrency: int = DEFAULT_ENRICH_CONCURRENCY
) -> IngestionDeps:
    """Dependencias reales: chat y embeddings del proxy LiteLLM, Pinecone (``ensure_index``,
    perezoso) y ``TableStore(settings.tables_dir)``. No hace llamadas de red al construirse."""
    from services.llm import get_chat_model, get_embeddings

    return IngestionDeps(
        settings=settings,
        chat_model=get_chat_model(settings),
        embeddings=get_embeddings(settings),
        vector_store_factory=lambda: ensure_index(settings),
        table_store=TableStore(settings.tables_dir),
        enrich_concurrency=enrich_concurrency,
    )


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _loaded(state: IngestionState) -> LoadedPdf:
    return LoadedPdf(
        file_name=state["file_name"],
        doc_hash=state["doc_hash"],
        pdf_bytes=state["pdf_bytes"],
        pages=state["pages"],
        median_font_size=state["median_font_size"],
    )


def _summary(errors: list[str]) -> str:
    text = " | ".join(errors)
    return text if len(text) <= MAX_ERROR_SUMMARY else text[: MAX_ERROR_SUMMARY - 1] + "…"


# --- nodos -----------------------------------------------------------------------------------------


def load_pdf_node(state: IngestionState) -> dict[str, Any]:
    """FR-010 / FR-027: abre el PDF; si no se puede, deja el error y el grafo termina."""
    try:
        loaded = load_pdf(state["file_name"], state["pdf_bytes"])
    except PdfOpenError as exc:
        return {
            "doc_hash": sha256_hex(state["pdf_bytes"]),
            "status": "pdf_error",
            "error": str(exc),
            "message": str(exc),
            "records": [],
            "n_indexed": 0,
        }
    return {
        "doc_hash": loaded.doc_hash,
        "pages": loaded.pages,
        "median_font_size": loaded.median_font_size,
        "status": "running",
    }


def extract_tables_node(state: IngestionState, deps: IngestionDeps) -> dict[str, Any]:
    """FR-011..FR-017, FR-028: tablas lógicas normalizadas, con su ``table_id`` (BR-010)."""
    loaded = _loaded(state)
    tables, warnings = extract_tables(loaded, max_tables=deps.settings.max_tables_per_pdf)
    drafts = [
        TableDraft(table_id=make_table_id(loaded.doc_hash, t.pages[0], t.index_on_page), raw=t)
        for t in tables
    ]
    update: dict[str, Any] = {"tables": drafts, "n_detected": len(drafts), "warnings": warnings}
    if not drafts:  # FR-026
        update |= {"status": "no_tables", "message": NO_TABLES_MESSAGE, "records": [], "n_indexed": 0}
    return update


def collect_context_node(state: IngestionState) -> dict[str, Any]:
    """FR-018 / BR-006: contexto arriba/abajo y sección de cada tabla."""
    loaded = _loaded(state)
    raws = [d.raw for d in state["tables"]]
    return {"tables": [replace(d, context=collect_context(loaded, d.raw, raws)) for d in state["tables"]]}


def _needs_enrichment(draft: TableDraft, max_attempts: int) -> bool:
    return draft.status in ("pending", "invalid") and draft.attempts < max_attempts


def enrich_node(state: IngestionState, deps: IngestionDeps) -> dict[str, Any]:
    """FR-019 / FR-021: una llamada de salida estructurada por tabla pendiente o inválida con
    intentos disponibles; en un reintento el prompt incluye los errores de validación.

    Tras este nodo, una tabla enriquecida queda ``pending`` (a validar); si el LLM devolvió una
    salida que no parsea, queda ``invalid`` con ese error (cuenta como intento).
    """
    drafts = list(state["tables"])
    todo = [i for i, d in enumerate(drafts) if _needs_enrichment(d, deps.max_attempts)]
    prompts = [
        build_enrich_messages(
            drafts[i],
            state["file_name"],
            feedback=drafts[i].errors if drafts[i].status == "invalid" else (),
        )
        for i in todo
    ]
    structured = deps.chat_model.with_structured_output(TableEnrichment)
    outputs = (
        structured.batch(prompts, config={"max_concurrency": deps.enrich_concurrency}, return_exceptions=True)
        if prompts
        else []
    )
    for i, output in zip(todo, outputs, strict=True):
        draft = drafts[i]
        attempts = draft.attempts + 1
        if isinstance(output, TableEnrichment):
            drafts[i] = replace(draft, enrichment=output, errors=[], attempts=attempts, status="pending")
        elif isinstance(output, RECOVERABLE_LLM_ERRORS):
            message = f"[llm] la respuesta no respetó el formato pedido: {str(output)[:MAX_ERROR_SUMMARY]}"
            drafts[i] = replace(draft, errors=[message], attempts=attempts, status="invalid")
        elif isinstance(output, BaseException):
            raise output
        else:  # pragma: no cover - with_structured_output siempre devuelve el schema
            raise TypeError(f"salida inesperada del LLM: {type(output).__name__}")
    return {"tables": drafts, "enrich_round": state.get("enrich_round", 0) + 1, "llm_calls": len(prompts)}


def validate_node(state: IngestionState) -> dict[str, Any]:
    """FR-020 / BR-007: valida las tablas recién enriquecidas y guarda sus errores."""
    drafts = []
    for draft in state["tables"]:
        if draft.status == "pending" and draft.enrichment is not None:
            errors = validate_enrichment(draft.enrichment, draft.df)
            draft = replace(draft, errors=errors, status="invalid" if errors else "valid")
        drafts.append(draft)
    return {"tables": drafts}


def route_after_validate(state: IngestionState, max_attempts: int) -> str:
    """FR-021 / FR-022: ``enrich`` si alguna tabla inválida tiene intentos; si no, ``repair`` si
    quedan inválidas; si no, ``index``."""
    invalid = [d for d in state["tables"] if d.status == "invalid"]
    if any(d.attempts < max_attempts for d in invalid):
        return "enrich"
    return "repair" if invalid else "index"


def repair_node(state: IngestionState) -> dict[str, Any]:
    """FR-022 / BR-008: reparación determinística de las tablas que agotaron los reintentos;
    quedan ``metadata_quality="degraded"`` y con un aviso."""
    drafts: list[TableDraft] = []
    warnings: list[IngestionWarning] = []
    for draft in state["tables"]:
        if draft.status == "invalid":
            repaired = repair_enrichment(
                draft.enrichment,
                draft.df,
                file_name=state["file_name"],
                first_page=draft.raw.pages[0],
                index_on_page=draft.raw.index_on_page,
                context_above=draft.context.above if draft.context else (),
                unit_hints=draft.raw.unit_hints,
            )
            warnings.append(
                IngestionWarning(
                    page=draft.raw.pages[0],
                    table_ref=draft.table_id,
                    reason=(
                        f"metadatos degradados (metadata_quality=degraded) tras {draft.attempts} "
                        f"intentos de enriquecimiento: {_summary(draft.errors)}"
                    ),
                )
            )
            draft = replace(draft, enrichment=repaired, status="repaired")
        drafts.append(draft)
    return {"tables": drafts, "warnings": warnings}


def build_record(draft: TableDraft, doc_hash: str, file_name: str) -> TableRecord:
    """``TableRecord`` de una tabla validada o reparada (``table_id`` y ``source`` del código)."""
    if draft.enrichment is None or draft.status not in ("valid", "repaired"):
        raise ValueError(f"{draft.table_id}: la tabla no está validada ni reparada ({draft.status})")
    enrichment = apply_unit_hints(draft.enrichment, draft.raw.unit_hints)
    return TableRecord(
        table_id=draft.table_id,
        doc_hash=doc_hash,
        name=enrichment.name,
        description=enrichment.description,
        columns=enrichment.columns,
        row_headers=[h.strip() for h in enrichment.row_headers],
        source=TableSource(file_name=file_name, pages=list(draft.raw.pages)),
        section=draft.context.section if draft.context else None,
        metadata_quality="degraded" if draft.status == "repaired" else "ok",
        validation_attempts=draft.attempts,
    )


def _index_failure(message: str, records: list[TableRecord], indexed: int) -> dict[str, Any]:
    return {
        "status": "index_error",
        "error": message,
        "message": message,
        "records": records,
        "n_indexed": indexed,
    }


def index_node(state: IngestionState, deps: IngestionDeps) -> dict[str, Any]:
    """FR-023 / FR-024 / FR-029 (D-020): índice → archivos → embeddings → upsert → obsoletos.

    El ``VectorStore`` se obtiene primero (crea el índice si no existe; con otra dimensión falla
    sin escribir nada, FR-004). Una falla de embeddings o de Pinecone deja el estado
    ``index_error`` con "k de n tablas indexadas" y no borra nada.
    """
    doc_hash, file_name = state["doc_hash"], state["file_name"]
    drafts = state["tables"]
    total = len(drafts)
    try:
        store = deps.vector_store()
    except Exception as exc:
        return _index_failure(
            f"0 de {total} tablas indexadas; no se pudo usar el índice de Pinecone: {exc}", [], 0
        )

    items = []
    for draft in drafts:
        saved = deps.table_store.save_table(build_record(draft, doc_hash, file_name), draft.df)
        items.append((saved, draft.df))
    records = [record for record, _ in items]
    texts = [build_embedding_text(record) for record in records]
    try:
        vectors = embed_texts(deps.embeddings, texts, expected_dim=deps.settings.embedding_dim)
    except Exception as exc:
        return _index_failure(
            f"0 de {total} tablas indexadas; falló el cálculo de embeddings: {exc}. "
            "Reingerir el mismo PDF completa la ingesta sin duplicados.",
            records,
            0,
        )
    try:
        indexed = upsert_records(store, items, vectors, texts)
    except IndexingError as exc:
        return _index_failure(str(exc), records, exc.indexed)

    keep = [record.table_id for record in records]
    warnings: list[IngestionWarning] = []
    deleted: list[str] = []
    try:
        deleted = delete_stale(store, doc_hash, keep)
    except Exception as exc:  # el documento quedó indexado; solo pueden quedar vectores viejos
        warnings.append(IngestionWarning(reason=f"no se pudieron borrar tablas obsoletas de Pinecone: {exc}"))
    deps.table_store.delete_stale(doc_hash, keep)

    degraded = sum(1 for r in records if r.metadata_quality == "degraded")
    message = f"{indexed} tablas indexadas"
    if degraded:
        message += f" ({degraded} con metadatos degradados)"
    return {
        "records": records,
        "n_indexed": indexed,
        "deleted_ids": deleted,
        "warnings": warnings,
        "status": "ok",
        "message": message,
    }
