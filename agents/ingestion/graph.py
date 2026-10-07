"""Grafo de Ingesta (``StateGraph`` de LangGraph) y punto de entrada ``ingest_pdf``.

```
START → load_pdf ─(error)→ END
          └→ extract_tables ─(sin tablas)→ END
               └→ collect_context → enrich → validate ─(inválidas con intentos)→ enrich
                                                     ├─(inválidas agotadas)→ repair → index → END
                                                     └─(todas válidas)→ index → END
```

``run_ingestion`` ejecuta el grafo con ``stream(stream_mode=["updates", "values"])``: cada
actualización ``{nodo: claves_actualizadas}`` se pasa a ``on_update`` (para la UI) y el último
estado completo se convierte en ``IngestionResult``. La traza raíz de LangSmith se llama
``ingestion:<archivo>`` y lleva ``doc_hash``, ``n_tables`` y ``n_warnings`` (FR-060, D-022).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from functools import partial
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from agents.ingestion.nodes import (
    IngestionDeps,
    build_default_deps,
    collect_context_node,
    enrich_node,
    extract_tables_node,
    index_node,
    load_pdf_node,
    repair_node,
    route_after_validate,
    sha256_hex,
    validate_node,
)
from agents.ingestion.schemas import IngestionWarning, TableRecord
from agents.ingestion.state import IngestionState, IngestionStatus
from services.config import load_settings
from services.tracing import make_trace_setup

NODE_NAMES = ("load_pdf", "extract_tables", "collect_context", "enrich", "validate", "repair", "index")
TRACE_PREFIX = "ingestion:"

UpdateCallback = Callable[[str, Mapping[str, Any]], None]


def _route_after_load(state: IngestionState) -> str:
    return "error" if state.get("status") == "pdf_error" else "ok"


def _route_after_extract(state: IngestionState) -> str:
    return "no_tables" if not state.get("tables") else "tables"


def build_ingestion_graph(deps: IngestionDeps) -> CompiledStateGraph:
    """Compila el grafo de Ingesta con las dependencias dadas (fakes en tests)."""
    graph = StateGraph(IngestionState)
    graph.add_node("load_pdf", load_pdf_node)
    graph.add_node("extract_tables", partial(extract_tables_node, deps=deps))
    graph.add_node("collect_context", collect_context_node)
    graph.add_node("enrich", partial(enrich_node, deps=deps))
    graph.add_node("validate", validate_node)
    graph.add_node("repair", repair_node)
    graph.add_node("index", partial(index_node, deps=deps))

    graph.add_edge(START, "load_pdf")
    graph.add_conditional_edges("load_pdf", _route_after_load, {"error": END, "ok": "extract_tables"})
    graph.add_conditional_edges(
        "extract_tables", _route_after_extract, {"no_tables": END, "tables": "collect_context"}
    )
    graph.add_edge("collect_context", "enrich")
    graph.add_edge("enrich", "validate")
    graph.add_conditional_edges(
        "validate",
        partial(route_after_validate, max_attempts=deps.max_attempts),
        {"enrich": "enrich", "repair": "repair", "index": "index"},
    )
    graph.add_edge("repair", "index")
    graph.add_edge("index", END)
    return graph.compile(name="ingestion")


def initial_state(file_name: str, pdf_bytes: bytes) -> IngestionState:
    """Estado de entrada del grafo."""
    return {
        "file_name": file_name,
        "pdf_bytes": pdf_bytes,
        "tables": [],
        "warnings": [],
        "records": [],
        "llm_calls": 0,
        "enrich_round": 0,
        "n_detected": 0,
        "n_indexed": 0,
        "status": "running",
        "error": None,
    }


def trace_metadata(state: Mapping[str, Any]) -> dict[str, Any]:
    """Metadata del run raíz al terminar (FR-060): ``doc_hash``, ``n_tables`` (tablas lógicas
    procesadas), ``n_warnings`` y contadores extra."""
    records = state.get("records") or []
    return {
        "doc_hash": state.get("doc_hash", ""),
        "n_tables": len(state.get("tables") or []),
        "n_warnings": len(state.get("warnings") or []),
        "n_indexed": int(state.get("n_indexed") or 0),
        "n_degraded": sum(1 for r in records if getattr(r, "metadata_quality", "") == "degraded"),
        "llm_calls": int(state.get("llm_calls") or 0),
        "status": state.get("status", ""),
    }


@dataclass
class IngestionResult:
    """Resultado de una ingesta (lo que muestran la UI y el CLI).

    ``status``: ``ok`` | ``no_tables`` (FR-026) | ``pdf_error`` (FR-027) | ``index_error``
    (FR-029 / FR-004). ``message`` es el texto para el usuario. ``records`` son las tablas
    guardadas en ``TABLES_DIR`` (con ``index_error`` puede haber menos indexadas: ``n_indexed``).
    """

    file_name: str
    doc_hash: str
    status: IngestionStatus
    message: str
    records: list[TableRecord] = field(default_factory=list)
    warnings: list[IngestionWarning] = field(default_factory=list)
    n_detected: int = 0
    n_indexed: int = 0
    llm_calls: int = 0
    enrich_rounds: int = 0
    deleted_ids: list[str] = field(default_factory=list)
    run_id: str | None = None
    elapsed_s: float = 0.0

    @property
    def ok(self) -> bool:
        return self.status in ("ok", "no_tables")

    @property
    def n_degraded(self) -> int:
        return sum(1 for r in self.records if r.metadata_quality == "degraded")


def result_from_state(
    state: Mapping[str, Any], *, run_id: str | None = None, elapsed_s: float = 0.0
) -> IngestionResult:
    status = state.get("status", "running")
    if status == "running":  # no debería pasar: todo camino al END fija el estado
        status = "index_error"
    return IngestionResult(
        file_name=state["file_name"],
        doc_hash=state.get("doc_hash", ""),
        status=status,
        message=state.get("message") or "",
        records=list(state.get("records") or []),
        warnings=list(state.get("warnings") or []),
        n_detected=int(state.get("n_detected") or 0),
        n_indexed=int(state.get("n_indexed") or 0),
        llm_calls=int(state.get("llm_calls") or 0),
        enrich_rounds=int(state.get("enrich_round") or 0),
        deleted_ids=list(state.get("deleted_ids") or []),
        run_id=run_id,
        elapsed_s=elapsed_s,
    )


def run_ingestion(
    file_name: str,
    pdf_bytes: bytes,
    deps: IngestionDeps | None = None,
    *,
    on_update: UpdateCallback | None = None,
) -> IngestionResult:
    """Ingiere un PDF: corre el grafo (con trazas si ``LANGSMITH_TRACING``) y devuelve el
    resultado. ``on_update(nodo, actualización)`` recibe cada evento de ``stream`` en vivo.

    Errores de servicios no recuperables (proxy caído, autenticación) se propagan; los de
    Pinecone/embeddings en ``index`` quedan en ``status="index_error"`` (FR-029).
    """
    if deps is None:
        from services.llm import enable_cache_if_configured

        settings = load_settings()
        enable_cache_if_configured(settings)
        deps = build_default_deps(settings)
    graph = build_ingestion_graph(deps)
    trace = make_trace_setup(
        deps.settings,
        run_name=f"{TRACE_PREFIX}{file_name}",
        metadata={"doc_hash": sha256_hex(pdf_bytes), "file_name": file_name},
        tags=["ingestion"],
        root_metadata=trace_metadata,
    )
    config: dict[str, Any] = {**trace.config, "recursion_limit": 10 + 2 * deps.max_attempts}
    final: Mapping[str, Any] = {}
    started = time.perf_counter()
    with trace.activate():
        try:
            for mode, chunk in graph.stream(
                initial_state(file_name, pdf_bytes), config, stream_mode=["updates", "values"]
            ):
                if mode == "values":
                    final = chunk
                elif on_update is not None:
                    for node, update in chunk.items():
                        on_update(node, update or {})
        finally:
            trace.flush()
    run_id = str(trace.run_id) if trace.enabled else None
    return result_from_state(final, run_id=run_id, elapsed_s=time.perf_counter() - started)


def ingest_pdf(file_name: str, pdf_bytes: bytes, deps: IngestionDeps | None = None) -> IngestionResult:
    """Interfaz de spec §7.7: ingesta completa de un PDF."""
    return run_ingestion(file_name, pdf_bytes, deps)
