"""Índice vectorial de tablas en Pinecone (spec FR-003, FR-004, FR-023, FR-024, BR-009, BR-010,
NFR-005, NFR-007, §7.4).

Una tabla = un vector. El id del vector es el ``table_id`` (== nombre base de sus archivos en
``TABLES_DIR``); namespace por defecto.

- ``VectorStore`` (``Protocol``) define las primitivas que usa el resto del código: ``upsert``,
  ``list_ids(prefix)``, ``delete``, ``query`` y ``count``. ``PineconeVectorStore`` las
  implementa sobre el SDK ``pinecone``; los tests usan ``tests.fakes.FakeVectorStore``.
- ``ensure_index`` crea el índice serverless (coseno, ``EMBEDDING_DIM``) si no existe y
  rechaza uno existente con otra dimensión, antes de cualquier escritura.
- El resto son funciones sobre esas primitivas: texto de embedding (BR-009), metadata con
  tope de tamaño (NFR-005), embeddings en lotes (NFR-007), upsert tabla por tabla con conteo
  de avance (FR-029), borrado de obsoletos por prefijo (BR-010) y conteo con espera por la
  consistencia eventual de Pinecone serverless.
"""

from __future__ import annotations

import json
import math
import time
import warnings
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import pandas as pd
from langchain_core.embeddings import Embeddings

from agents.ingestion.schemas import ColumnMeta, TableRecord
from services.config import Settings
from services.table_store import doc_prefix

# NFR-005: tope de la metadata serializada por registro (margen bajo el límite de 40 KB).
MAX_METADATA_BYTES = 35_000
# NFR-007: embeddings en lotes de hasta 64 textos (≤ 1 llamada cada 64 tablas).
EMBEDDING_BATCH_SIZE = 64
# BR-009: cantidad de rótulos de fila que entran en el texto de embedding.
EMBEDDING_ROW_HEADERS = 50
# §7.4: filas de la vista previa en CSV.
PREVIEW_ROWS = 5
DEFAULT_NAMESPACE = ""
# Espera por la consistencia eventual de los conteos (VC-027).
COUNT_TIMEOUT_S = 30.0
COUNT_POLL_S = 2.0
INDEX_READY_TIMEOUT_S = 300

# §7.4: claves de la metadata de cada vector.
METADATA_KEYS: tuple[str, ...] = (
    "table_id",
    "doc_hash",
    "name",
    "description",
    "columns_json",
    "row_headers",
    "file_name",
    "pages",
    "section",
    "n_rows",
    "n_cols",
    "preview_csv",
    "metadata_quality",
    "embedding_text",
)


# --- Errores -------------------------------------------------------------------------------------


class IndexDimensionMismatch(Exception):
    """FR-004: el índice existe con una dimensión distinta de ``EMBEDDING_DIM``."""

    def __init__(self, index_name: str, index_dim: int | None, expected_dim: int) -> None:
        self.index_name = index_name
        self.index_dim = index_dim
        self.expected_dim = expected_dim
        super().__init__(
            f"El índice de Pinecone '{index_name}' tiene dimensión {index_dim}, pero "
            f"EMBEDDING_DIM es {expected_dim}. No se escribe en el índice: usá otro "
            f"PINECONE_INDEX_NAME o un modelo de embeddings de dimensión {index_dim}."
        )


class MetadataTooLarge(ValueError):
    """La metadata no entra en ``MAX_METADATA_BYTES`` ni truncando lo truncable (NFR-005)."""


class IndexingError(Exception):
    """FR-029: falla a mitad del upsert. ``indexed`` = tablas ya indexadas de ``total``."""

    def __init__(self, indexed: int, total: int, cause: BaseException) -> None:
        self.indexed = indexed
        self.total = total
        self.cause = cause
        super().__init__(
            f"{indexed} de {total} tablas indexadas; falló Pinecone: {cause}. "
            "Reingerir el mismo PDF completa la ingesta sin duplicados."
        )


# --- Primitivas ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class VectorRecord:
    """Un vector a escribir: id (``table_id``), valores y metadata (§7.4)."""

    id: str
    values: list[float]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Match:
    """Resultado de una consulta: id, score (coseno) y metadata tal como la guarda el índice."""

    id: str
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class VectorStore(Protocol):
    """Primitivas de un índice vectorial (Pinecone real o fake en memoria)."""

    def upsert(self, vectors: Sequence[VectorRecord]) -> None: ...

    def list_ids(self, prefix: str) -> list[str]: ...

    def delete(self, ids: Sequence[str]) -> None: ...

    def query(self, vector: Sequence[float], top_k: int) -> list[Match]: ...

    def count(self) -> int: ...


class PineconeVectorStore:
    """``VectorStore`` sobre un índice del SDK ``pinecone`` (namespace por defecto)."""

    DELETE_BATCH = 1000

    def __init__(self, index: Any, namespace: str = DEFAULT_NAMESPACE, name: str = "") -> None:
        self.index = index
        self.namespace = namespace
        self.name = name

    def upsert(self, vectors: Sequence[VectorRecord]) -> None:
        if not vectors:
            return
        payload = [{"id": v.id, "values": list(v.values), "metadata": dict(v.metadata)} for v in vectors]
        self.index.upsert(vectors=payload, namespace=self.namespace, show_progress=False)

    def list_ids(self, prefix: str) -> list[str]:
        ids: list[str] = []
        for page in self.index.list(prefix=prefix, namespace=self.namespace):
            for item in getattr(page, "vectors", page):
                ids.append(item if isinstance(item, str) else item.id)
        return sorted(ids)

    def delete(self, ids: Sequence[str]) -> None:
        ids = list(ids)
        for start in range(0, len(ids), self.DELETE_BATCH):
            self.index.delete(ids=ids[start : start + self.DELETE_BATCH], namespace=self.namespace)

    def query(self, vector: Sequence[float], top_k: int) -> list[Match]:
        response = self.index.query(
            vector=list(vector), top_k=top_k, include_metadata=True, namespace=self.namespace
        )
        return [
            Match(id=m.id, score=float(m.score), metadata=dict(m.metadata or {})) for m in response.matches
        ]

    def count(self) -> int:
        return int(self.index.describe_index_stats().total_vector_count)


def _index_dimension(description: Any) -> int | None:
    with warnings.catch_warnings():  # `dimension` es una propiedad deprecada en el SDK 10
        warnings.simplefilter("ignore", DeprecationWarning)
        dimension = getattr(description, "dimension", None)
    return int(dimension) if dimension is not None else None


def make_pinecone_client(settings: Settings) -> Any:
    """Cliente del SDK ``pinecone`` con ``PINECONE_API_KEY`` (plano de control + datos)."""
    from pinecone import Pinecone

    return Pinecone(api_key=settings.pinecone_api_key)


def ensure_index(
    settings: Settings,
    client: Any | None = None,
    *,
    index_name: str | None = None,
    ready_timeout_s: int = INDEX_READY_TIMEOUT_S,
) -> PineconeVectorStore:
    """Devuelve el ``VectorStore`` del índice ``PINECONE_INDEX_NAME`` (o ``index_name``).

    FR-003: si el índice no existe, lo crea serverless (``PINECONE_CLOUD``/``PINECONE_REGION``)
    con dimensión ``EMBEDDING_DIM`` y métrica coseno, y espera a que esté listo.
    FR-004: si existe con otra dimensión, lanza ``IndexDimensionMismatch`` sin escribir nada.
    ``client`` es un ``pinecone.Pinecone`` (o un fake con ``has_index``, ``describe_index``,
    ``create_index`` e ``Index``); por defecto se crea con ``PINECONE_API_KEY``.
    """
    from pinecone import ServerlessSpec

    client = client if client is not None else make_pinecone_client(settings)
    name = index_name or settings.pinecone_index_name
    if client.has_index(name):
        description = client.describe_index(name)
        dimension = _index_dimension(description)
        if dimension != settings.embedding_dim:
            raise IndexDimensionMismatch(name, dimension, settings.embedding_dim)
    else:
        description = client.create_index(
            name=name,
            dimension=settings.embedding_dim,
            metric="cosine",
            spec=ServerlessSpec(cloud=settings.pinecone_cloud, region=settings.pinecone_region),
            timeout=ready_timeout_s,
        )
    host = getattr(description, "host", None)
    index = client.Index(name=name, host=host) if host else client.Index(name=name)
    return PineconeVectorStore(index, name=name)


def open_index(
    settings: Settings, client: Any | None = None, *, index_name: str | None = None
) -> PineconeVectorStore | None:
    """Versión de solo lectura de ``ensure_index`` para el Analista (que no escribe en Pinecone):
    devuelve ``None`` si el índice no existe (no lo crea) y lanza ``IndexDimensionMismatch`` si
    existe con otra dimensión (FR-004)."""
    client = client if client is not None else make_pinecone_client(settings)
    name = index_name or settings.pinecone_index_name
    if not client.has_index(name):
        return None
    description = client.describe_index(name)
    dimension = _index_dimension(description)
    if dimension != settings.embedding_dim:
        raise IndexDimensionMismatch(name, dimension, settings.embedding_dim)
    host = getattr(description, "host", None)
    index = client.Index(name=name, host=host) if host else client.Index(name=name)
    return PineconeVectorStore(index, name=name)


# --- Texto de embedding y metadata ---------------------------------------------------------------


def format_pages(pages: Sequence[int]) -> str:
    """``[5]`` → ``"pág. 5"``; ``[3, 4]`` → ``"págs. 3, 4"``."""
    label = "pág." if len(pages) == 1 else "págs."
    return f"{label} {', '.join(str(p) for p in pages)}"


def _column_text(column: ColumnMeta) -> str:
    text = column.name
    if column.unit:
        text += f" ({column.unit})"
    if column.description.strip():
        text += f": {column.description.strip()}"
    return text


def build_embedding_text(record: TableRecord) -> str:
    """BR-009: name, description, columnas ``nombre (unidad): descripción``, primeros 50
    rótulos de fila, fuente y sección. Sin sección (``None``) se omite esa línea."""
    lines = [
        record.name,
        record.description,
        "Columnas: " + "; ".join(_column_text(c) for c in record.columns),
        "Filas: " + "; ".join(h.strip() for h in record.row_headers[:EMBEDDING_ROW_HEADERS]),
        f"Fuente: {record.source.file_name}, {format_pages(record.source.pages)}",
    ]
    if record.section:
        lines.append(f"Sección: {record.section}")
    return "\n".join(lines)


def metadata_size(metadata: Mapping[str, Any]) -> int:
    """Bytes de la metadata serializada como JSON UTF-8 (medida de NFR-005)."""
    return len(json.dumps(metadata, ensure_ascii=False).encode("utf-8"))


def preview_csv(df: pd.DataFrame, rows: int = PREVIEW_ROWS) -> str:
    """Primeras ``rows`` filas como CSV (con encabezado, sin índice; ``NaN`` → vacío)."""
    return df.head(rows).to_csv(index=False, lineterminator="\n")


def _fit_text(metadata: dict[str, Any], key: str, max_bytes: int) -> None:
    """Recorta ``metadata[key]`` (texto) al prefijo más largo con el que todo entra."""
    text = metadata[key]
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        metadata[key] = text[:mid]
        if metadata_size(metadata) <= max_bytes:
            lo = mid
        else:
            hi = mid - 1
    metadata[key] = text[:lo]


def _fit_list(metadata: dict[str, Any], key: str, max_bytes: int) -> None:
    """Recorta ``metadata[key]`` (lista) a los primeros elementos con los que todo entra."""
    items = metadata[key]
    lo, hi = 0, len(items)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        metadata[key] = items[:mid]
        if metadata_size(metadata) <= max_bytes:
            lo = mid
        else:
            hi = mid - 1
    metadata[key] = items[:lo]


def build_metadata(
    record: TableRecord,
    df: pd.DataFrame,
    embedding_text: str | None = None,
    max_bytes: int = MAX_METADATA_BYTES,
) -> dict[str, Any]:
    """Metadata de §7.4 para el vector de ``record``, de a lo sumo ``max_bytes`` serializada.

    Tipos aptos para Pinecone (texto, número, booleano, lista de textos; nunca ``null``):
    ``pages`` se guarda como lista de textos y ``section`` ausente como ``""`` (D-018);
    ``decode_metadata`` lo revierte.

    NFR-005: si no entra, se trunca primero ``preview_csv`` (filas enteras, después texto) y
    luego ``row_headers`` (desde el final). Si aun así no entra, se recorta la copia de
    ``embedding_text`` de la metadata (el vector se calcula con el texto completo) y, como
    último recurso, ``MetadataTooLarge``.
    """
    text = embedding_text if embedding_text is not None else build_embedding_text(record)
    metadata: dict[str, Any] = {
        "table_id": record.table_id,
        "doc_hash": record.doc_hash,
        "name": record.name,
        "description": record.description,
        "columns_json": json.dumps([c.model_dump() for c in record.columns], ensure_ascii=False),
        "row_headers": [str(h) for h in record.row_headers],
        "file_name": record.source.file_name,
        "pages": [str(p) for p in record.source.pages],
        "section": record.section or "",
        "n_rows": int(record.n_rows or len(df)),
        "n_cols": int(record.n_cols or len(df.columns)),
        "preview_csv": preview_csv(df),
        "metadata_quality": record.metadata_quality,
        "embedding_text": text,
    }
    if metadata_size(metadata) <= max_bytes:
        return metadata
    for rows in range(PREVIEW_ROWS - 1, -1, -1):
        metadata["preview_csv"] = preview_csv(df, rows)
        if metadata_size(metadata) <= max_bytes:
            return metadata
    _fit_text(metadata, "preview_csv", max_bytes)
    if metadata_size(metadata) <= max_bytes:
        return metadata
    _fit_list(metadata, "row_headers", max_bytes)
    if metadata_size(metadata) <= max_bytes:
        return metadata
    _fit_text(metadata, "embedding_text", max_bytes)
    if metadata_size(metadata) > max_bytes:
        raise MetadataTooLarge(
            f"{record.table_id}: la metadata ocupa {metadata_size(metadata)} bytes "
            f"(> {max_bytes}) aun truncando preview_csv, row_headers y embedding_text"
        )
    return metadata


def decode_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Inversa práctica de ``build_metadata``: ``columns`` (``list[ColumnMeta]``), ``pages``
    (``list[int]``), ``n_rows``/``n_cols`` (``int``) y ``section`` (``None`` si vacía)."""
    out = dict(metadata)
    out["columns"] = [ColumnMeta.model_validate(c) for c in json.loads(metadata.get("columns_json", "[]"))]
    out["pages"] = [int(float(p)) for p in metadata.get("pages", [])]
    out["n_rows"] = int(metadata.get("n_rows", 0))
    out["n_cols"] = int(metadata.get("n_cols", 0))
    out["row_headers"] = list(metadata.get("row_headers", []))
    out["section"] = metadata.get("section") or None
    return out


# --- Embeddings, upsert, borrado y conteo --------------------------------------------------------


def embed_texts(
    embeddings: Embeddings,
    texts: Sequence[str],
    *,
    batch_size: int = EMBEDDING_BATCH_SIZE,
    expected_dim: int | None = None,
) -> list[list[float]]:
    """Embeddings de ``texts`` con una llamada a ``embed_documents`` por lote de ≤ ``batch_size``
    (NFR-007). Con ``expected_dim`` verifica la dimensión de cada vector (``ValueError``)."""
    if batch_size < 1:
        raise ValueError("batch_size debe ser ≥ 1")
    vectors: list[list[float]] = []
    for start in range(0, len(texts), batch_size):
        batch = list(texts[start : start + batch_size])
        result = embeddings.embed_documents(batch)
        if len(result) != len(batch):
            raise ValueError(f"se pidieron {len(batch)} embeddings y llegaron {len(result)}")
        vectors.extend([float(x) for x in vector] for vector in result)
    if expected_dim is not None:
        wrong = {len(v) for v in vectors if len(v) != expected_dim}
        if wrong:
            raise ValueError(f"embeddings de dimensión {sorted(wrong)}; EMBEDDING_DIM es {expected_dim}")
    bad = [i for i, v in enumerate(vectors) if not all(math.isfinite(x) for x in v)]
    if bad:
        raise ValueError(f"embeddings con valores no finitos en las posiciones {bad}")
    return vectors


def build_vector_records(
    items: Sequence[tuple[TableRecord, pd.DataFrame]],
    vectors: Sequence[Sequence[float]],
    embedding_texts: Sequence[str] | None = None,
) -> list[VectorRecord]:
    """Arma un ``VectorRecord`` por tabla (id = ``table_id``, metadata de ``build_metadata``)."""
    if len(items) != len(vectors):
        raise ValueError(f"{len(items)} tablas y {len(vectors)} vectores")
    texts = list(embedding_texts) if embedding_texts is not None else [None] * len(items)
    return [
        VectorRecord(id=record.table_id, values=list(vector), metadata=build_metadata(record, df, text))
        for (record, df), vector, text in zip(items, vectors, texts, strict=True)
    ]


def upsert_records(
    store: VectorStore,
    items: Sequence[tuple[TableRecord, pd.DataFrame]],
    vectors: Sequence[Sequence[float]],
    embedding_texts: Sequence[str] | None = None,
) -> int:
    """Upsert de una tabla por llamada, en orden; devuelve cuántas se indexaron.

    Si una llamada falla, lanza ``IndexingError(indexed, total, cause)`` (FR-029): las
    anteriores quedan indexadas y reingerir con los mismos ``table_id`` no duplica (BR-010).
    """
    vector_records = build_vector_records(items, vectors, embedding_texts)
    indexed = 0
    for vector_record in vector_records:
        try:
            store.upsert([vector_record])
        except Exception as exc:
            raise IndexingError(indexed, len(vector_records), exc) from exc
        indexed += 1
    return indexed


def delete_stale(store: VectorStore, doc_hash: str, keep_ids: Sequence[str]) -> list[str]:
    """BR-010: lista los ids con prefijo ``doc_hash[:16] + "-"`` y borra los que no están en
    ``keep_ids``. Devuelve los ids borrados (ordenados)."""
    keep = set(keep_ids)
    stale = sorted(i for i in store.list_ids(doc_prefix(doc_hash)) if i not in keep)
    if stale:
        store.delete(stale)
    return stale


def query(store: VectorStore, vector: Sequence[float], top_k: int) -> list[Match]:
    """Las ``top_k`` tablas más parecidas al vector, de mayor a menor score."""
    return store.query(vector, top_k)


def count_vectors(
    store: VectorStore,
    *,
    expected: int | None = None,
    timeout_s: float = COUNT_TIMEOUT_S,
    poll_s: float = COUNT_POLL_S,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Cantidad de vectores del índice.

    Sin ``expected`` devuelve el conteo actual. Con ``expected``, reintenta hasta que el conteo
    sea ese o pasen ``timeout_s`` segundos (los conteos de Pinecone serverless son
    eventualmente consistentes) y devuelve el último conteo leído.
    """
    current = store.count()
    if expected is None:
        return current
    deadline = time.monotonic() + timeout_s
    while current != expected and time.monotonic() < deadline:
        sleep(poll_s)
        current = store.count()
    return current
