"""VC-003 y VC-027 (parte vectorstore) contra Pinecone real + embeddings reales de LiteLLM,
y NFR-005 sobre las tablas reales de los PDFs de muestra.

Aislamiento (D-019): los tests escriben en un índice **efímero propio**
(``<PINECONE_INDEX_NAME>-itest-<6 hex>``), que se crea una vez por corrida y se borra al
final aunque algo falle. El índice de producción ``PINECONE_INDEX_NAME`` no se toca.
Los conteos de Pinecone serverless son eventualmente consistentes: se leen con
``count_vectors(..., expected=..., timeout_s=30)``.
"""

from __future__ import annotations

import uuid
import warnings
from collections.abc import Iterator
from functools import cache
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from agents.ingestion.schemas import ColumnMeta, TableRecord, TableSource
from services.config import ConfigError, Settings, load_settings
from services.llm import get_embeddings
from services.pdf_context import collect_context
from services.pdf_extract import RawTable, extract_tables, load_pdf
from services.table_store import make_table_id
from services.vectorstore import (
    MAX_METADATA_BYTES,
    PineconeVectorStore,
    build_embedding_text,
    build_metadata,
    count_vectors,
    delete_stale,
    embed_texts,
    ensure_index,
    make_pinecone_client,
    metadata_size,
    query,
    upsert_records,
)

pytestmark = pytest.mark.integration

SAMPLE_DIR = Path(__file__).resolve().parents[2] / "data" / "sample_pdfs"
EPH_2025 = "indec_eph_mercado_trabajo_2t2025.pdf"


@cache
def real_tables(file_name: str, as_name: str | None = None) -> list[tuple[TableRecord, pd.DataFrame]]:
    """Tablas reales del PDF como (``TableRecord`` con metadatos provisorios, DataFrame).

    Los metadatos imitan la reparación determinística (BR-008): nombre genérico, contexto
    superior como descripción y columnas desde el DataFrame. ``as_name`` simula subir el mismo
    PDF con otro nombre.
    """
    loaded = load_pdf(as_name or file_name, (SAMPLE_DIR / file_name).read_bytes())
    tables, _ = extract_tables(loaded)
    out = []
    for table in tables:
        context = collect_context(loaded, table, tables)
        out.append((record_from_raw(loaded.doc_hash, loaded.file_name, table, context), table.df))
    return out


def record_from_raw(doc_hash: str, file_name: str, table: RawTable, context: Any) -> TableRecord:
    df = table.df
    text_columns = [c for c in df.columns if str(df[c].dtype) == "str"]
    row_headers = (
        [str(v).strip() for v in df[text_columns[0]].dropna().tolist() if str(v).strip()]
        if text_columns
        else []
    )
    dtype_map = {"float64": "number", "Int64": "year", "str": "text"}
    return TableRecord(
        table_id=make_table_id(doc_hash, table.pages[0], table.index_on_page),
        doc_hash=doc_hash,
        name=f"Tabla p{table.pages[0]} #{table.index_on_page} de {file_name}",
        description=(" ".join(context.above) or "Sin descripción.")[:600],
        columns=[
            ColumnMeta(name=str(c), description="", dtype=dtype_map.get(str(df[c].dtype), "text"))
            for c in df.columns
        ],
        row_headers=row_headers,
        source=TableSource(file_name=file_name, pages=table.pages),
        section=context.section,
        n_rows=len(df),
        n_cols=len(df.columns),
        dtypes={str(c): str(df[c].dtype) for c in df.columns},
    )


@pytest.fixture(scope="module")
def settings() -> Settings:
    try:
        return load_settings()
    except ConfigError as exc:  # pragma: no cover - depende del .env local
        pytest.skip(f"configuración incompleta para Pinecone: {exc}")


@pytest.fixture(scope="module")
def test_index(settings: Settings) -> Iterator[tuple[Any, str, PineconeVectorStore]]:
    """Índice efímero propio de esta corrida; se borra al final pase lo que pase."""
    client = make_pinecone_client(settings)
    name = f"{settings.pinecone_index_name}-itest-{uuid.uuid4().hex[:6]}"[:45]
    assert not client.has_index(name)
    try:
        store = ensure_index(settings, client, index_name=name)
        yield client, name, store
    finally:
        if client.has_index(name):
            client.delete_index(name)


def test_vc003_missing_index_is_created_with_embedding_dim_and_cosine(
    settings: Settings, test_index: tuple[Any, str, PineconeVectorStore]
) -> None:
    """Given un índice inexistente, when se asegura (primera ingesta), then queda creado con
    dimensión EMBEDDING_DIM y métrica coseno; asegurarlo de nuevo no crea otro."""
    client, name, _ = test_index

    description = client.describe_index(name)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        assert description.dimension == settings.embedding_dim
        assert description.metric == "cosine"
    assert description.status.ready
    again = ensure_index(settings, client, index_name=name)
    assert again.name == name


def test_vc027_reupserting_renamed_pdf_keeps_ids_and_vector_count(
    settings: Settings, test_index: tuple[Any, str, PineconeVectorStore]
) -> None:
    """Given las tablas reales de un PDF indexadas (N vectores), when se reindexa el mismo PDF
    subido con otro nombre, then los table_id son los mismos y el índice sigue con N vectores;
    una consulta encuentra la tabla; borrar obsoletos deja N-1."""
    _, _, store = test_index
    first = real_tables(EPH_2025)
    renamed = real_tables(EPH_2025, as_name="eph_renombrado.pdf")
    n = len(first)
    assert n >= 2
    assert [r.table_id for r, _ in renamed] == [r.table_id for r, _ in first]
    embeddings = get_embeddings(settings)
    texts = [build_embedding_text(record) for record, _ in first]
    vectors = embed_texts(embeddings, texts, expected_dim=settings.embedding_dim)

    assert upsert_records(store, first, vectors, texts) == n
    assert count_vectors(store, expected=n) == n

    renamed_texts = [build_embedding_text(record) for record, _ in renamed]
    renamed_vectors = embed_texts(embeddings, renamed_texts, expected_dim=settings.embedding_dim)
    assert upsert_records(store, renamed, renamed_vectors, renamed_texts) == n
    assert count_vectors(store, expected=n) == n
    doc_hash = first[0][0].doc_hash
    assert store.list_ids(doc_hash[:16] + "-") == sorted(r.table_id for r, _ in first)

    target = renamed[1][0]
    matches = query(store, embeddings.embed_query(renamed_texts[1]), top_k=3)
    assert matches and matches[0].id == target.table_id
    assert matches[0].metadata["file_name"] == "eph_renombrado.pdf"
    assert matches[0].metadata["pages"] == [str(p) for p in target.source.pages]

    keep = [r.table_id for r, _ in first[:-1]]
    assert delete_stale(store, doc_hash, keep) == [first[-1][0].table_id]
    assert count_vectors(store, expected=n - 1) == n - 1


@pytest.mark.parametrize(
    "file_name",
    [
        "cepal_anuario_estadistico_2025_poblacion_trabajo.pdf",
        "indec_eph_mercado_trabajo_2t2026.pdf",
        EPH_2025,
    ],
)
def test_nfr005_real_tables_metadata_fits_without_truncation(file_name: str) -> None:
    """Given las tablas reales (columnas largas de CEPAL, `*` y `°` de INDEC), then la
    metadata de cada una ocupa ≤ 35 KB sin truncar la vista previa ni los rótulos de fila."""
    for record, df in real_tables(file_name):
        assert isinstance(df, pd.DataFrame)
        metadata = build_metadata(record, df)
        assert metadata_size(metadata) <= MAX_METADATA_BYTES
        assert metadata["row_headers"] == record.row_headers, record.table_id
        assert metadata["preview_csv"] == df.head(5).to_csv(index=False, lineterminator="\n"), record.table_id
