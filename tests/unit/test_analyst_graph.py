"""Grafo Analista con fakes (sin red): VC-041, VC-042, VC-044, VC-052, VC-056, más
normalización del plan y de la selección, stubs de código, presupuesto de llamadas (NFR-006)
y traza ``analyst:<pregunta>`` (mecanismo de FR-061)."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pandas as pd
import pydantic
import pytest

from agents.analyst.graph import (
    NODE_NAMES,
    build_analyst_graph,
    run_analyst,
    trace_metadata,
    trace_run_name,
)
from agents.analyst.nodes import (
    CODEGEN_STUB_REASON,
    COVERED_WITHOUT_TABLE_REASON,
    NO_CANDIDATES_REASON,
    NOT_EVALUATED_REASON,
    AnalystDeps,
    decide_status_node,
    merge_matches,
    normalize_selection,
)
from agents.analyst.schemas import (
    AnalystResult,
    Candidate,
    MissingPart,
    PartCoverage,
    QueryPart,
    QueryPlan,
    TableSelection,
)
from agents.ingestion.schemas import TableRecord
from services.sandbox import ExecutionResult, ParsedOutput, ResultItem
from services.table_store import TableStore
from services.vectorstore import Match, VectorRecord, build_embedding_text, build_metadata
from tests.fakes import FakeEmbeddings, FakeVectorStore, ScriptedChatModel
from tests.tables import DOC_HASH, OTHER_DOC_HASH, fake_settings, sample_df, sample_record
from tests.unit.test_tracing import FakeLangSmithClient, metadata_of

QUESTION = "¿Qué región tuvo la mayor desocupación en 2026 y cuánto superó al promedio nacional?"


def part(i: int, queries: Sequence[str] = ("desocupación por región",)) -> QueryPart:
    return QueryPart(
        id=f"p{i}",
        text=f"parte {i}",
        metrics=["tasa de desocupación"],
        entities=[],
        periods=["2026"],
        operation="valor",
        search_queries=list(queries),
    )


def plan(n: int = 1, queries: Sequence[str] = ("desocupación por región",)) -> QueryPlan:
    return QueryPlan(parts=[part(i, queries) for i in range(1, n + 1)])


def coverage(part_id: str, kind: str, table_ids: Sequence[str] = (), **kwargs: Any) -> PartCoverage:
    return PartCoverage(part_id=part_id, coverage=kind, table_ids=list(table_ids), **kwargs)


class ScriptedVectorStore(FakeVectorStore):
    """``query`` devuelve, en cada llamada, la siguiente lista de ``Match`` guionada."""

    def __init__(self, scripted: list[list[Match]]) -> None:
        super().__init__()
        self.scripted = scripted

    def query(self, vector: Sequence[float], top_k: int) -> list[Match]:
        self.query_calls.append(top_k)
        return self.scripted[len(self.query_calls) - 1]


class Harness:
    """Deps de test (LLM guionado, embeddings fake, Pinecone fake, TableStore temporal)."""

    def __init__(
        self,
        tmp_path: Path,
        responses: list[Any],
        store: FakeVectorStore | None = None,
        *,
        no_index: bool = False,
        **settings: Any,
    ) -> None:
        self.settings = fake_settings(tmp_path / "tables", **settings)
        self.chat = ScriptedChatModel(responses=responses)
        self.embeddings = FakeEmbeddings(dimension=self.settings.embedding_dim)
        self.store = store if store is not None else FakeVectorStore()
        self.table_store = TableStore(self.settings.tables_dir)
        self.factory_calls = 0
        self.events: list[tuple[str, dict[str, Any]]] = []

        def factory() -> FakeVectorStore | None:
            self.factory_calls += 1
            return None if no_index else self.store

        self.deps = AnalystDeps(
            settings=self.settings,
            chat_model=self.chat,
            embeddings=self.embeddings,
            vector_store_factory=factory,
            table_store=self.table_store,
        )

    def index(self, record: TableRecord, df: pd.DataFrame, *, with_parquet: bool = True) -> TableRecord:
        """Indexa una tabla en el Pinecone fake y, si ``with_parquet``, guarda sus archivos."""
        if with_parquet:
            record = self.table_store.save_table(record, df)
        text = build_embedding_text(record)
        self.store.upsert(
            [
                VectorRecord(
                    id=record.table_id,
                    values=self.embeddings.vector(text),
                    metadata=build_metadata(record, df),
                )
            ]
        )
        return record

    def run(self, question: str = QUESTION) -> AnalystResult:
        return run_analyst(question, self.deps, on_update=lambda n, u: self.events.append((n, dict(u))))

    @property
    def nodes(self) -> list[str]:
        return [name for name, _ in self.events]


def two_tables(h: Harness, *, second_with_parquet: bool = True) -> tuple[TableRecord, TableRecord]:
    df = sample_df()
    a = h.index(sample_record(DOC_HASH, 3, 0, df, name="Desocupación por región 2026"), df)
    b = h.index(
        sample_record(OTHER_DOC_HASH, 5, 0, df, name="Desocupación por región 2025"),
        df,
        with_parquet=second_with_parquet,
    )
    return a, b


# --- modelos -------------------------------------------------------------------------------------


def test_query_plan_renumbers_ids_and_cleans_search_queries() -> None:
    """Given una salida del LLM con ids raros y consultas repetidas/vacías/de más, then los ids
    quedan p1..pn y cada parte tiene 1–3 consultas distintas (vacías → el texto de la parte)."""
    raw = {
        "parts": [
            {
                "id": "p0",
                "text": "a",
                "metrics": [],
                "entities": [],
                "periods": [],
                "operation": "valor",
                "search_queries": ["x", "X ", "y", "z", "w"],
            },
            {
                "id": "p0",
                "text": "tasa b",
                "metrics": [],
                "entities": [],
                "periods": [],
                "operation": "valor",
                "search_queries": ["", "  "],
            },
        ]
    }

    result = QueryPlan.model_validate(raw)

    assert result.part_ids == ["p1", "p2"]
    assert result.parts[0].search_queries == ["x", "y", "z"]
    assert result.parts[1].search_queries == ["tasa b"]


def test_query_plan_rejects_more_than_five_parts_or_none() -> None:
    """Given 0 o 6 partes, then el plan es inválido (§7.5: 1..5)."""
    with pytest.raises(pydantic.ValidationError):
        QueryPlan(parts=[])
    with pytest.raises(pydantic.ValidationError):
        QueryPlan(parts=[part(i) for i in range(1, 7)])


# --- retrieve: VC-041, VC-042, VC-056 ------------------------------------------------------------


def test_vc041_same_table_from_two_queries_is_one_candidate_with_max_score(tmp_path: Path) -> None:
    """Given dos consultas que devuelven la misma tabla con scores 0.4 y 0.9, then queda 1
    candidata con score 0.9, ambas consultas registradas y una sola llamada de embeddings."""
    df = sample_df()
    record = sample_record(DOC_HASH, 3, 0, df)
    metadata = build_metadata(record, df)
    store = ScriptedVectorStore(
        [
            [Match(id=record.table_id, score=0.4, metadata=metadata)],
            [Match(id=record.table_id, score=0.9, metadata=metadata)],
        ]
    )
    h = Harness(
        tmp_path,
        [
            plan(1, ["desocupación por región", "tasa de desempleo regional"]),
            TableSelection(selected_table_ids=[], coverage=[coverage("p1", "missing", missing_reason="no")]),
            "respuesta",
        ],
        store,
    )
    h.table_store.save_table(record, df)

    result = h.run()

    assert [c.table_id for c in result.candidates] == [record.table_id]
    assert result.candidates[0].score == pytest.approx(0.9)
    assert result.candidates[0].matched_queries == ["desocupación por región", "tasa de desempleo regional"]
    assert result.candidates[0].pages == [3, 4]
    assert result.candidates[0].columns[0].name == "Región"
    assert len(store.query_calls) == 2 and store.query_calls[0] == h.settings.retrieval_top_k
    assert len(h.embeddings.calls) == 1 and result.embedding_calls == 1


def test_merge_matches_orders_by_score_and_skips_unreadable_metadata() -> None:
    """Given matches de varias consultas y uno con metadata corrupta, then orden por score
    descendente, partes que la trajeron y un aviso por la tabla ilegible."""
    df = sample_df()
    a = sample_record(DOC_HASH, 3, 0, df)
    b = sample_record(OTHER_DOC_HASH, 5, 0, df)
    meta_a, meta_b = build_metadata(a, df), build_metadata(b, df)

    candidates, warnings = merge_matches(
        [
            ("p1", "q1", [Match(a.table_id, 0.5, meta_a), Match(b.table_id, 0.7, meta_b)]),
            (
                "p2",
                "q2",
                [Match(a.table_id, 0.8, meta_a), Match("roto", 0.99, {"columns_json": "{no es json"})],
            ),
        ]
    )

    assert [c.table_id for c in candidates] == [a.table_id, b.table_id]
    assert [c.score for c in candidates] == [pytest.approx(0.8), pytest.approx(0.7)]
    assert candidates[0].matched_parts == ["p1", "p2"]
    assert len(warnings) == 1 and "roto" in warnings[0]


def test_vc042_no_candidates_skips_selection_and_code(tmp_path: Path) -> None:
    """Given un Pinecone vacío, then el grafo va de retrieve a decide_status (sin select_tables
    ni generate_code), status=cannot_answer con motivo "no hay tablas indexadas relacionadas"."""
    h = Harness(tmp_path, [plan(2), "No hay tablas relacionadas."])

    result = h.run()

    assert h.nodes == ["understand_query", "retrieve", "decide_status", "respond"]
    assert result.status == "cannot_answer"
    assert result.results == [] and result.selection is None
    assert [m.part_id for m in result.missing] == ["p1", "p2"]
    assert all(m.reason == NO_CANDIDATES_REASON for m in result.missing)
    assert len(h.chat.calls) == 2  # understand + respond
    assert NO_CANDIDATES_REASON in h.chat.calls[-1]
    assert result.answer == "No hay tablas relacionadas."


def test_vc042_missing_index_is_treated_as_no_candidates_without_embeddings(tmp_path: Path) -> None:
    """Given que el índice no existe (factory → None), then cannot_answer, aviso y 0 embeddings."""
    h = Harness(tmp_path, [plan(1), "sin datos"], no_index=True)

    result = h.run()

    assert result.status == "cannot_answer"
    assert h.embeddings.calls == [] and result.embedding_calls == 0
    assert any("no existe" in w for w in result.warnings)
    assert h.factory_calls == 1


def test_vc056_candidate_without_local_parquet_is_dropped_with_warning(tmp_path: Path) -> None:
    """Given 2 candidatas y una sin Parquet local, then queda 1 candidata y el aviso nombra la
    tabla faltante y su archivo."""
    h = Harness(tmp_path, [])
    a, b = two_tables(h, second_with_parquet=False)
    h.chat.responses = [
        plan(1),
        TableSelection(selected_table_ids=[], coverage=[coverage("p1", "missing", missing_reason="no")]),
        "respuesta",
    ]

    result = h.run()

    assert [c.table_id for c in result.candidates] == [a.table_id]
    expected = f"tabla {b.name} indexada pero sin datos locales; reingerir {b.source.file_name}"
    assert expected in result.warnings


def test_vc056_all_candidates_without_parquet_is_cannot_answer(tmp_path: Path) -> None:
    """Given que ninguna candidata tiene Parquet, then aplica FR-032: sin selección y cannot_answer."""
    h = Harness(tmp_path, [plan(1), "respuesta"])
    df = sample_df()
    h.index(sample_record(DOC_HASH, 3, 0, df, name="Tabla A"), df, with_parquet=False)
    h.index(sample_record(OTHER_DOC_HASH, 5, 0, df, name="Tabla B"), df, with_parquet=False)

    result = h.run()

    assert result.status == "cannot_answer"
    assert "select_tables" not in h.nodes
    assert result.candidates == []
    assert sum("sin datos locales" in w for w in result.warnings) == 2
    assert result.missing[0].reason == NO_CANDIDATES_REASON


# --- select_tables: VC-044 y normalización ------------------------------------------------------


def test_select_prompt_has_full_metadata_and_preview_of_each_candidate(tmp_path: Path) -> None:
    """Given 2 candidatas, then el prompt de selección trae archivo, páginas, sección, columnas
    exactas con tipo, rótulos de fila y la vista previa CSV de cada una, y las reglas de BR-014."""
    h = Harness(tmp_path, [])
    a, b = two_tables(h)
    h.chat.responses = [
        plan(1),
        TableSelection(selected_table_ids=[], coverage=[coverage("p1", "missing", missing_reason="no")]),
        "respuesta",
    ]

    h.run()

    prompt = h.chat.calls[1]
    for record in (a, b):
        assert f"### Tabla {record.table_id}" in prompt
        assert record.name in prompt
    assert "informe.pdf (págs. 3, 4)" in prompt and "págs. 5, 6" in prompt
    assert "Sección: 1. Resultados" in prompt
    assert '"Tasa de desocupación - 2T2026"' in prompt
    assert 'Rótulos de fila: ["GBA", "Noreste", "Patagonia"]' in prompt
    assert "Región,Tasa de desocupación - 2T2026,Año" in prompt  # encabezado del preview_csv
    assert "promedio nacional" in prompt and "tipo de cambio" in prompt and "nominal o real" in prompt


def test_vc044_all_parts_missing_never_generates_code(tmp_path: Path) -> None:
    """Given una selección con todas las partes missing, then 0 pasos por generate_code,
    status=cannot_answer, results=[] y los faltantes conservan motivo y tabla relacionada."""
    h = Harness(tmp_path, [])
    a, _ = two_tables(h)
    selection = TableSelection(
        selected_table_ids=[a.table_id],
        coverage=[
            coverage(
                "p1",
                "missing",
                [a.table_id],
                missing_reason="falta 2024",
                related_table_note="cubre 2024–2026",
            ),
            coverage("p2", "missing", missing_reason="no hay promedio nacional"),
        ],
        rationale="nada alcanza",
    )
    h.chat.responses = [plan(2), selection, "No se puede responder."]

    result = h.run()

    assert "generate_code" not in h.nodes and "execute" not in h.nodes
    assert h.nodes == ["understand_query", "retrieve", "select_tables", "decide_status", "respond"]
    assert result.status == "cannot_answer"
    assert result.results == []
    assert result.selection is not None and result.selection.selected_table_ids == []
    assert [(m.part_id, m.reason, m.related_table_note) for m in result.missing] == [
        ("p1", "falta 2024", "cubre 2024–2026"),
        ("p2", "no hay promedio nacional", None),
    ]
    assert result.missing[0].table_ids == [a.table_id]
    assert result.code is None and result.code_attempts == 0
    respond_prompt = h.chat.calls[-1]
    assert "cubre 2024–2026" in respond_prompt and a.name in respond_prompt


def test_covered_parts_go_through_code_stubs_and_end_as_missing(tmp_path: Path) -> None:
    """Given una parte covered, then pasa por generate_code y execute (stubs de la Iteración 7),
    que no llaman al LLM, y la parte termina missing con "codegen no implementado"."""
    h = Harness(tmp_path, [])
    a, b = two_tables(h)
    selection = TableSelection(
        selected_table_ids=[a.table_id],
        coverage=[
            coverage("p1", "covered", [a.table_id]),
            coverage("p2", "missing", missing_reason="no hay"),
        ],
    )
    h.chat.responses = [plan(2), selection, "No se pudo calcular."]

    result = h.run()

    assert h.nodes == list(NODE_NAMES)
    assert result.status == "cannot_answer"
    assert result.results == []
    assert result.missing[0] == MissingPart(part_id="p1", reason=CODEGEN_STUB_REASON, table_ids=[a.table_id])
    assert result.execution is None and result.code is None and result.code_attempts == 0
    assert [t.table_id for t in result.selected_tables] == [a.table_id]
    assert len(h.chat.calls) == 3  # understand + select + respond (los stubs no llaman al LLM)


def test_normalize_selection_fixes_ids_coverage_and_selected_tables() -> None:
    """Given una salida del LLM con un id inventado, una parte sin evaluar, una covered sin
    tabla válida y una parte desconocida, then se corrige como describe D-030."""
    candidates = [
        Candidate(table_id="A", score=0.9, name="A"),
        Candidate(table_id="B", score=0.8, name="B"),
    ]
    raw = TableSelection(
        selected_table_ids=["B", "Z", "A"],
        coverage=[
            coverage("p1", "covered", ["A", "Z"]),
            coverage("p2", "covered", ["Z"]),
            coverage("p9", "covered", ["B"]),
            coverage("p1", "missing", missing_reason="duplicada"),
            coverage("p4", "missing", ["B"], missing_reason="  ", related_table_note=""),
        ],
        rationale="r",
    )

    selection, warnings = normalize_selection(raw, plan(4), candidates)

    assert [(c.part_id, c.coverage, c.table_ids) for c in selection.coverage] == [
        ("p1", "covered", ["A"]),
        ("p2", "missing", []),
        ("p3", "missing", []),
        ("p4", "missing", ["B"]),
    ]
    assert selection.coverage[1].missing_reason == COVERED_WITHOUT_TABLE_REASON
    assert selection.coverage[2].missing_reason == NOT_EVALUATED_REASON
    assert selection.coverage[3].missing_reason and selection.coverage[3].related_table_note is None
    assert selection.selected_table_ids == ["A"]  # solo tablas de partes covered
    assert warnings and "Z" in warnings[0]


def test_unparseable_llm_outputs_degrade_without_stopping(tmp_path: Path) -> None:
    """Given que el plan y la selección no respetan el formato (ValueError), then el plan cae a
    una parte con la pregunta entera, la selección marca todo missing y se responde igual."""
    h = Harness(tmp_path, [])
    two_tables(h)
    h.chat.responses = [ValueError("json roto"), ValueError("json roto"), "No se pudo."]

    result = h.run()

    assert result.plan is not None and len(result.plan.parts) == 1
    assert result.plan.parts[0].search_queries == [QUESTION]
    assert result.status == "cannot_answer"
    assert "generate_code" not in h.nodes
    assert len(result.warnings) == 2


def test_service_errors_propagate(tmp_path: Path) -> None:
    """Given un error del proxy que no es de formato, then se propaga (la UI lo muestra, FR-054)."""
    h = Harness(tmp_path, [RuntimeError("proxy caído")])

    with pytest.raises(RuntimeError, match="proxy caído"):
        h.run()


# --- decide_status y respond: VC-052 ------------------------------------------------------------


def test_vc052_cannot_answer_state_has_no_numeric_results() -> None:
    """Given una ejecución fallida que igual trae resultados, then decide_status da
    cannot_answer, results=[] y conversions=[], y el faltante explica el error."""
    p = plan(1)
    selection = TableSelection(selected_table_ids=["A"], coverage=[coverage("p1", "covered", ["A"])])
    execution = ExecutionResult(
        status="error",
        returncode=1,
        error="returncode 1: KeyError: 'x'",
        parsed=ParsedOutput(results=[ResultItem(part_id="p1", value=3.0)], conversions=["miles → millones"]),
    )

    update = decide_status_node({"question": "q", "plan": p, "selection": selection, "execution": execution})

    assert update["status"] == "cannot_answer"
    assert update["results"] == [] and update["conversions"] == []
    assert "KeyError" in update["missing"][0].reason and update["missing"][0].table_ids == ["A"]


def test_partial_state_keeps_only_answered_results() -> None:
    """Given 2 partes covered y resultado solo para p1 (p2 reportada missing por el código),
    then partial, results solo con p1 y el faltante de p2 con el motivo del código."""
    from services.sandbox import MissingItem

    p = plan(2)
    selection = TableSelection(
        selected_table_ids=["A"],
        coverage=[coverage("p1", "covered", ["A"]), coverage("p2", "covered", ["A"])],
    )
    execution = ExecutionResult(
        status="ok",
        returncode=0,
        parsed=ParsedOutput(
            results=[ResultItem(part_id="p1", label="x", value=1.5, unit="%")],
            missing=[MissingItem(part_id="p2", reason="falta 2024")],
            conversions=["% → fracción"],
        ),
    )

    update = decide_status_node({"question": "q", "plan": p, "selection": selection, "execution": execution})

    assert update["status"] == "partial"
    assert [r.part_id for r in update["results"]] == ["p1"]
    assert update["conversions"] == ["% → fracción"]
    assert [(m.part_id, m.reason) for m in update["missing"]] == [("p2", "falta 2024")]


def test_vc052_respond_prompt_for_cannot_answer_has_no_results_block(tmp_path: Path) -> None:
    """Given status cannot_answer, then el prompt de respond no tiene resultados ni conversiones
    (solo faltantes) y el resultado no tiene valores."""
    h = Harness(tmp_path, [plan(1), "No hay datos."])

    result = h.run()

    assert result.status == "cannot_answer" and result.results == []
    prompt = h.chat.calls[-1]
    assert "Resultados:" not in prompt and "Conversiones aplicadas:" not in prompt
    assert "Partes que faltan:" in prompt


# --- presupuesto, grafo y trazas ------------------------------------------------------------------


def test_nfr006_call_budget_with_many_parts_and_queries(tmp_path: Path) -> None:
    """Given 3 partes con 3 consultas cada una (5 distintas), then 1 llamada de embeddings con
    las 5 consultas distintas, 5 consultas a Pinecone y ≤ 3 llamadas de chat."""
    h = Harness(tmp_path, [])
    two_tables(h)
    p = QueryPlan(
        parts=[
            part(1, ["a", "b", "c"]),
            part(2, ["a", "d", "e"]),
            part(3, ["b", "c", "e"]),
        ]
    )
    h.chat.responses = [
        p,
        TableSelection(
            selected_table_ids=[],
            coverage=[coverage(f"p{i}", "missing", missing_reason="no") for i in (1, 2, 3)],
        ),
        "respuesta",
    ]

    result = h.run()

    assert h.embeddings.calls == [["a", "b", "c", "d", "e"]]
    assert len(h.store.query_calls) == 5
    assert len(h.chat.calls) == result.llm_calls == 3
    assert len(h.chat.calls) <= 4 + h.settings.max_code_retries
    assert result.embedding_calls == 1 <= 3


def test_graph_mermaid_has_all_nodes_and_conditional_edges(tmp_path: Path) -> None:
    """Given el grafo compilado, then draw_mermaid tiene los 7 nodos y las aristas condicionales
    de retrieve y select_tables."""
    mermaid = build_analyst_graph(Harness(tmp_path, []).deps).get_graph().draw_mermaid()

    for name in NODE_NAMES:
        assert name in mermaid
    for edge in (
        "retrieve -.-> decide_status",
        "retrieve -.-> select_tables",
        "select_tables -.-> decide_status",
        "select_tables -.-> generate_code",
        "generate_code --> execute",
        "execute --> decide_status",
        "decide_status --> respond",
    ):
        assert edge in mermaid


def test_analyst_trace_has_name_run_id_and_final_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Given trazas activadas (cliente fake), when se pregunta, then el run raíz se llama
    analyst:<60 caracteres>, tiene el run_id del resultado, un hijo por nodo y metadata status,
    selected_table_ids y code_attempts (FR-061)."""
    import services.tracing as tracing

    client = FakeLangSmithClient()
    monkeypatch.setattr(tracing, "make_langsmith_client", lambda settings: client)
    h = Harness(tmp_path, [], langsmith_tracing=True)
    a, _ = two_tables(h)
    h.chat.responses = [
        plan(1),
        TableSelection(selected_table_ids=[a.table_id], coverage=[coverage("p1", "covered", [a.table_id])]),
        "respuesta",
    ]
    long_question = "¿" + "x" * 100 + "?"

    result = h.run(long_question)

    name = trace_run_name(long_question)
    assert name == "analyst:" + long_question[:60]
    roots = [c for c in client.created if c.get("name") == name]
    assert len(roots) == 1 and str(roots[0]["id"]) == result.run_id
    created = {c.get("name") for c in client.created}
    assert set(NODE_NAMES) <= created
    metadata = metadata_of(client.updated, name)
    assert metadata["status"] == "cannot_answer"
    assert metadata["selected_table_ids"] == [a.table_id]
    assert metadata["code_attempts"] == 0


def test_trace_metadata_without_selection() -> None:
    assert trace_metadata({"status": "cannot_answer"}) == {
        "status": "cannot_answer",
        "selected_table_ids": [],
        "code_attempts": 0,
        "n_candidates": 0,
        "llm_calls": 0,
        "embedding_calls": 0,
    }
