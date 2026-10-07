"""Modelos Pydantic de la Ingesta (spec §7.3).

``TableEnrichment`` es lo que devuelve el LLM; ``TableRecord`` es el registro que se guarda
como ``<table_id>.json`` en ``TABLES_DIR`` y se indexa en Pinecone. ``table_id`` y ``source``
los asigna el código, nunca el LLM.

``IngestionWarning`` vive en ``services.pdf_extract`` (D-016) y se reexporta acá.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from services.pdf_extract import IngestionWarning

__all__ = [
    "ColumnDtype",
    "ColumnMeta",
    "IngestionWarning",
    "MetadataQuality",
    "TableEnrichment",
    "TableRecord",
    "TableSource",
]

ColumnDtype = Literal["number", "integer", "percent", "currency", "year", "date", "text"]
MetadataQuality = Literal["ok", "degraded"]


class ColumnMeta(BaseModel):
    """Metadatos de una columna. ``name`` es el nombre **exacto** de la columna del DataFrame."""

    name: str = Field(description="Nombre exacto de la columna, copiado tal cual.")
    description: str = Field(description="Qué mide la columna, en español.")
    dtype: ColumnDtype = Field(description="Tipo semántico de la columna.")
    unit: str | None = Field(default=None, description="Unidad (p. ej. '%', 'miles de personas').")


class TableEnrichment(BaseModel):
    """Salida estructurada del LLM en el nodo ``enrich`` (FR-019)."""

    name: str = Field(description="Nombre corto y descriptivo de la tabla (3-80 caracteres).")
    description: str = Field(description="Descripción de 2 a 4 oraciones (máx. 600 caracteres).")
    columns: list[ColumnMeta] = Field(description="Una entrada por columna, en el mismo orden.")
    row_headers: list[str] = Field(description="Rótulos de fila tal como aparecen en la tabla.")


class TableSource(BaseModel):
    """Origen de la tabla: archivo subido y páginas (1-based) que abarca."""

    file_name: str
    pages: list[int]


class TableRecord(BaseModel):
    """Registro de una tabla indexada (``<table_id>.json``).

    ``n_rows``, ``n_cols``, ``dtypes`` y ``data_path`` los completa
    ``TableStore.save_table`` a partir del DataFrame que guarda; ``dtypes`` son los dtypes de
    pandas (``"float64"``, ``"Int64"``, ``"str"``) y son informativos (el Parquet es la fuente
    de verdad, spec §7.2).
    """

    table_id: str
    doc_hash: str
    name: str
    description: str
    columns: list[ColumnMeta]
    row_headers: list[str] = Field(default_factory=list)
    source: TableSource
    section: str | None = None
    n_rows: int = 0
    n_cols: int = 0
    dtypes: dict[str, str] = Field(default_factory=dict)
    metadata_quality: MetadataQuality = "ok"
    validation_attempts: int = 1
    data_path: str = ""
