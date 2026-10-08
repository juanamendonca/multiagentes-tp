"""Grafo Analista con fakes (sin red): VC-041, VC-042, VC-044, VC-045, VC-047, VC-048 (en el
grafo), VC-049, VC-052, VC-056, más normalización del plan y de la selección, extracción del
código, presupuesto de llamadas (NFR-006) y traza ``analyst:<pregunta>`` (mecanismo de FR-061).

El sandbox es un fake (``FakeSandbox``: salidas guionadas validadas con ``check_code`` y
``parse_result_line``) salvo en VC-045 y VC-047, que lanzan el sandbox real (3 subprocesos)."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
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
    COVERED_WITHOUT_TABLE_REASON,
    NO_CANDIDATES_REASON,
    NOT_EVALUATED_REASON,
    AnalystDeps,
    decide_status_node,
    extract_code,
    merge_matches,
    normalize_selection,
)
from agents.analyst.schemas import (
    AnalystResult,
    Candidate,
    PartCoverage,
    QueryPart,
    QueryPlan,
    TableSelection,
)
from agents.ingestion.schemas import TableRecord
from services.sandbox import (
    ExecutionResult,
    OutputContractError,
    ParsedOutput,
    ResultItem,
    check_code,
    parse_result_line,
    run_in_sandbox,
    summarize_stderr,
)
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


SandboxOutput = tuple[int, str, str] | str


class FakeSandbox:
    """``run_in_sandbox`` fake: cada llamada toma la siguiente salida guionada (``str`` = stdout
    con returncode 0, o ``(returncode, stdout, stderr)``) y arma el ``ExecutionResult`` con las
    mismas reglas que el real: ``check_code`` (``rejected``), returncode ≠ 0 (``error`` con la
    última línea de stderr) y ``parse_result_line`` (``invalid_output``). Registra las llamadas."""

    def __init__(self, outputs: Sequence[SandboxOutput] = ()) -> None:
        self.outputs: list[SandboxOutput] = list(outputs)
        self.calls: list[dict[str, Any]] = []
        self.executions = 0  # llamadas que pasaron el chequeo AST (las que consumen una salida)

    def __call__(
        self,
        code: str,
        tables: Any,
        timeout_s: int,
        static_check: bool = True,
        *,
        covered_part_ids: Sequence[str] | None = None,
    ) -> ExecutionResult:
        self.calls.append(
            {"code": code, "tables": dict(tables), "timeout_s": timeout_s, "covered": covered_part_ids}
        )
        reasons = check_code(code) if static_check else []
        if reasons:
            return ExecutionResult(
                status="rejected",
                error="código rechazado por el chequeo de seguridad: " + "; ".join(reasons),
                rejected_reasons=reasons,
            )
        index = self.executions
        self.executions += 1
        if index >= len(self.outputs):
            raise AssertionError(f"FakeSandbox: ejecución #{index + 1} sin salida guionada")
        output = self.outputs[index]
        returncode, stdout, stderr = (0, output, "") if isinstance(output, str) else output
        base = {"stdout": stdout, "stderr": stderr, "returncode": returncode, "duration_s": 0.01}
        if returncode != 0:
            return ExecutionResult(
                status="error", error=f"returncode {returncode}: {summarize_stderr(stderr)}", **base
            )
        if covered_part_ids is None:
            return ExecutionResult(status="ok", **base)
        try:
            parsed = parse_result_line(stdout, covered_part_ids)
        except OutputContractError as exc:
            return ExecutionResult(status="invalid_output", error=f"salida inválida: {exc}", **base)
        return ExecutionResult(status="ok", parsed=parsed, **base)


def ok_stdout(
    *results: tuple[str, float], missing: Sequence[tuple[str, str]] = (), conversions: Sequence[str] = ()
) -> str:
    """Stdout válido de §7.6 con los resultados ``(part_id, valor)`` y faltantes dados."""
    import json

    payload = {
        "results": [{"part_id": pid, "label": f"valor {pid}", "value": v, "unit": "%"} for pid, v in results],
        "missing": [{"part_id": pid, "reason": reason} for pid, reason in missing],
        "conversions": list(conversions),
    }
    return "calculando…\n" + json.dumps(payload, ensure_ascii=False)


def code_reply(code: str, prose: str = "Acá está el programa:") -> str:
    """Respuesta del LLM de codegen: texto + bloque ```python```."""
    return f"{prose}\n```python\n{code}\n```\n"


GOOD_CODE = 'import json\nprint(json.dumps({"results": [], "missing": [], "conversions": []}))'
BROKEN_CODE = 'x = tables["t1"]["no existe"]'
KEY_ERROR_STDERR = "Traceback (most recent call last):\n  File \"main.py\", line 30\nKeyError: 'no existe'\n"


class Harness:
    """Deps de test (LLM guionado, embeddings fake, Pinecone fake, TableStore temporal y sandbox
    fake; ``sandbox=run_in_sandbox`` usa el real)."""

    def __init__(
        self,
        tmp_path: Path,
        responses: list[Any],
        store: FakeVectorStore | None = None,
        *,
        no_index: bool = False,
        sandbox: Any = None,
        **settings: Any,
    ) -> None:
        self.settings = fake_settings(tmp_path / "tables", **settings)
        self.chat = ScriptedChatModel(responses=responses)
        self.embeddings = FakeEmbeddings(dimension=self.settings.embedding_dim)
        self.store = store if store is not None else FakeVectorStore()
        self.table_store = TableStore(self.settings.tables_dir)
        self.factory_calls = 0
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.sandbox = sandbox if sandbox is not None else FakeSandbox()

        def factory() -> FakeVectorStore | None:
            self.factory_calls += 1
            return None if no_index else self.store

        self.deps = AnalystDeps(
            settings=self.settings,
            chat_model=self.chat,
            embeddings=self.embeddings,
            vector_store_factory=factory,
            table_store=self.table_store,
            sandbox=self.sandbox,
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
    de retrieve, select_tables y execute (reintento, FR-037)."""
    mermaid = build_analyst_graph(Harness(tmp_path, []).deps).get_graph().draw_mermaid()

    for name in NODE_NAMES:
        assert name in mermaid
    for edge in (
        "retrieve -.-> decide_status",
        "retrieve -.-> select_tables",
        "select_tables -.-> decide_status",
        "select_tables -.-> generate_code",
        "generate_code --> execute",
        "execute -.-> generate_code",
        "execute -.-> decide_status",
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
        code_reply(BROKEN_CODE),
        code_reply(GOOD_CODE),
        "respuesta",
    ]
    h.sandbox.outputs = [(1, "", KEY_ERROR_STDERR), ok_stdout(("p1", 8.1))]
    long_question = "¿" + "x" * 100 + "?"

    result = h.run(long_question)

    name = trace_run_name(long_question)
    assert name == "analyst:" + long_question[:60]
    roots = [c for c in client.created if c.get("name") == name]
    assert len(roots) == 1 and str(roots[0]["id"]) == result.run_id
    created = {c.get("name") for c in client.created}
    assert set(NODE_NAMES) <= created
    node_runs = [c.get("name") for c in client.created if c.get("parent_run_id") == roots[0]["id"]]
    assert node_runs.count("generate_code") == node_runs.count("execute") == 2  # repeticiones (VC-081)
    metadata = metadata_of(client.updated, name)
    assert metadata["status"] == "answered"
    assert metadata["selected_table_ids"] == [a.table_id]
    assert metadata["code_attempts"] == 2


def test_trace_metadata_without_selection() -> None:
    assert trace_metadata({"status": "cannot_answer"}) == {
        "status": "cannot_answer",
        "selected_table_ids": [],
        "code_attempts": 0,
        "n_candidates": 0,
        "llm_calls": 0,
        "embedding_calls": 0,
    }


# --- generate_code / execute: VC-045, VC-047, VC-048, VC-049, NFR-006 ----------------------------

RATE = "Tasa de desocupación - 2T2026"


def gba_code(*aliases_by_part: tuple[str, str]) -> str:
    """Programa que lee el valor de GBA de cada alias y lo reporta para cada parte."""
    lines = ["import json", "results = []"]
    for part_id, alias in aliases_by_part:
        lines += [
            f'df = tables["{alias}"]',
            f'v = float(df.loc[df["Región"].str.strip() == "GBA", "{RATE}"].iloc[0])',
            f'results.append({{"part_id": "{part_id}", "label": "GBA {alias}", "value": v, "unit": "%"}})',
        ]
    lines.append(
        'print(json.dumps({"results": results, "missing": [], "conversions": []}, ensure_ascii=False))'
    )
    return "\n".join(lines)


def select_covered(
    *parts: tuple[str, Sequence[str]], selected: Sequence[str] | None = None
) -> TableSelection:
    ids = list(selected) if selected is not None else [t for _, tables in parts for t in tables]
    return TableSelection(
        selected_table_ids=ids, coverage=[coverage(pid, "covered", tables) for pid, tables in parts]
    )


def test_extract_code_takes_the_python_block() -> None:
    """Given respuestas del LLM con y sin bloques de código, then se extrae el programa."""
    assert extract_code("Texto\n```python\nx = 1\n```\nmás texto") == "x = 1"
    assert extract_code("```\ny = 2\n```") == "y = 2"
    assert extract_code("```sql\nselect 1\n```\n```py\nz = 3\n```") == "z = 3"
    assert extract_code("w = 4\n") == "w = 4"
    assert extract_code("```python\nsin_cerrar = 5\n") == "sin_cerrar = 5"
    assert extract_code("```python\n```") == ""


def test_vc045_codegen_prompt_has_exact_columns_and_program_reads_tables_by_alias(tmp_path: Path) -> None:
    """Given 2 tablas elegidas (orden b, a), when se genera y ejecuta el código en el sandbox
    REAL, then el prompt trae, por alias t1/t2 y en ese orden, los nombres exactos de columnas con
    su dtype, la primera columna fila por fila y df.head(5); el programa lee tables["t1"] y
    tables["t2"] (alias = orden de selected_table_ids) sin abrir archivos y la pregunta queda
    answered con los valores de cada tabla."""
    h = Harness(tmp_path, [], sandbox=run_in_sandbox)
    df_a = sample_df()
    df_b = sample_df()
    df_b[RATE] = pd.Series([9.9, 1.0, np.nan, 2.0], dtype="float64")
    a = h.index(sample_record(DOC_HASH, 3, 0, df_a, name="Desocupación por región 2026"), df_a)
    b = h.index(sample_record(OTHER_DOC_HASH, 5, 0, df_b, name="Desocupación por región 2025"), df_b)
    code = gba_code(("p1", "t1"), ("p2", "t2"))
    h.chat.responses = [
        plan(2),
        select_covered(("p1", [b.table_id]), ("p2", [a.table_id]), selected=[b.table_id, a.table_id]),
        code_reply(code),
        "respuesta",
    ]

    result = h.run()

    prompt = h.chat.calls[2]
    t1 = prompt.index(f'### t1 = tables["t1"]: {b.name}')
    t2 = prompt.index(f'### t2 = tables["t2"]: {a.name}')
    assert t1 < t2
    for block in (prompt[t1:t2], prompt[t2:]):
        assert '"Región" (dtype str; tipo text)' in block
        assert f'"{RATE}" (dtype float64; tipo text)' in block
        assert '"Año" (dtype Int64; tipo text)' in block
        assert "  2: <NaN>" in block and "  3: Patagonia" in block
        assert f"Región,{RATE},Año" in block  # df.head(5)
    assert "- p1: parte 1" in prompt and "- p2: parte 2" in prompt
    assert 'tables["t1"]' in prompt and "p1, p2" in prompt  # reglas y partes a calcular
    assert result.code == code and "open(" not in code
    assert result.execution is not None and result.execution.ok and result.execution.returncode == 0
    assert {r.part_id: r.value for r in result.results} == {"p1": 9.9, "p2": 8.1}
    assert result.status == "answered" and result.code_attempts == 1
    assert [t.table_id for t in result.selected_tables] == [b.table_id, a.table_id]


def test_vc047_key_error_then_fixed_code_retries_with_error_feedback(tmp_path: Path) -> None:
    """Given un LLM que devuelve código con KeyError y después código correcto (sandbox REAL),
    then 2 pasadas por generate_code, el segundo prompt contiene el código anterior y KeyError,
    code_attempts=2 y status=answered."""
    h = Harness(tmp_path, [], sandbox=run_in_sandbox)
    a, _ = two_tables(h)
    h.chat.responses = [
        plan(1),
        select_covered(("p1", [a.table_id])),
        code_reply(BROKEN_CODE),
        code_reply(gba_code(("p1", "t1"))),
        "respuesta",
    ]

    result = h.run()

    assert h.nodes.count("generate_code") == 2 and h.nodes.count("execute") == 2
    retry_prompt = h.chat.calls[3]
    assert "KeyError" in retry_prompt and BROKEN_CODE in retry_prompt
    assert "El intento 1 de 4 falló" in retry_prompt
    assert result.code_attempts == 2
    assert result.status == "answered"
    assert [(r.part_id, r.value) for r in result.results] == [("p1", 8.1)]
    assert result.missing == []


@pytest.mark.parametrize(
    "bad_stdout",
    [
        "hola, no hay JSON\n",
        '{"results": [{"part_id": "p1", "value": 1.0}], "missing": [], "conversions": []}',
        '{"results": [{"part_id": "p1", "value": NaN}, {"part_id": "p2", "value": 1.0}], "missing": []}',
    ],
    ids=["sin-json-final", "falta-parte-covered", "value-nan"],
)
def test_vc048_invalid_output_in_the_graph_triggers_a_retry(tmp_path: Path, bad_stdout: str) -> None:
    """Given una salida sin JSON final, sin una parte covered o con value=NaN, then cuenta como
    inválida (§7.6), se reintenta con el error como feedback y el segundo intento responde."""
    h = Harness(tmp_path, [])
    a, _ = two_tables(h)
    h.chat.responses = [
        plan(2),
        select_covered(("p1", [a.table_id]), ("p2", [a.table_id])),
        code_reply(GOOD_CODE),
        code_reply(GOOD_CODE + "\n# corregido"),
        "respuesta",
    ]
    h.sandbox.outputs = [bad_stdout, ok_stdout(("p1", 1.0), ("p2", 2.0))]

    result = h.run()

    assert h.nodes.count("generate_code") == 2
    assert "salida inválida" in h.chat.calls[3]
    assert "Final de stdout" in h.chat.calls[3]
    assert result.code_attempts == 2 and result.status == "answered"
    assert h.sandbox.calls[0]["covered"] == ["p1", "p2"]


@pytest.mark.parametrize(
    ("first_reply", "expected"),
    [
        (code_reply("import os\nprint(os.getcwd())"), "rechazado por el chequeo de seguridad"),
        (code_reply(""), "no contenía código"),
        ("Lo siento, no puedo escribir ese programa.", "error de sintaxis"),
    ],
    ids=["ast", "sin-codigo", "texto"],
)
def test_rejected_or_missing_code_is_retried_without_running(
    tmp_path: Path, first_reply: str, expected: str
) -> None:
    """Given un primer programa rechazado por el AST, vacío o que no es código, then no se
    ejecuta, el reintento recibe el motivo y el segundo programa responde."""
    h = Harness(tmp_path, [])
    a, _ = two_tables(h)
    h.chat.responses = [
        plan(1),
        select_covered(("p1", [a.table_id])),
        first_reply,
        code_reply(GOOD_CODE),
        "r",
    ]
    h.sandbox.outputs = [ok_stdout(("p1", 3.0))]

    result = h.run()

    assert expected in h.chat.calls[3]
    assert h.sandbox.executions == 1  # solo se ejecutó el segundo programa
    assert result.status == "answered" and result.code_attempts == 2


@pytest.mark.parametrize("max_code_retries", [0, 1, 3])
def test_vc049_nfr006_always_broken_code_exhausts_retries(tmp_path: Path, max_code_retries: int) -> None:
    """Given un LLM que siempre devuelve código roto y MAX_CODE_RETRIES=R, then exactamente 1 + R
    pasadas por generate_code/execute, la parte covered pasa a missing con "falló la ejecución
    tras 1 + R intentos: …", status según BR-015 (cannot_answer: ninguna parte respondida) y el
    presupuesto de NFR-006: 4 + R llamadas de chat y 1 de embeddings."""
    attempts = 1 + max_code_retries
    h = Harness(tmp_path, [], max_code_retries=max_code_retries)
    a, _ = two_tables(h)
    h.chat.responses = [
        plan(2),
        TableSelection(
            selected_table_ids=[a.table_id],
            coverage=[
                coverage("p1", "covered", [a.table_id]),
                coverage("p2", "missing", missing_reason="no"),
            ],
        ),
        *[code_reply(BROKEN_CODE)] * attempts,
        "No se pudo calcular.",
    ]
    h.sandbox.outputs = [(1, "", KEY_ERROR_STDERR)] * attempts

    result = h.run()

    assert h.nodes.count("generate_code") == attempts == h.nodes.count("execute")
    assert result.code_attempts == attempts
    assert result.status == "cannot_answer" and result.results == []
    p1 = result.missing[0]
    assert p1.part_id == "p1" and p1.table_ids == [a.table_id]
    assert p1.reason.startswith(f"falló la ejecución tras {attempts} intentos: returncode 1: KeyError")
    assert [m.part_id for m in result.missing] == ["p1", "p2"]
    assert len(h.chat.calls) == result.llm_calls == 4 + max_code_retries
    assert result.embedding_calls == 1 == len(h.embeddings.calls)
    assert "Código que calculó" not in h.chat.calls[-1]


def test_code_reported_missing_makes_partial_and_respond_gets_tables_conversions_and_code(
    tmp_path: Path,
) -> None:
    """Given 2 partes covered y un programa que calcula p1 (con conversión) y reporta p2 en
    missing, then partial, results solo con p1, faltante de p2 con el motivo del código, y el
    prompt de respond trae la conversión, la tabla con archivo y páginas y el código (FR-040)."""
    h = Harness(tmp_path, [])
    a, _ = two_tables(h)
    h.chat.responses = [
        plan(2),
        select_covered(("p1", [a.table_id]), ("p2", [a.table_id])),
        code_reply(GOOD_CODE),
        "respuesta parcial",
    ]
    h.sandbox.outputs = [
        ok_stdout(("p1", 1.164), missing=[("p2", "falta 2027")], conversions=["miles → millones"])
    ]

    result = h.run()

    assert result.status == "partial"
    assert [r.part_id for r in result.results] == ["p1"]
    assert [(m.part_id, m.reason) for m in result.missing] == [("p2", "falta 2027")]
    assert result.conversions == ["miles → millones"]
    prompt = h.chat.calls[-1]
    assert "miles → millones" in prompt
    assert f"{a.name} (informe.pdf, págs. 3, 4; id {a.table_id})" in prompt
    assert "Código que calculó los resultados" in prompt and GOOD_CODE in prompt
    assert h.sandbox.calls[0]["tables"] == {"t1": h.table_store.parquet_path(a.table_id)}
    assert h.sandbox.calls[0]["timeout_s"] == h.settings.sandbox_timeout_s
