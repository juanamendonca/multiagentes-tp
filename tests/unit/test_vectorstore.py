"""VC-004, VC-025 (texto de embedding y upsert), VC-026 y VC-028 (parte Pinecone) con fakes.

También: creación del índice (FR-003, lado lógico), embeddings en lotes (NFR-007), falla
parcial del upsert (FR-029, lado vectorstore), conteo con espera y el adaptador de Pinecone
sobre un índice con la forma del SDK.
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

import numpy as np
import pandas as pd
import pytest

from agents.ingestion.schemas import ColumnMeta, TableSource
from services.config import Settings
from services.vectorstore import (
    MAX_METADATA_BYTES,
    METADATA_KEYS,
    IndexDimensionMismatch,
    IndexingError,
    PineconeVectorStore,
    VectorRecord,
    VectorStore,
    build_embedding_text,
    build_metadata,
    count_vectors,
    decode_metadata,
    delete_stale,
    embed_texts,
    ensure_index,
    metadata_size,
    query,
    upsert_records,
)
from tests.fakes import FakeEmbeddings, FakePineconeClient, FakePineconeIndex, FakeVectorStore
from tests.tables import DOC_HASH, OTHER_DOC_HASH, sample_df, sample_record

SETTINGS = Settings(
    litellm_base_url="http://proxy.invalid",
    litellm_api_key="test",
    llm_model="fake-llm",
    embedding_model="fake-embeddings",
    embedding_dim=1536,
    pinecone_api_key="test",
    langsmith_api_key="test",
    langsmith_project="test",
    pinecone_index_name="tablas-test",
    pinecone_cloud="aws",
    pinecone_region="us-east-1",
)


def items(n: int = 3, doc_hash: str = DOC_HASH) -> list[tuple[Any, pd.DataFrame]]:
    df = sample_df()
    return [(sample_record(doc_hash=doc_hash, first_page=p, df=df), df) for p in range(1, n + 1)]


# --- ensure_index (FR-003, FR-004) ---------------------------------------------------------------


def test_vc004_existing_index_with_other_dimension_raises_and_writes_nothing() -> None:
    """Given un índice existente de dimensión 1536 y EMBEDDING_DIM=3072, when se asegura el
    índice, then el error muestra ambas dimensiones y no hay ningún upsert."""
    client = FakePineconeClient({"tablas-test": {"dimension": 1536, "metric": "cosine"}})

    with pytest.raises(IndexDimensionMismatch) as error:
        ensure_index(replace(SETTINGS, embedding_dim=3072), client)

    message = str(error.value)
    assert "1536" in message and "3072" in message and "tablas-test" in message
    assert error.value.index_dim == 1536 and error.value.expected_dim == 3072
    assert client.created == []
    assert all(index.upsert_calls == 0 for index in client.index_objects.values())
    assert client.index_objects == {}


def test_missing_index_is_created_serverless_cosine_with_embedding_dim() -> None:
    """Given que el índice no existe, when se asegura, then se crea serverless (cloud/región de
    la config) con dimensión EMBEDDING_DIM y métrica coseno (FR-003)."""
    client = FakePineconeClient()

    store = ensure_index(SETTINGS, client)

    assert len(client.created) == 1
    created = client.created[0]
    assert created["name"] == "tablas-test"
    assert created["dimension"] == 1536 and created["metric"] == "cosine"
    assert created["spec"].cloud == "aws" and created["spec"].region == "us-east-1"
    assert isinstance(store, PineconeVectorStore) and isinstance(store, VectorStore)


def test_existing_index_with_same_dimension_is_reused() -> None:
    """Given un índice existente con la dimensión correcta, then no se crea otro."""
    client = FakePineconeClient({"tablas-test": {"dimension": 1536, "metric": "cosine"}})

    store = ensure_index(SETTINGS, client)

    assert client.created == []
    assert store.name == "tablas-test"


def test_ensure_index_accepts_another_index_name() -> None:
    """Given index_name explícito (tests de integración), then se usa en lugar del de la config."""
    client = FakePineconeClient()

    ensure_index(SETTINGS, client, index_name="otro")

    assert client.created[0]["name"] == "otro"


# --- texto de embedding (BR-009, VC-025) ---------------------------------------------------------


def test_vc025_embedding_text_has_name_description_columns_rows_source_and_section() -> None:
    """Given un registro, then el texto de embedding tiene name, description, cada columna con
    unidad y descripción, los rótulos de fila, "Fuente:" con archivo y páginas, y la sección."""
    record = sample_record(
        columns=[
            ColumnMeta(name="Región", description="Región estadística", dtype="text"),
            ColumnMeta(
                name="Tasa de desocupación - 2T2026", description="Desocupados/PEA", dtype="percent", unit="%"
            ),
            ColumnMeta(name="Año", description="", dtype="year"),
        ]
    )

    text = build_embedding_text(record)

    lines = text.split("\n")
    assert lines[0] == record.name
    assert lines[1] == record.description
    assert lines[2] == (
        "Columnas: Región: Región estadística; Tasa de desocupación - 2T2026 (%): Desocupados/PEA; Año"
    )
    assert lines[3] == "Filas: GBA; Noreste; Patagonia"
    assert lines[4] == "Fuente: informe.pdf, págs. 3, 4"
    assert lines[5] == "Sección: 1. Resultados"
    for column in record.columns:
        assert column.name in text


def test_embedding_text_uses_first_50_row_headers_and_omits_missing_section() -> None:
    """Given 80 rótulos de fila y sin sección, then entran solo los primeros 50 y no hay línea de
    sección; una sola página se escribe "pág."."""
    record = sample_record(
        row_headers=[f"fila {i}" for i in range(80)],
        section=None,
        source=TableSource(file_name="a.pdf", pages=[7]),
    )

    text = build_embedding_text(record)

    assert "fila 49" in text and "fila 50" not in text
    assert "Sección" not in text
    assert text.endswith("Fuente: a.pdf, pág. 7")


# --- metadata (§7.4, NFR-005) --------------------------------------------------------------------


def test_metadata_has_spec_keys_and_pinecone_compatible_types() -> None:
    """Given un registro, then la metadata tiene exactamente las claves de §7.4, sin nulls, con
    listas solo de textos, y decode_metadata recupera columnas, páginas y números."""
    df = sample_df()
    record = sample_record(df=df, section=None)

    metadata = build_metadata(record, df)

    assert tuple(metadata) == METADATA_KEYS
    for key, value in metadata.items():
        assert value is not None, key
        assert isinstance(value, str | int | float | bool | list), key
        if isinstance(value, list):
            assert all(isinstance(v, str) for v in value), key
    assert metadata["preview_csv"].splitlines()[0] == "Región,Tasa de desocupación - 2T2026,Año"
    assert len(metadata["preview_csv"].splitlines()) == 1 + 4
    decoded = decode_metadata(metadata)
    assert decoded["columns"] == record.columns
    assert decoded["pages"] == [3, 4]
    assert decoded["section"] is None
    assert decoded["n_rows"] == 4 and decoded["n_cols"] == 3
    assert json.loads(metadata["columns_json"])[0]["name"] == "Región"


def test_vc026_large_table_metadata_fits_in_35kb() -> None:
    """Given una tabla de 5 000 filas y 300 rótulos de fila largos, when se arma la metadata,
    then ocupa ≤ 35 KB; se truncan preview_csv y row_headers, en ese orden."""
    n_cols = 40
    columns = [
        f"Indicador muy largo número {i} - (En miles de personas a mitad de año) - 2025"
        for i in range(n_cols)
    ]
    rng = np.random.default_rng(0)
    df = pd.DataFrame(rng.random((5000, n_cols)) * 1e6, columns=columns)
    record = sample_record(
        df=df,
        row_headers=[
            f"Rótulo de fila número {i} con texto bastante largo para ocupar espacio " * 2 for i in range(300)
        ],
    )
    full_preview = df.head(5).to_csv(index=False, lineterminator="\n")

    metadata = build_metadata(record, df)

    assert metadata_size(metadata) <= MAX_METADATA_BYTES == 35_000
    assert len(json.dumps(metadata, ensure_ascii=False).encode("utf-8")) <= 35 * 1024
    assert len(metadata["preview_csv"]) < len(full_preview)
    assert len(metadata["row_headers"]) < 300
    assert metadata["row_headers"] == record.row_headers[: len(metadata["row_headers"])]
    assert metadata["embedding_text"] == build_embedding_text(record)


def test_preview_is_truncated_before_row_headers() -> None:
    """Given metadata que se pasa del tope solo por la vista previa, then se recortan filas de
    preview_csv y row_headers queda completo."""
    df = pd.DataFrame({"Texto": pd.Series(["x" * 4000] * 10, dtype="str")})
    record = sample_record(df=df, row_headers=["a", "b"])
    budget = metadata_size(build_metadata(record, df.head(0))) + 9000

    metadata = build_metadata(record, df, max_bytes=budget)

    assert metadata_size(metadata) <= budget
    assert metadata["row_headers"] == ["a", "b"]
    assert len(metadata["preview_csv"].splitlines()) == 1 + 2  # encabezado + 2 filas enteras


# --- embeddings en lotes (NFR-007) ---------------------------------------------------------------


def test_embeddings_are_requested_in_batches_of_at_most_64() -> None:
    """Given 130 textos, when se calculan los embeddings, then hay 3 llamadas (64 + 64 + 2) y un
    vector por texto en orden."""
    embeddings = FakeEmbeddings(dimension=8)
    texts = [f"tabla {i}" for i in range(130)]

    vectors = embed_texts(embeddings, texts, expected_dim=8)

    assert [len(batch) for batch in embeddings.calls] == [64, 64, 2]
    assert vectors[129] == embeddings.vector("tabla 129")
    assert len(vectors) == 130


def test_embeddings_with_wrong_dimension_are_rejected() -> None:
    """Given embeddings de dimensión 8 y EMBEDDING_DIM=1536, then ValueError con ambas."""
    with pytest.raises(ValueError, match="1536"):
        embed_texts(FakeEmbeddings(dimension=8), ["a"], expected_dim=1536)


# --- upsert, query y borrado (VC-025, VC-028, FR-029) --------------------------------------------


def test_vc025_upsert_uses_table_id_as_vector_id_with_spec_metadata() -> None:
    """Given 3 tablas y sus embeddings, when se indexan, then cada vector tiene id table_id, la
    metadata de §7.4 y embedding_text con name, description, columnas y "Fuente:"."""
    store = FakeVectorStore()
    embeddings = FakeEmbeddings(dimension=8)
    tables = items(3)
    texts = [build_embedding_text(record) for record, _ in tables]

    indexed = upsert_records(store, tables, embed_texts(embeddings, texts), texts)

    assert indexed == 3
    assert sorted(store.vectors) == sorted(record.table_id for record, _ in tables)
    for record, _ in tables:
        vector = store.vectors[record.table_id]
        assert tuple(vector.metadata) == METADATA_KEYS
        assert vector.metadata["table_id"] == record.table_id
        text = vector.metadata["embedding_text"]
        assert record.name in text and record.description in text and "Fuente:" in text
        assert all(column.name in text for column in record.columns)
    best = query(store, embeddings.embed_query(texts[1]), top_k=2)
    assert best[0].id == tables[1][0].table_id and best[0].score == pytest.approx(1.0)
    assert len(embeddings.calls) == 1


def test_reupserting_the_same_ids_does_not_duplicate() -> None:
    """Given una tabla indexada, when se vuelve a indexar con el mismo id, then el conteo no cambia."""
    store = FakeVectorStore()
    tables = items(2)
    vectors = [[1.0, 0.0], [0.0, 1.0]]
    upsert_records(store, tables, vectors)

    upsert_records(store, tables, vectors)

    assert count_vectors(store) == 2


def test_partial_upsert_failure_reports_how_many_tables_were_indexed() -> None:
    """Given un Pinecone que falla en el upsert de la 2.ª de 3 tablas, then IndexingError dice
    "1 de 3 tablas indexadas"; reindexar con Pinecone sano deja 3 vectores (FR-029)."""
    store = FakeVectorStore(fail_on_upsert_call=2)
    tables = items(3)
    vectors = [[1.0, 0.0], [0.0, 1.0], [1.0, 1.0]]

    with pytest.raises(IndexingError) as error:
        upsert_records(store, tables, vectors)

    assert error.value.indexed == 1 and error.value.total == 3
    assert "1 de 3 tablas indexadas" in str(error.value)
    assert store.count() == 1
    store.fail_on_upsert_call = None
    upsert_records(store, tables, vectors)
    assert store.count() == 3


def test_vc028_delete_stale_removes_ids_of_the_document_not_in_the_new_ingestion() -> None:
    """Given una ingesta previa con ids {a,b,c} y una nueva con {a,b}, when se borran los
    obsoletos, then c se borra del índice y las tablas de otro documento quedan."""
    store = FakeVectorStore()
    previous = items(3)
    other = items(1, doc_hash=OTHER_DOC_HASH)
    upsert_records(store, previous + other, [[1.0, 0.0]] * 4)
    a, b, c = (record.table_id for record, _ in previous)

    deleted = delete_stale(store, DOC_HASH, keep_ids=[a, b])

    assert deleted == [c]
    assert sorted(store.vectors) == sorted([a, b, other[0][0].table_id])
    assert delete_stale(store, DOC_HASH, keep_ids=[a, b]) == []
    assert len(store.delete_calls) == 1


def test_count_vectors_waits_until_the_expected_count() -> None:
    """Given un índice cuyo conteo tarda en actualizarse, when se espera el conteo esperado,
    then se reintenta hasta verlo (sin dormir de verdad)."""

    class LaggingStore(FakeVectorStore):
        def __init__(self) -> None:
            super().__init__()
            self.reads = 0

        def count(self) -> int:
            self.reads += 1
            return 0 if self.reads < 3 else 5

    store = LaggingStore()
    sleeps: list[float] = []

    assert count_vectors(store, expected=5, timeout_s=30, poll_s=2, sleep=sleeps.append) == 5
    assert store.reads == 3 and sleeps == [2, 2]


def test_count_vectors_gives_up_after_the_timeout() -> None:
    """Given un conteo que nunca llega, when vence el tiempo, then se devuelve el último leído."""
    assert count_vectors(FakeVectorStore(), expected=1, timeout_s=0, sleep=lambda _: None) == 0


# --- adaptador de Pinecone -----------------------------------------------------------------------


def test_pinecone_adapter_maps_sdk_calls() -> None:
    """Given un índice con la forma del SDK, then el adaptador hace upsert, lista por prefijo
    (paginado), consulta con metadata, cuenta y borra."""
    index = FakePineconeIndex()
    store = PineconeVectorStore(index, name="x")
    store.upsert(
        [
            VectorRecord(id=f"{DOC_HASH[:16]}-p00{i}-t0", values=[1.0, float(i)], metadata={"i": str(i)})
            for i in range(1, 4)
        ]
        + [VectorRecord(id=f"{OTHER_DOC_HASH[:16]}-p001-t0", values=[0.0, 1.0], metadata={})]
    )

    assert store.count() == 4
    assert store.list_ids(DOC_HASH[:16] + "-") == [f"{DOC_HASH[:16]}-p00{i}-t0" for i in (1, 2, 3)]
    matches = store.query([1.0, 1.0], top_k=1)
    assert matches[0].id == f"{DOC_HASH[:16]}-p001-t0" and matches[0].metadata == {"i": "1"}
    store.delete([f"{DOC_HASH[:16]}-p001-t0"])
    assert store.count() == 3
    store.upsert([])
    assert index.upsert_calls == 1
