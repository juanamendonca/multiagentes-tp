"""Ingesta real: VC-021 (LLM real), VC-027 (reingesta real con otro nombre) y VC-080 (traza
en LangSmith).

Para gastar poco (presupuesto del proxy) se usa un PDF real reducido: las págs. 8–9 del extracto
de CEPAL (cuadros 1.2.1 y 1.2.2, 2 tablas) → ~2 llamadas de chat por ingesta. Con
``LLM_CACHE=true`` las corridas siguientes salen de la caché.

Aislamiento (como D-019): índice de Pinecone efímero ``<PINECONE_INDEX_NAME>-itest-<6 hex>``
(lo crea el propio nodo ``index`` vía ``ensure_index``, FR-003) y ``TABLES_DIR`` temporal; el
índice se borra al final aunque algo falle. Nunca se toca el índice de producción ni
``data/tables/``. La traza sí va al proyecto real ``LANGSMITH_PROJECT`` (es lo que verifica VC-080).
"""

from __future__ import annotations

import dataclasses
import time
import uuid
import warnings
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pymupdf
import pytest

from agents.ingestion.graph import NODE_NAMES, TRACE_PREFIX, IngestionResult, run_ingestion
from agents.ingestion.nodes import IngestionDeps, build_default_deps
from agents.ingestion.prompts import build_enrich_messages
from agents.ingestion.schemas import TableEnrichment
from agents.ingestion.state import TableDraft
from agents.ingestion.validation import validate_enrichment
from services.config import ConfigError, Settings, load_settings
from services.llm import enable_cache_if_configured, get_chat_model
from services.pdf_context import collect_context
from services.pdf_extract import extract_tables, load_pdf
from services.table_store import make_table_id
from services.vectorstore import PineconeVectorStore, count_vectors, ensure_index, make_pinecone_client

pytestmark = pytest.mark.integration

SAMPLE = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "sample_pdfs"
    / "cepal_anuario_estadistico_2025_poblacion_trabajo.pdf"
)
SUBSET_PAGES = [7, 8]  # 0-based: págs. 8 y 9 del extracto (cuadros 1.2.1 y 1.2.2)
FILE_NAME = "itest_cepal_trabajo.pdf"
RENAMED = "itest_cepal_trabajo_renombrado.pdf"
TRACE_TIMEOUT_S = 120


@pytest.fixture(scope="module")
def base_settings() -> Settings:
    try:
        settings = load_settings()
    except ConfigError as exc:  # pragma: no cover - depende del .env local
        pytest.skip(f"configuración incompleta: {exc}")
    enable_cache_if_configured(settings)
    return settings


@pytest.fixture(scope="module")
def subset_pdf() -> bytes:
    doc = pymupdf.open(SAMPLE)
    try:
        doc.select(SUBSET_PAGES)
        return doc.tobytes()
    finally:
        doc.close()


@pytest.fixture(scope="module")
def isolated(base_settings: Settings, tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, Any]]:
    """Settings con índice efímero y TABLES_DIR temporal; el índice se borra al final."""
    client = make_pinecone_client(base_settings)
    name = f"{base_settings.pinecone_index_name}-itest-{uuid.uuid4().hex[:6]}"[:45]
    assert not client.has_index(name)
    settings = dataclasses.replace(
        base_settings, pinecone_index_name=name, tables_dir=tmp_path_factory.mktemp("tables")
    )
    stores: list[PineconeVectorStore] = []

    def factory() -> PineconeVectorStore:
        store = ensure_index(settings, client)
        stores.append(store)
        return store

    try:
        yield {"settings": settings, "factory": factory, "stores": stores, "client": client, "name": name}
    finally:
        if client.has_index(name):
            client.delete_index(name)


def make_deps(isolated: dict[str, Any]) -> IngestionDeps:
    deps = build_default_deps(isolated["settings"])
    deps.vector_store_factory = isolated["factory"]
    return deps


@pytest.fixture(scope="module")
def ingested(isolated: dict[str, Any], subset_pdf: bytes) -> dict[str, IngestionResult]:
    """Ingesta del PDF reducido y reingesta del mismo PDF con otro nombre (deps nuevas)."""
    first = run_ingestion(FILE_NAME, subset_pdf, make_deps(isolated))
    renamed = run_ingestion(RENAMED, subset_pdf, make_deps(isolated))
    return {"first": first, "renamed": renamed}


def test_vc021_real_table_enrichment_parses_with_one_entry_per_column(
    base_settings: Settings, subset_pdf: bytes
) -> None:
    """Given una tabla real y su contexto, when se llama al LLM real con salida estructurada,
    then devuelve un TableEnrichment sin excepción y con len(columns) == n_cols."""
    loaded = load_pdf(FILE_NAME, subset_pdf)
    tables, _ = extract_tables(loaded)
    table = tables[0]
    draft = TableDraft(
        table_id=make_table_id(loaded.doc_hash, table.pages[0], table.index_on_page),
        raw=table,
        context=collect_context(loaded, table, tables),
    )

    structured = get_chat_model(base_settings).with_structured_output(TableEnrichment)
    enrichment = structured.invoke(build_enrich_messages(draft, FILE_NAME))

    assert isinstance(enrichment, TableEnrichment)
    assert len(enrichment.columns) == len(table.df.columns)
    print("errores BR-007 del primer intento:", validate_enrichment(enrichment, table.df))


def test_vc027_reingesting_renamed_pdf_keeps_ids_and_vector_count(
    ingested: dict[str, IngestionResult], isolated: dict[str, Any]
) -> None:
    """Given un PDF real ingerido (N vectores en un índice creado por la propia ingesta), when se
    reingiere con otro nombre, then mismos table_id y el índice sigue con N vectores."""
    first, renamed = ingested["first"], ingested["renamed"]
    assert first.status == "ok", first.message
    assert renamed.status == "ok", renamed.message
    n = len(first.records)
    assert n >= 2 and first.n_indexed == n
    assert [r.table_id for r in renamed.records] == [r.table_id for r in first.records]
    assert renamed.deleted_ids == []
    store = isolated["stores"][-1]
    assert count_vectors(store, expected=n, timeout_s=30) == n
    assert store.list_ids(first.doc_hash[:16] + "-") == sorted(r.table_id for r in first.records)
    assert {r.source.file_name for r in renamed.records} == {RENAMED}
    for record in first.records:
        print(record.table_id, record.metadata_quality, record.validation_attempts, record.name)


def _runs(client: Any, **kwargs: Any) -> list[Any]:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        return list(client.list_runs(**kwargs))


def test_vc080_ingestion_trace_in_langsmith(
    ingested: dict[str, IngestionResult], base_settings: Settings
) -> None:
    """Given una ingesta con LANGSMITH_TRACING=true, then list_runs(is_root=True) devuelve el run
    ingestion:<archivo> con hijos llamados como cada nodo y metadata doc_hash, n_tables y
    n_warnings (se espera hasta que LangSmith lo muestre)."""
    if not base_settings.langsmith_tracing:
        pytest.skip("LANGSMITH_TRACING=false")
    from langsmith import Client

    result = ingested["first"]
    assert result.run_id is not None
    client = Client(api_key=base_settings.langsmith_api_key)
    project = base_settings.langsmith_project
    name = f"{TRACE_PREFIX}{FILE_NAME}"
    deadline = time.monotonic() + TRACE_TIMEOUT_S
    root = None
    children: list[Any] = []
    while time.monotonic() < deadline:
        roots = _runs(client, project_name=project, is_root=True, filter=f'eq(name, "{name}")', limit=20)
        root = next((r for r in roots if str(r.id) == result.run_id), None)
        if root is not None and root.end_time is not None and "n_tables" in (root.metadata or {}):
            children = [
                r
                for r in _runs(client, project_name=project, trace_id=result.run_id)
                if r.parent_run_id == root.id
            ]
            if {"load_pdf", "index"} <= {c.name for c in children}:
                break
        time.sleep(5)

    assert root is not None, "no apareció el run raíz en LangSmith"
    assert root.name == name
    metadata = root.metadata or {}
    assert metadata["doc_hash"] == result.doc_hash
    assert metadata["n_tables"] == len(result.records)
    assert metadata["n_warnings"] == len(result.warnings)
    names = [c.name for c in children]
    assert set(names) <= set(NODE_NAMES) | {"_route_after_load", "_route_after_extract"}
    for node in ("load_pdf", "extract_tables", "collect_context", "enrich", "validate", "index"):
        assert node in names, names
    assert names.count("enrich") == result.enrich_rounds == names.count("validate")
    root_inputs = root.inputs or {}
    assert str(root_inputs.get("pdf_bytes", "")).endswith("bytes>")  # saneado, no el PDF entero
