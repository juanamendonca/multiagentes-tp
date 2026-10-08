"""Grafo Analista (``StateGraph`` de LangGraph) y punto de entrada ``ask``.

```
START → understand_query → retrieve ─(sin candidatas)→ decide_status
                              └→ select_tables ─(ninguna parte covered)→ decide_status
                                    └→ generate_code → execute ─(ok o sin intentos)→ decide_status
                                            ↑______________│ (falló y quedan intentos, FR-037)
decide_status → respond → END
```

``execute → generate_code`` se repite hasta ``1 + MAX_CODE_RETRIES`` intentos (BR-011).

``run_analyst`` ejecuta el grafo con ``stream(stream_mode=["updates", "values"])``: cada
actualización ``{nodo: claves}`` va a ``on_update`` (UI) y el último estado completo se convierte
en ``AnalystResult``. La traza raíz de LangSmith se llama ``analyst:<primeros 60 caracteres>`` y
lleva ``status``, ``selected_table_ids`` y ``code_attempts`` (FR-061, D-022).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from functools import partial
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from agents.analyst.nodes import (
    AnalystDeps,
    build_default_deps,
    decide_status_node,
    execute_node,
    generate_code_node,
    respond_node,
    retrieve_node,
    route_after_execute,
    route_after_retrieve,
    route_after_select,
    select_tables_node,
    understand_query_node,
)
from agents.analyst.schemas import AnalystResult
from agents.analyst.state import AnalystState
from services.config import load_settings
from services.tracing import make_trace_setup

NODE_NAMES = (
    "understand_query",
    "retrieve",
    "select_tables",
    "generate_code",
    "execute",
    "decide_status",
    "respond",
)
TRACE_PREFIX = "analyst:"
TRACE_QUESTION_CHARS = 60

UpdateCallback = Callable[[str, Mapping[str, Any]], None]


def build_analyst_graph(deps: AnalystDeps) -> CompiledStateGraph:
    """Compila el grafo Analista con las dependencias dadas (fakes en tests)."""
    graph = StateGraph(AnalystState)
    graph.add_node("understand_query", partial(understand_query_node, deps=deps))
    graph.add_node("retrieve", partial(retrieve_node, deps=deps))
    graph.add_node("select_tables", partial(select_tables_node, deps=deps))
    graph.add_node("generate_code", partial(generate_code_node, deps=deps))
    graph.add_node("execute", partial(execute_node, deps=deps))
    graph.add_node("decide_status", decide_status_node)
    graph.add_node("respond", partial(respond_node, deps=deps))

    graph.add_edge(START, "understand_query")
    graph.add_edge("understand_query", "retrieve")
    graph.add_conditional_edges(
        "retrieve", route_after_retrieve, {"select_tables": "select_tables", "decide_status": "decide_status"}
    )
    graph.add_conditional_edges(
        "select_tables",
        route_after_select,
        {"generate_code": "generate_code", "decide_status": "decide_status"},
    )
    graph.add_edge("generate_code", "execute")
    graph.add_conditional_edges(
        "execute",
        partial(route_after_execute, deps=deps),
        {"generate_code": "generate_code", "decide_status": "decide_status"},
    )
    graph.add_edge("decide_status", "respond")
    graph.add_edge("respond", END)
    return graph.compile(name="analyst")


def initial_state(question: str) -> AnalystState:
    """Estado de entrada del grafo."""
    return {
        "question": question,
        "plan": None,
        "candidates": [],
        "selection": None,
        "code": None,
        "code_attempts": 0,
        "execution": None,
        "results": [],
        "missing": [],
        "conversions": [],
        "status": None,
        "answer": "",
        "run_url": None,
        "warnings": [],
        "llm_calls": 0,
        "embedding_calls": 0,
    }


def trace_run_name(question: str) -> str:
    """``analyst:<primeros 60 caracteres de la pregunta>`` (FR-061)."""
    return f"{TRACE_PREFIX}{question.strip()[:TRACE_QUESTION_CHARS]}"


def trace_metadata(state: Mapping[str, Any]) -> dict[str, Any]:
    """Metadata del run raíz al terminar (FR-061) más contadores."""
    selection = state.get("selection")
    return {
        "status": state.get("status") or "",
        "selected_table_ids": list(selection.selected_table_ids) if selection is not None else [],
        "code_attempts": int(state.get("code_attempts") or 0),
        "n_candidates": len(state.get("candidates") or []),
        "llm_calls": int(state.get("llm_calls") or 0),
        "embedding_calls": int(state.get("embedding_calls") or 0),
    }


def result_from_state(
    state: Mapping[str, Any], *, run_id: str | None = None, elapsed_s: float = 0.0
) -> AnalystResult:
    return AnalystResult(
        question=state["question"],
        status=state.get("status") or "cannot_answer",
        answer=state.get("answer") or "",
        plan=state.get("plan"),
        candidates=list(state.get("candidates") or []),
        selection=state.get("selection"),
        results=list(state.get("results") or []),
        missing=list(state.get("missing") or []),
        conversions=list(state.get("conversions") or []),
        code=state.get("code"),
        code_attempts=int(state.get("code_attempts") or 0),
        execution=state.get("execution"),
        warnings=list(state.get("warnings") or []),
        llm_calls=int(state.get("llm_calls") or 0),
        embedding_calls=int(state.get("embedding_calls") or 0),
        run_id=run_id,
        run_url=state.get("run_url"),
        elapsed_s=elapsed_s,
    )


def run_analyst(
    question: str,
    deps: AnalystDeps | None = None,
    *,
    on_update: UpdateCallback | None = None,
) -> AnalystResult:
    """Responde una pregunta: corre el grafo (con trazas si ``LANGSMITH_TRACING``) y devuelve el
    resultado. ``on_update(nodo, actualización)`` recibe cada evento de ``stream`` en vivo.

    Errores de servicios no recuperables (proxy, Pinecone, dimensión del índice) se propagan.
    """
    if deps is None:
        from services.llm import enable_cache_if_configured

        settings = load_settings()
        enable_cache_if_configured(settings)
        deps = build_default_deps(settings)
    graph = build_analyst_graph(deps)
    trace = make_trace_setup(
        deps.settings,
        run_name=trace_run_name(question),
        metadata={"question": question},
        tags=["analyst"],
        root_metadata=trace_metadata,
    )
    # cada intento de código pasa por generate_code y execute (2 pasos), más 5 nodos fijos
    config: dict[str, Any] = {**trace.config, "recursion_limit": 10 + 2 * deps.max_code_attempts}
    final: Mapping[str, Any] = {}
    started = time.perf_counter()
    with trace.activate():
        try:
            for mode, chunk in graph.stream(
                initial_state(question), config, stream_mode=["updates", "values"]
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


def ask(question: str, deps: AnalystDeps | None = None) -> AnalystResult:
    """Interfaz de spec §7.7: responde una pregunta con el Agente Analista."""
    return run_analyst(question, deps)
