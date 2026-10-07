"""Grafo de Ingesta de punta a punta con fakes (sin red): VC-023, VC-024, VC-025 (nodo index),
VC-030, VC-031, VC-032, VC-033, NFR-007, FR-024/BR-010 (reingesta) y formato del stream."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import openai
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from agents.ingestion.graph import NODE_NAMES, IngestionResult, build_ingestion_graph, run_ingestion
from agents.ingestion.nodes import NO_TABLES_MESSAGE, IngestionDeps
from agents.ingestion.schemas import TableEnrichment
from agents.ingestion.validation import rule_tag
from services.pdf_extract import RawTable, extract_tables, load_pdf
from services.table_store import TableStore, make_table_id
from services.vectorstore import VectorRecord
from tests.fakes import FakeEmbeddings, FakeVectorStore, ScriptedChatModel
from tests.fixtures import make_pdfs
from tests.tables import fake_settings, sample_df, sample_record, valid_enrichment


class Harness:
    """Deps de test + registro de las llamadas al factory del vector store y de los eventos."""

    def __init__(
        self, tmp_path: Path, responses: list[Any], store: FakeVectorStore | None = None, **settings: Any
    ):
        self.settings = fake_settings(tmp_path / "tables", **settings)
        self.chat = ScriptedChatModel(responses=responses)
        self.embeddings = FakeEmbeddings(dimension=self.settings.embedding_dim)
        self.store = store if store is not None else FakeVectorStore()
        self.factory_calls = 0
        self.table_store = TableStore(self.settings.tables_dir)
        self.events: list[tuple[str, dict[str, Any]]] = []

        def factory() -> FakeVectorStore:
            self.factory_calls += 1
            return self.store

        self.deps = IngestionDeps(
            settings=self.settings,
            chat_model=self.chat,
            embeddings=self.embeddings,
            vector_store_factory=factory,
            table_store=self.table_store,
            enrich_concurrency=1,
        )

    def run(self, file_name: str, pdf: bytes) -> IngestionResult:
        return run_ingestion(
            file_name, pdf, self.deps, on_update=lambda n, u: self.events.append((n, dict(u)))
        )

    @property
    def nodes(self) -> list[str]:
        return [name for name, _ in self.events]


def tables_of(pdf: bytes, max_tables: int = 40) -> list[RawTable]:
    tables, _ = extract_tables(load_pdf("x.pdf", pdf), max_tables=max_tables)
    return tables


def valid_for(pdf: bytes, max_tables: int = 40) -> list[TableEnrichment]:
    return [valid_enrichment(t.df) for t in tables_of(pdf, max_tables)]


def wrong_columns(df: pd.DataFrame) -> TableEnrichment:
    """Metadatos con una columna de menos (viola la regla 1)."""
    enrichment = valid_enrichment(df)
    return enrichment.model_copy(update={"columns": enrichment.columns[:-1]})


# --- camino feliz, stream y VC-025 ------------------------------------------------------------------


def test_happy_path_stream_events_and_records(tmp_path: Path) -> None:
    """Given un PDF con 2 tablas y un LLM que responde bien, then el stream emite un evento por
    nodo en orden (sin repair), 2 tablas quedan indexadas con calidad ok y 1 intento cada una."""
    pdf = make_pdfs.two_tables_one_page()
    h = Harness(tmp_path, valid_for(pdf))

    result = h.run("informe.pdf", pdf)

    assert h.nodes == ["load_pdf", "extract_tables", "collect_context", "enrich", "validate", "index"]
    assert set(h.nodes) <= set(NODE_NAMES)
    assert result.status == "ok" and result.ok
    assert result.message == "2 tablas indexadas"
    assert result.n_detected == 2 and result.n_indexed == 2 and result.llm_calls == 2
    assert [(r.metadata_quality, r.validation_attempts) for r in result.records] == [("ok", 1), ("ok", 1)]
    assert result.run_id is None  # trazas desactivadas en fake_settings
    events = dict(h.events)
    assert len(events["extract_tables"]["tables"]) == 2
    assert events["enrich"]["llm_calls"] == 2
    assert events["index"]["n_indexed"] == 2


def test_vc025_index_node_writes_parquet_json_and_upserts_by_table_id(tmp_path: Path) -> None:
    """Given tablas validadas, when corre index, then por tabla existen <id>.parquet y <id>.json,
    read_parquet devuelve el DataFrame indexado (dtypes incluidos), el upsert usa el table_id y
    el embedding_text tiene name, description, cada columna y "Fuente:"."""
    pdf = make_pdfs.two_tables_one_page()
    expected = tables_of(pdf)
    h = Harness(tmp_path, valid_for(pdf))

    result = h.run("informe.pdf", pdf)

    doc_hash = load_pdf("x.pdf", pdf).doc_hash
    ids = [make_table_id(doc_hash, t.pages[0], t.index_on_page) for t in expected]
    assert [r.table_id for r in result.records] == ids
    assert sorted(h.store.vectors) == sorted(ids)
    assert h.store.upsert_calls == [[i] for i in ids]
    for table, record in zip(expected, result.records, strict=True):
        assert h.table_store.parquet_path(record.table_id).is_file()
        assert h.table_store.json_path(record.table_id).is_file()
        assert_frame_equal(pd.read_parquet(h.table_store.parquet_path(record.table_id)), table.df)
        assert h.table_store.load_record(record.table_id) == record
        text = h.store.vectors[record.table_id].metadata["embedding_text"]
        assert record.name in text and record.description in text and "Fuente: informe.pdf" in text
        for column in table.df.columns:
            assert column in text
        assert h.store.vectors[record.table_id].metadata["file_name"] == "informe.pdf"
    assert len(h.embeddings.calls) == 1  # NFR-007: un lote


def test_unit_hints_end_up_as_column_units(tmp_path: Path) -> None:
    """Las unidades removidas al normalizar (BR-003) quedan en el JSON si el LLM no puso unidad."""
    pdf = make_pdfs.two_tables_one_page()
    raw = tables_of(pdf)[0]
    h = Harness(tmp_path, valid_for(pdf))
    hinted = {name: hint for name, hint in raw.unit_hints.items() if hint}

    result = h.run("informe.pdf", pdf)

    units = {c.name: c.unit for c in result.records[0].columns}
    assert all(units[name] == hint for name, hint in hinted.items())


# --- VC-023 / VC-024 / NFR-007 ------------------------------------------------------------------------


def test_vc023_invalid_then_valid_retries_enrich_with_error_feedback(tmp_path: Path) -> None:
    """Given un LLM que devuelve columnas incorrectas en el intento 1 y correctas en el 2, then
    el grafo pasa 2 veces por enrich, el segundo prompt contiene el error, la tabla queda ok
    con validation_attempts=2."""
    pdf = make_pdfs.table_with_context()
    df = tables_of(pdf)[0].df
    h = Harness(tmp_path, [wrong_columns(df), valid_enrichment(df)])

    result = h.run("informe.pdf", pdf)

    assert h.nodes.count("enrich") == 2 and h.nodes.count("validate") == 2
    assert "repair" not in h.nodes
    last_validate = [u for n, u in h.events if n == "validate"][-1]
    assert [d.errors for d in last_validate["tables"]] == [[]]
    assert len(h.chat.calls) == 2
    assert "Tu respuesta anterior fue rechazada" not in h.chat.calls[0]
    assert (
        rule_tag(1) in h.chat.calls[1] and "columns tiene 2 entradas pero la tabla tiene 3" in h.chat.calls[1]
    )
    (record,) = result.records
    assert record.metadata_quality == "ok" and record.validation_attempts == 2


def test_enrich_prompt_has_exact_columns_context_rows_and_row_headers(tmp_path: Path) -> None:
    pdf = make_pdfs.table_with_context()
    df = tables_of(pdf)[0].df
    h = Harness(tmp_path, [valid_enrichment(df)])

    h.run("eph_2t2026.pdf", pdf)

    prompt = h.chat.calls[0]
    assert "Archivo: eph_2t2026.pdf" in prompt and "pág. 1" in prompt
    assert "Cuadro 3. Ocupación por región" in prompt and "Fuente: INDEC" in prompt
    assert "2. Mercado de trabajo regional" in prompt
    for i, column in enumerate(df.columns):
        assert f'{i}. "{column}"' in prompt
    assert '["Región 1", "Región 2", "Región 3"]' in prompt
    assert "Región 1,1001.5,1.5" in prompt  # primeras filas en CSV


def test_vc024_always_invalid_exhausts_retries_and_repairs_as_degraded(tmp_path: Path) -> None:
    """Given un LLM siempre inválido y MAX_ENRICH_RETRIES=2, then exactamente 3 llamadas a enrich,
    columnas reconstruidas idénticas a las del DataFrame, metadata_quality=degraded y aviso."""
    pdf = make_pdfs.table_with_context()
    df = tables_of(pdf)[0].df
    h = Harness(tmp_path, [wrong_columns(df)] * 3, max_enrich_retries=2)

    result = h.run("informe.pdf", pdf)

    assert len(h.chat.calls) == 3 and h.nodes.count("enrich") == 3
    assert h.nodes[-2:] == ["repair", "index"]
    (record,) = result.records
    assert [c.name for c in record.columns] == list(df.columns)
    assert [c.dtype for c in record.columns] == ["text", "number", "number"]
    assert record.metadata_quality == "degraded" and record.validation_attempts == 3
    assert record.name == "Tabla de prueba"  # BR-008 conserva el nombre del último intento
    degraded = [w for w in result.warnings if w.table_ref == record.table_id]
    assert len(degraded) == 1 and "metadata_quality=degraded" in degraded[0].reason
    assert rule_tag(1) in degraded[0].reason
    assert result.message == "1 tablas indexadas (1 con metadatos degradados)"
    assert result.n_degraded == 1
    assert h.store.vectors[record.table_id].metadata["metadata_quality"] == "degraded"


@pytest.mark.parametrize("retries", [0, 1, 3])
def test_nfr007_chat_calls_per_table_bounded_and_embeddings_batched(tmp_path: Path, retries: int) -> None:
    """Given 2 tablas siempre inválidas, then cada tabla recibe exactamente 1 + MAX_ENRICH_RETRIES
    llamadas de chat (≤ el máximo de NFR-007) y los embeddings van en una sola llamada."""
    pdf = make_pdfs.two_tables_one_page()
    first, second = tables_of(pdf)
    responses = [wrong_columns(first.df), wrong_columns(second.df)] * (1 + retries)
    h = Harness(tmp_path, responses, max_enrich_retries=retries)

    result = h.run("informe.pdf", pdf)

    per_table = [sum('"Sector"' in c for c in h.chat.calls), sum('"Población"' in c for c in h.chat.calls)]
    assert per_table == [1 + retries, 1 + retries]
    assert result.llm_calls == len(h.chat.calls) == 2 * (1 + retries)
    assert len(h.embeddings.calls) == 1
    assert all(r.metadata_quality == "degraded" for r in result.records)


def test_retries_are_per_table_only_invalid_tables_go_back_to_enrich(tmp_path: Path) -> None:
    """Given una tabla válida y otra inválida en el primer intento, then el reintento solo
    llama al LLM por la inválida (D-021)."""
    pdf = make_pdfs.two_tables_one_page()
    first, second = tables_of(pdf)
    h = Harness(tmp_path, [valid_enrichment(first.df), wrong_columns(second.df), valid_enrichment(second.df)])

    result = h.run("informe.pdf", pdf)

    assert len(h.chat.calls) == 3
    assert '"Sector"' in h.chat.calls[2] and '"Población"' not in h.chat.calls[2]
    assert [r.validation_attempts for r in result.records] == [1, 2]


def test_unparseable_llm_output_counts_as_a_failed_attempt(tmp_path: Path) -> None:
    """Given una salida que no valida contra TableEnrichment, then cuenta como intento con el
    error como feedback, y el segundo intento válido deja la tabla ok."""
    pdf = make_pdfs.table_with_context()
    df = tables_of(pdf)[0].df
    h = Harness(tmp_path, [{"name": "sin columnas"}, valid_enrichment(df)])

    result = h.run("informe.pdf", pdf)

    assert "[llm]" in h.chat.calls[1]
    assert result.records[0].validation_attempts == 2 and result.records[0].metadata_quality == "ok"


def test_unrecoverable_llm_error_stops_the_ingestion_without_writing(tmp_path: Path) -> None:
    """Given el proxy caído, then la excepción se propaga (la UI la informa, FR-054) y no se
    escribe nada en Pinecone ni en TABLES_DIR."""
    pdf = make_pdfs.table_with_context()
    error = openai.APIConnectionError(request=httpx.Request("POST", "http://litellm.invalid"))
    h = Harness(tmp_path, [error])

    with pytest.raises(openai.APIConnectionError):
        h.run("informe.pdf", pdf)

    assert h.factory_calls == 0 and h.store.upsert_calls == []
    assert h.table_store.list_table_ids() == []


# --- VC-030 / VC-031 / VC-032 -------------------------------------------------------------------------


def test_vc030_pdf_without_tables_writes_nothing(tmp_path: Path) -> None:
    """Given un PDF sin tablas, then 0 upserts, Pinecone ni se abre y el mensaje es "No se
    encontraron tablas legibles"."""
    h = Harness(tmp_path, [])

    result = h.run("sin_tablas.pdf", make_pdfs.no_tables())

    assert result.status == "no_tables" and result.message == NO_TABLES_MESSAGE
    assert h.nodes == ["load_pdf", "extract_tables"]
    assert h.factory_calls == 0 and h.store.upsert_calls == [] and h.chat.calls == []
    assert result.records == [] and h.table_store.list_table_ids() == []


@pytest.mark.parametrize(
    "data",
    [
        b"esto es un archivo de texto, no un PDF\n",
        make_pdfs.two_tables_one_page()[:300],
        make_pdfs.encrypted(),
    ],
    ids=["txt", "truncado", "encriptado"],
)
def test_vc031_invalid_pdf_stops_at_load_pdf(tmp_path: Path, data: bytes) -> None:
    """Given un .txt, un PDF truncado o uno encriptado, then el grafo termina en load_pdf con
    "No se pudo abrir el PDF: <motivo>", 0 upserts y TABLES_DIR sin cambios."""
    h = Harness(tmp_path, [])
    previous = sample_record()
    h.table_store.save_table(previous, sample_df())
    before = sorted(p.name for p in h.settings.tables_dir.iterdir())

    result = h.run("archivo.pdf", data)

    assert h.nodes == ["load_pdf"]
    assert result.status == "pdf_error" and not result.ok
    assert result.message.startswith("No se pudo abrir el PDF: ")
    assert h.factory_calls == 0 and h.store.upsert_calls == [] and h.chat.calls == []
    assert sorted(p.name for p in h.settings.tables_dir.iterdir()) == before


def test_vc032_max_tables_per_pdf_limits_enrichment(tmp_path: Path) -> None:
    """Given 5 tablas y MAX_TABLES_PER_PDF=3, then se enriquecen 3 y hay un aviso de 2 omitidas."""
    pdf = make_pdfs.five_tables()
    h = Harness(tmp_path, valid_for(pdf, max_tables=3), max_tables_per_pdf=3)

    result = h.run("cinco.pdf", pdf)

    assert len(h.chat.calls) == 3 and result.n_detected == 3 and len(result.records) == 3
    assert any("2 tablas omitidas" in w.reason for w in result.warnings)


# --- VC-033 / FR-029 y FR-024 / BR-010 -----------------------------------------------------------------


def test_vc033_partial_pinecone_failure_then_reingest_completes_without_duplicates(tmp_path: Path) -> None:
    """Given Pinecone que falla en el upsert de la 2.ª de 3 tablas, then el resultado informa
    "1 de 3 tablas indexadas"; reingerir con Pinecone sano deja 3 vectores en total."""
    pdf = make_pdfs.five_tables()
    store = FakeVectorStore(fail_on_upsert_call=2)
    h = Harness(tmp_path, valid_for(pdf, max_tables=3), store=store, max_tables_per_pdf=3)

    failed = h.run("cinco.pdf", pdf)

    assert failed.status == "index_error" and not failed.ok
    assert "1 de 3 tablas indexadas" in failed.message
    assert "Reingerir el mismo PDF" in failed.message
    assert failed.n_indexed == 1 and store.count() == 1

    store.fail_on_upsert_call = None
    retry = Harness(tmp_path, valid_for(pdf, max_tables=3), store=store, max_tables_per_pdf=3)
    healed = retry.run("cinco.pdf", pdf)

    assert healed.status == "ok" and healed.n_indexed == 3
    assert store.count() == 3
    assert sorted(store.vectors) == sorted(r.table_id for r in failed.records)


def test_embeddings_failure_is_reported_as_index_error(tmp_path: Path) -> None:
    pdf = make_pdfs.table_with_context()
    h = Harness(tmp_path, valid_for(pdf))
    h.deps.embeddings = FakeEmbeddings(dimension=5)  # dimensión distinta de EMBEDDING_DIM

    result = h.run("informe.pdf", pdf)

    assert result.status == "index_error"
    assert result.message.startswith("0 de 1 tablas indexadas; falló el cálculo de embeddings")
    assert h.store.upsert_calls == []


def test_vector_store_unavailable_writes_nothing(tmp_path: Path) -> None:
    """Given que no se puede abrir el índice (p. ej. dimensión distinta, FR-004), then no se
    escribe nada en TABLES_DIR y el resultado lo informa."""
    pdf = make_pdfs.table_with_context()
    h = Harness(tmp_path, valid_for(pdf))

    def broken() -> FakeVectorStore:
        raise RuntimeError("dimensión 3072 distinta de 8")

    h.deps.vector_store_factory = broken

    result = h.run("informe.pdf", pdf)

    assert result.status == "index_error" and "dimensión 3072" in result.message
    assert h.table_store.list_table_ids() == []


def test_fr024_reingest_renamed_pdf_keeps_ids_count_and_removes_stale(tmp_path: Path) -> None:
    """Given un PDF ingerido y restos viejos del mismo doc_hash (vector + archivos), when se
    reingiere con otro nombre, then mismos table_id, mismo total de vectores y los restos se
    borran de Pinecone y de TABLES_DIR (BR-010)."""
    pdf = make_pdfs.two_tables_one_page()
    store = FakeVectorStore()
    first = Harness(tmp_path, valid_for(pdf), store=store).run("informe.pdf", pdf)
    doc_hash = first.doc_hash
    stale = sample_record(doc_hash=doc_hash, first_page=9, index_on_page=0)
    first_store = TableStore(tmp_path / "tables")
    first_store.save_table(stale, sample_df())
    store.upsert([VectorRecord(id=stale.table_id, values=[1.0] * 8, metadata={})])
    other = sample_record(doc_hash="f" * 64)
    store.upsert([VectorRecord(id=other.table_id, values=[1.0] * 8, metadata={})])
    assert store.count() == 4

    second = Harness(tmp_path, valid_for(pdf), store=store).run("renombrado.pdf", pdf)

    assert [r.table_id for r in second.records] == [r.table_id for r in first.records]
    assert second.deleted_ids == [stale.table_id]
    assert store.count() == 3  # 2 del documento + 1 de otro documento
    assert not first_store.table_exists(stale.table_id)
    assert {r.source.file_name for r in first_store.list_records()} == {"renombrado.pdf"}


def test_graph_mermaid_has_all_nodes(tmp_path: Path) -> None:
    mermaid = build_ingestion_graph(Harness(tmp_path, []).deps).get_graph().draw_mermaid()

    for node in NODE_NAMES:
        assert f"\t{node}({node})" in mermaid
    assert "validate -.-> enrich;" in mermaid and "validate -.-> repair;" in mermaid
