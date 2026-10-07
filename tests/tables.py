"""Tablas y registros de ejemplo para tests (sin red ni PDFs)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from agents.ingestion.schemas import ColumnMeta, TableRecord, TableSource
from services.table_store import make_table_id

DOC_HASH = "0123456789abcdef" + "0" * 48
OTHER_DOC_HASH = "fedcba9876543210" + "1" * 48


def sample_df() -> pd.DataFrame:
    """DataFrame normalizado típico: texto con faltante, ``float64`` con ``NaN`` y años ``Int64``."""
    return pd.DataFrame(
        {
            "Región": pd.Series(["GBA", "Noreste", None, "Patagonia"], dtype="str"),
            "Tasa de desocupación - 2T2026": pd.Series([8.1, 4.5, np.nan, 5.2], dtype="float64"),
            "Año": pd.Series([2024, 2025, None, 2026], dtype="Int64"),
        }
    )


def sample_record(
    doc_hash: str = DOC_HASH,
    first_page: int = 3,
    index_on_page: int = 0,
    df: pd.DataFrame | None = None,
    **overrides: object,
) -> TableRecord:
    """``TableRecord`` coherente con ``df`` (por defecto ``sample_df()``)."""
    df = sample_df() if df is None else df
    fields: dict[str, object] = {
        "table_id": make_table_id(doc_hash, first_page, index_on_page),
        "doc_hash": doc_hash,
        "name": "Desocupación por región",
        "description": "Tasa de desocupación por región. Fuente: EPH.",
        "columns": [
            ColumnMeta(name=str(c), description=f"Descripción de {c}", dtype="text", unit=None)
            for c in df.columns
        ],
        "row_headers": ["GBA", "Noreste", "Patagonia"],
        "source": TableSource(file_name="informe.pdf", pages=[first_page, first_page + 1]),
        "section": "1. Resultados",
    }
    fields.update(overrides)
    return TableRecord.model_validate(fields)
