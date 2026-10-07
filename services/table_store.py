"""Almacén local de tablas en ``TABLES_DIR`` (spec §7.2, FR-023, BR-010).

Por tabla hay dos archivos con el mismo nombre base, que es el ``table_id`` (y también el id
del vector en Pinecone):

- ``<table_id>.parquet`` — datos normalizados (``pyarrow``, sin índice). Fuente de verdad:
  preserva los dtypes (``float64``, ``Int64``, ``str``, ``NaN``).
- ``<table_id>.json`` — ``TableRecord`` (catálogo de la pestaña Explorar).

Los archivos se escriben de forma atómica (archivo temporal + ``os.replace``): un corte a
mitad de la escritura no deja un Parquet o JSON a medias.
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
from collections.abc import Callable, Iterable
from pathlib import Path

import pandas as pd
from pydantic import ValidationError

from agents.ingestion.schemas import TableRecord

logger = logging.getLogger(__name__)

# BR-010: f"{doc_hash[:16]}-p{primera_pagina:03d}-t{indice_en_pagina}"
DOC_PREFIX_LEN = 16
TABLE_ID_RE = re.compile(r"^[0-9a-f]{16}-p\d{3,}-t\d+$")
_DOC_PREFIX_RE = re.compile(r"^[0-9a-f]{16,64}$")


def make_table_id(doc_hash: str, first_page: int, index_on_page: int) -> str:
    """``table_id`` determinístico de BR-010 (página 1-based, índice 0-based).

    >>> make_table_id("ab" * 32, 3, 0)
    'abababababababab-p003-t0'
    """
    doc_hash = doc_hash.lower()
    if not _DOC_PREFIX_RE.fullmatch(doc_hash):
        raise ValueError(f"doc_hash inválido (se esperaba hex de ≥ 16 caracteres): {doc_hash!r}")
    if first_page < 1:
        raise ValueError(f"first_page es 1-based: {first_page}")
    if index_on_page < 0:
        raise ValueError(f"index_on_page es 0-based: {index_on_page}")
    return f"{doc_hash[:DOC_PREFIX_LEN]}-p{first_page:03d}-t{index_on_page}"


def doc_prefix(doc_hash: str) -> str:
    """Prefijo de los ``table_id`` de un documento: ``doc_hash[:16] + "-"``."""
    doc_hash = doc_hash.lower()
    if not _DOC_PREFIX_RE.fullmatch(doc_hash):
        raise ValueError(f"doc_hash inválido (se esperaba hex de ≥ 16 caracteres): {doc_hash!r}")
    return doc_hash[:DOC_PREFIX_LEN] + "-"


def _check_table_id(table_id: str) -> str:
    if not TABLE_ID_RE.fullmatch(table_id):
        raise ValueError(f"table_id inválido: {table_id!r}")
    return table_id


def _is_parquet_safe_dtype(dtype: object) -> bool:
    return (
        isinstance(dtype, pd.StringDtype)
        or pd.api.types.is_numeric_dtype(dtype)  # float64, Int64, int64, bool
        or pd.api.types.is_datetime64_any_dtype(dtype)
    )


def ensure_parquet_safe(df: pd.DataFrame) -> pd.DataFrame:
    """Devuelve ``df`` listo para Parquet: nombres ``str`` únicos y un tipo homogéneo por columna.

    ``normalize_table`` ya deja cada columna como ``float64``/``Int64``/``str``; si llega una
    columna ``object`` (p. ej. números y textos mezclados, que ``pyarrow`` no puede escribir)
    se convierte a texto (``str``) conservando los faltantes como ``NaN``. Nombres de columna
    que no son ``str`` o repetidos lanzan ``ValueError`` (cambiarlos rompería la coherencia con
    los metadatos).
    """
    names = list(df.columns)
    bad = [name for name in names if not isinstance(name, str)]
    if bad:
        raise ValueError(f"nombres de columna no str (Parquet exige str): {bad!r}")
    if len(set(names)) != len(names):
        raise ValueError(f"nombres de columna repetidos: {names!r}")
    out = df.reset_index(drop=True)
    for name in names:
        if not _is_parquet_safe_dtype(out[name].dtype):
            out[name] = out[name].astype("str")
    unsafe = {name: str(out[name].dtype) for name in names if not _is_parquet_safe_dtype(out[name].dtype)}
    assert not unsafe, f"columnas no aptas para Parquet: {unsafe}"
    return out


def _write_atomic(path: Path, write: Callable[[Path], object]) -> None:
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        write(tmp)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


class TableStore:
    """Parquet + JSON por tabla en ``tables_dir`` (``Settings.tables_dir``)."""

    def __init__(self, tables_dir: str | Path) -> None:
        self.tables_dir = Path(tables_dir)

    def parquet_path(self, table_id: str) -> Path:
        return self.tables_dir / f"{_check_table_id(table_id)}.parquet"

    def json_path(self, table_id: str) -> Path:
        return self.tables_dir / f"{_check_table_id(table_id)}.json"

    def save_table(self, record: TableRecord, df: pd.DataFrame) -> TableRecord:
        """Escribe ``<id>.parquet`` y ``<id>.json`` (reemplazando los anteriores) y devuelve el
        registro guardado, con ``n_rows``, ``n_cols``, ``dtypes`` y ``data_path`` tomados del
        DataFrame efectivamente escrito.

        Lanza ``ValueError`` si el ``table_id`` es inválido o si ``record.columns`` no nombra
        exactamente, y en orden, las columnas de ``df``.
        """
        table_id = _check_table_id(record.table_id)
        safe = ensure_parquet_safe(df)
        column_names = [column.name for column in record.columns]
        if column_names != list(safe.columns):
            raise ValueError(
                f"{table_id}: las columnas del registro no coinciden con las del DataFrame "
                f"({column_names!r} != {list(safe.columns)!r})"
            )
        self.tables_dir.mkdir(parents=True, exist_ok=True)
        parquet = self.parquet_path(table_id)
        saved = record.model_copy(
            update={
                "n_rows": len(safe),
                "n_cols": len(safe.columns),
                "dtypes": {name: str(safe[name].dtype) for name in safe.columns},
                "data_path": parquet.as_posix(),
            }
        )
        _write_atomic(parquet, lambda tmp: safe.to_parquet(tmp, engine="pyarrow", index=False))
        _write_atomic(
            self.json_path(table_id),
            lambda tmp: tmp.write_text(saved.model_dump_json(indent=2), encoding="utf-8"),
        )
        return saved

    def load_table(self, table_id: str) -> pd.DataFrame:
        """Lee el Parquet de la tabla (``FileNotFoundError`` si no existe)."""
        return pd.read_parquet(self.parquet_path(table_id), engine="pyarrow")

    def load_record(self, table_id: str) -> TableRecord:
        """Lee el ``TableRecord`` de la tabla (``FileNotFoundError`` si no existe)."""
        return TableRecord.model_validate_json(self.json_path(table_id).read_text(encoding="utf-8"))

    def table_exists(self, table_id: str) -> bool:
        """``True`` si existe el Parquet de la tabla (FR-042: sin Parquet no hay datos locales)."""
        return self.parquet_path(table_id).is_file()

    def list_records(self) -> list[TableRecord]:
        """Todos los ``TableRecord`` de ``*.json``, ordenados por ``table_id``.

        Un JSON ilegible o que no es un ``TableRecord`` se saltea (con log), no rompe el catálogo.
        """
        if not self.tables_dir.is_dir():
            return []
        records: list[TableRecord] = []
        for path in sorted(self.tables_dir.glob("*.json")):
            if not TABLE_ID_RE.fullmatch(path.stem):
                continue
            try:
                records.append(TableRecord.model_validate_json(path.read_text(encoding="utf-8")))
            except (OSError, ValidationError, ValueError) as exc:
                logger.warning("registro de tabla ilegible, se ignora: %s (%s)", path.name, exc)
        return records

    def list_table_ids(self, doc_hash_prefix: str = "") -> list[str]:
        """Ids con archivos (``.parquet`` o ``.json``) cuyo nombre empieza con el prefijo dado."""
        if not self.tables_dir.is_dir():
            return []
        ids = {
            path.stem
            for path in self.tables_dir.iterdir()
            if path.suffix in {".parquet", ".json"}
            and TABLE_ID_RE.fullmatch(path.stem)
            and path.stem.startswith(doc_hash_prefix)
        }
        return sorted(ids)

    def delete_tables(self, table_ids: Iterable[str]) -> list[str]:
        """Borra el Parquet y el JSON de cada id. Devuelve los ids que tenían algún archivo."""
        deleted: list[str] = []
        for table_id in table_ids:
            removed = False
            for path in (self.parquet_path(table_id), self.json_path(table_id)):
                if path.exists():
                    path.unlink()
                    removed = True
            if removed:
                deleted.append(table_id)
        return deleted

    def delete_document(self, doc_hash_prefix: str) -> list[str]:
        """Borra todas las tablas de un documento (acepta ``doc_hash`` completo o sus 16 primeros
        caracteres). Devuelve los ids borrados."""
        return self.delete_tables(self.list_table_ids(doc_prefix(doc_hash_prefix)))

    def delete_stale(self, doc_hash: str, keep_ids: Iterable[str]) -> list[str]:
        """BR-010: borra las tablas locales del documento que no están en ``keep_ids``."""
        keep = set(keep_ids)
        stale = [tid for tid in self.list_table_ids(doc_prefix(doc_hash)) if tid not in keep]
        return self.delete_tables(stale)
